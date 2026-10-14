"""/t/<도구>/… 중계 — httpx.AsyncClient(연결 풀)로 요청·응답 본문을 버퍼링 없이 흘림. 브라우저가 끊으면 도구 쪽 연결도 바로 닫음.
도구로 보내는 헤더 규칙: 사용자 헤더(X-Agent-User 등)·포털 쿠키(agsid·agtheme)·로컬 주인 토큰은 버리고 포털이 정한 X-Agent-User 를 붙임.
도구 첫 화면 HTML 에만 사용법(?)·테마·저작자 표기를 끼워 넣음. 오래 걸리는 POST 는 작업 알림(jobs)에 자동 기록."""
import asyncio, http.cookiejar, re, secrets, sqlite3, time, urllib.parse

import anyio
import httpx
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from . import config, jobs, pages, tools
from .config import HOP
from .ctx import Raw, TooBig, drain, length, read_body, too_big

DROP_REQ = HOP | {"x-agent-user", "x-forwarded-user", "x-user", "cookie", "x-local-owner"}   # 로컬 주인 토큰은 도구로 넘기지 않음
AG_COOKIE = re.compile(r"(?:^|;\s*)ag(?:sid|theme)=[^;]*")
REDIRECTS = (301, 302, 303, 307, 308)
CLIENT = {"c": None}


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


def open_client():
    # 쿠키 저장소는 끔: 켜 두면 한 사람의 요청에 도구가 준 Set-Cookie 를 클라이언트가 기억했다가 다른 사람 요청에 붙인다.
    # 리디렉트는 직접 따라감(_send) — httpx 가 따라가면 Cookie 헤더를 빼서 portal.py(urllib)와 달라짐.
    jar = http.cookiejar.CookieJar(policy=_NoCookies())
    CLIENT["c"] = httpx.AsyncClient(timeout=httpx.Timeout(config.PROXY_TIMEOUT, connect=10.0), follow_redirects=False, trust_env=False, cookies=jar,
                                    limits=httpx.Limits(max_connections=None, max_keepalive_connections=64, keepalive_expiry=30))


async def close_client():
    await CLIENT["c"].aclose()


# ── 오래 걸리는 POST 자동 기록(작업 알림) ─────────────────────────────────
def _auto_write(user, t, jid, status, message):
    for _ in range(2):   # running 과 done 이 거의 같은 때 처음 들어가면 한쪽이 키 충돌 — 한 번 더
        try:
            j = jobs.upsert(user, t["dir"], status, jid, f"{t.get('name', t['dir'])} 작업", "/", message, auto=True)
            if j:
                jobs.HUB.publish("job", j, j["user"])
            return
        except sqlite3.IntegrityError:
            continue
        except Exception as e:
            print("jobs: 자동 기록 실패", type(e).__name__, e, flush=True)
            return


def _auto_job(ctx, t):
    """오래 걸리는 프록시 POST 자동 기록: AUTO_S(기본 60초) 지나도 안 끝나면 running, 끝나면 done/failed.
    파동이 펫·자기 알림(portal_jobs.py)을 쓰는 도구는 건너뜀(같은 작업이 두 번 뜨지 않게)"""
    if ctx.method != "POST" or t["dir"] == "padong-local" or t["dir"] in jobs.SELF_REPORT:
        return None
    loop, st = asyncio.get_running_loop(), {"id": secrets.token_hex(6)}
    st["h"] = loop.call_later(jobs.AUTO_S, lambda: loop.run_in_executor(None, _auto_write, ctx.agent_user, t, st["id"], "running", "오래 걸리는 요청 — 진행 중"))
    return st


def _record(ctx, t0, status, auto):
    """프록시 요청이 끝남 → 자동 작업이면 AUTO_S 를 넘겼을 때만 done/failed 로 기록(예전 /api/events 와 같은 조건)"""
    if not auto:
        return
    auto["h"].cancel()
    sec = time.time() - t0
    if sec > jobs.AUTO_S:
        t = tools.tool(ctx.raw.split("/", 3)[2])
        if t:
            msg = (f"{round(sec)}초 걸림" if sec < 90 else f"{round(sec / 60)}분 걸림") + ("" if status < 400 else f" · HTTP {status}")
            asyncio.get_running_loop().run_in_executor(None, _auto_write, ctx.agent_user, t, auto["id"], "done" if status < 400 else "failed", msg)


# ── 응답 흘리기 ─────────────────────────────────────────────────────────
class Stream(Response):
    """도구 응답을 버퍼링 없이 흘림. 브라우저가 끊으면(http.disconnect) 바로 멈추고 도구 쪽 연결도 닫음 — 조용한 SSE 도 기다리지 않음"""

    def __init__(self, status, headers, upstream, done):
        self.status_code, self.raw_headers, self.background = status, headers, None
        self.upstream, self.done = upstream, done

    async def __call__(self, scope, receive, send):
        try:
            async with anyio.create_task_group() as tg:
                async def pump():
                    try:
                        await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
                        async for chunk in self.upstream.aiter_raw():
                            await send({"type": "http.response.body", "body": chunk, "more_body": True})
                        await send({"type": "http.response.body", "body": b"", "more_body": False})
                    except OSError:   # 보내는 중 브라우저가 끊음
                        pass
                    except httpx.HTTPError as e:   # 도구가 중간에 끊음 — 응답을 마치지 않고 끝내면 uvicorn 이 연결을 닫음(받는 쪽이 '잘림'을 알게)
                        print("proxy: upstream", type(e).__name__, flush=True)
                    finally:
                        tg.cancel_scope.cancel()

                async def watch():
                    while (await receive())["type"] != "http.disconnect":
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(pump)
                tg.start_soon(watch)
        finally:
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()
                self.done()


async def _send(method, url, hd, content):
    """도구로 보내고 응답 머리까지 받음. 도구의 리디렉트는 portal.py(urllib)처럼 포털이 따라감(최대 10번, 헤더 그대로) —
    GET 은 모든 3xx, POST 는 301·302·303 만(본문 없이 GET 으로). 단 같은 도구(127.0.0.1 또는 tools.json host:포트) 안에서만 — 바깥 주소로는 안 따라감(포털이 남의 주소를 대신 부르지 않게)"""
    cl = CLIENT["c"]
    origin = urllib.parse.urlsplit(url)[:2]
    for hop in range(11):
        r = await cl.send(cl.build_request(method, url, headers=hd, content=content), stream=True)
        loc = r.headers.get("location")
        if hop < 10 and loc and r.status_code in REDIRECTS and (method == "GET" or (method == "POST" and r.status_code in (301, 302, 303))):
            nxt = urllib.parse.urljoin(url, loc.replace(" ", "%20"))
            if urllib.parse.urlsplit(nxt)[:2] == origin:
                await r.aclose()
                if method == "POST":
                    method, content = "GET", None
                    hd = [(k, v) for k, v in hd if k.lower() not in ("content-length", "content-type")]
                url = nxt
                continue
        return r
    return r


def _errname(e):
    """502 문구의 예외 이름 — portal.py(urllib)와 같은 말로: 연결 거부 = URLError, 시간 초과 = TimeoutError"""
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "URLError"
    if isinstance(e, httpx.TimeoutException):
        return "TimeoutError"
    return type(e).__name__


def upstream_headers(ctx, length_):
    """도구로 보낼 요청 헤더(규칙은 머리말)"""
    hd = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in ctx.request.headers.raw if k.decode("latin-1").lower() not in DROP_REQ]
    hd.append(("X-Agent-User", ctx.agent_user))
    ck = AG_COOKIE.sub("", ctx.cookie).strip("; ")
    if ck:
        hd.append(("Cookie", ck))
    hd.append(("Accept-Encoding", "identity"))   # 압축 안 받음(portal.py 의 urllib 와 같음) — 첫 화면 HTML 에 끼워 넣어야 하므로
    if length_ is not None:
        hd.append(("Content-Length", str(length_)))
        if not any(k.lower() == "content-type" for k, _ in hd):
            hd.append(("Content-Type", "application/x-www-form-urlencoded"))   # urllib 기본값과 같게
    return hd


async def proxy(ctx):
    t0, status = time.time(), [200]
    parts = ctx.raw.split("/", 3)
    t = tools.tool(parts[2]) if len(parts) > 2 else None
    if not t:
        return ctx.send({"error": "unknown tool"}, code=404)
    if len(parts) < 4:
        return ctx.bare(302, [("Location", f"/t/{t['dir']}/")])
    auto = _auto_job(ctx, t)
    rec = lambda: _record(ctx, t0, status[0], auto)
    # 요청 본문: Content-Length 가 있으면 그 길이로 흘려 넘김(버퍼 없음), 청크면 상한까지 모아 길이를 붙여 넘김(stdlib 도구는 청크 본문을 못 읽음)
    try:
        n = length(ctx)
    except ValueError:
        return ctx.send({"error": "Content-Length 가 틀립니다"}, code=400)
    chunked = n is None and "chunked" in (ctx.headers.get("transfer-encoding") or "").lower()
    content, clen = None, None
    if n and n > config.MAX_BODY:
        await drain(ctx.request, n); rec()
        return too_big(ctx, config.MAX_BODY)
    if n:
        async def body_iter():
            async for chunk in ctx.request.stream():
                yield chunk
        content, clen = body_iter(), n
    elif chunked:
        try:
            content = await read_body(ctx, config.MAX_BODY)
        except TooBig:
            rec(); return too_big(ctx, config.MAX_BODY)
        clen = len(content) or None
        content = content or None
    hd = upstream_headers(ctx, clen)
    host = tools.tool_host(t)
    if not host:   # tools.json 의 host 형식이 틀림 — 엉뚱한 주소로 보내지 않음(값은 응답에 안 넣음)
        print("proxy: tools.json host 형식 틀림:", t["dir"], flush=True)
        if n:
            await drain(ctx.request, n)
        rec(); return ctx.send({"error": f"{t['dir']} 주소 설정이 틀립니다 — 관리자에게 알려 주세요"}, code=502)
    try:
        r = await _send(ctx.method, f"http://{host}:{t['port']}/{parts[3]}", hd, content)
    except Exception as e:   # 도구 꺼짐·연결 실패·시간 초과 — 내부 주소·스택은 내보내지 않음
        rec()
        return ctx.send({"error": f"{t['dir']} 응답 없음 ({_errname(e)}) — 포털에서 '시작'을 누르세요"}, code=502)
    status[0] = r.status_code
    out = [(b"set-cookie", ctx.setcookie.encode())] if ctx.setcookie else []
    out += [(k, v) for k, v in r.headers.raw if k.decode("latin-1").lower() not in HOP]
    inject = ctx.method == "GET" and pages.first_html(parts[3]) and "text/html" in (r.headers.get("content-type") or "")
    if inject:   # 도구 첫 화면: 사용법(?) + 테마 + 저작자 표기를 붙임 — 이것만 다 읽어서 고침
        try:
            raw = await r.aread()
        except httpx.HTTPError as e:
            rec(); return ctx.send({"error": f"{t['dir']} 응답 없음 ({_errname(e)}) — 포털에서 '시작'을 누르세요"}, code=502)
        finally:
            await r.aclose()
        body = await run_in_threadpool(lambda: ctx.html_page(raw.decode("utf-8", "replace") + ctx.help_html(t["dir"]), "tool", t["dir"]))
        rec()
        return Raw(body, r.status_code, out)
    up_len = r.headers.get("content-length")
    if up_len:
        out.append((b"content-length", up_len.encode()))
    return Stream(r.status_code, out, r, rec)
