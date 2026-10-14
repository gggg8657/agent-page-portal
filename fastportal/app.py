"""에이전트 페이지 포털 — FastAPI(uvicorn, 비동기) 판. portal.py(stdlib ThreadingHTTPServer)와 같은 주소·응답·DB.
  ~/.local/fastportal-venv/bin/python -m fastportal            # http://0.0.0.0:8720 (PORT 로 바꿈)
  uvicorn fastportal.app:app --host 0.0.0.0 --port 8720 --no-server-header --no-proxy-headers   # 같은 앱(TLS 최소 버전은 python -m 쪽만 1.2 로 고정)

이 파일은 라우팅·조립만 — 일은 모듈이 한다:
  config 설정값·환경변수 · db 연결·표·백업 · sessions 세션·쿠키·잠금·로컬 주인 · accounts 부서·요청·관리 API · tools 도구 목록·시작·상태·GPU
  proxy /t/ 중계 · pages 화면·테마·정적 파일·React 빌드 · jobs 작업 알림 · ctx 요청 상태·응답·본문 읽기
  - 막히는 일(DB·PIN 해시·nvidia-smi)은 스레드 풀(run_in_threadpool)에서 — 이벤트 루프를 막지 않게
  - 주소 판정은 portal.py 처럼 원래 요청 경로(raw) 문자열로 한다(라우터의 디코딩·슬래시 규칙 차이로 응답이 달라지지 않게)
env: portal.py 와 같음(PORT 기본 8720, AGENT_DATA, AGENT_OWNERS, TLS_CERT·TLS_KEY, SHOW_HIDDEN) +
     PORTAL_LOCAL_OWNER(기본 token — 127.0.0.1 + 헤더 X-Local-Owner=_data/.local_owner_token 일 때만 주인; 1=예전 방식, 0=끔) TRUSTED_PROXIES(쉼표, 이 주소에서 온 접속만 X-Forwarded-For 를 믿음) +
     BACKUP=1(매일 백업 — 기본 꺼짐: 운영 포털이 돌림) PORTAL_MAX_BODY(도구로 넘기는 본문 상한 바이트, 기본 600MB → 413)
     PORTAL_MAX_JSON(포털 API JSON 본문 상한, 기본 1MB) TOOLS_ALIVE_TTL(도구 켜짐 확인 캐시 초, 기본 5)
     TOOL_BIND(포털이 띄우는 도구의 BIND, 기본 127.0.0.1 — 컨테이너에선 0.0.0.0) WEB_DIST·WEB_PREFIX(React 빌드를 같은 출처로, 기본 꺼짐·/app)
     WEB_ROOT=1·WEB_ROOT_DIST('/' 를 React 로 — 준비만, 기본 꺼짐) PORTAL_ANON_AUTO·PORTAL_ANON_AFTER_DAYS(비활성 계정 익명화 정리, 기본 꺼짐·365)
     작업 알림 센터(jobs.py): PORTAL_JOBS_AUTO_S(자동 기록 기준 초, 60) PORTAL_JOBS_URL(도구에 넘길 내부 주소, 기본 http://127.0.0.1:PORT/api/internal/jobs)
     PORTAL_JOBS_SECRET(도구 토큰을 만드는 비밀값 — 없으면 _data/.portal_jobs_secret) PORTAL_JOBS_ALLOW(내부 경로를 더 받을 IP, 쉼표) PORTAL_JOBS_KEEPALIVE(SSE 빈 줄 초, 25)
     (모두 config.py 한곳에서 읽음)
tools.json 의 "host": 그 도구는 다른 컨테이너·서버(http://<host>:<port>) — 상태 확인·프록시만, 시작·중지는 409"""
import asyncio, json, os, threading, urllib.parse
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from . import accounts, config, db, jobs, pages, proxy, sessions, tools
from .config import OWNERS, TLS
from .ctx import Ctx, TooBig, drain, length, not_implemented, parse_json, read_body, too_big

db.init_db(t.get("dept") for t in tools.tools())
with db.db() as _c:
    jobs.init(_c)   # 작업 알림 센터(jobs 표) — 예전 메모리 EVENTS 를 대체(재시작해도 남음)
jobs.load_self_report()


@asynccontextmanager
async def lifespan(_app):
    jobs.HUB.loop = asyncio.get_running_loop()
    proxy.open_client()
    if config.BACKUP:
        threading.Thread(target=db.backup_loop, daemon=True).start()
    if config.ANON_AUTO:   # 비활성 계정 익명화 정리 — 기본 꺼짐(PORTAL_ANON_AUTO=1 로 켬, 기한 PORTAL_ANON_AFTER_DAYS)
        threading.Thread(target=accounts.anon_loop, daemon=True).start()
    print(f"fastportal → {'https' if TLS else 'http'} data={config.DATA} owners={sorted(OWNERS)} max_body={config.MAX_BODY} backup={os.environ.get('BACKUP', '0')}  {pages._SIG}", flush=True)
    yield
    await proxy.close_client()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)   # /docs·/openapi.json 은 열지 않음(포털 주소 규칙 그대로)


# ── GET ─────────────────────────────────────────────────────────────────
def get_sync(ctx):
    """막히는 일(DB·파일)을 하는 GET 전부 — 스레드 풀에서. 비동기로 할 일은 문자열 표시로 돌려줌('proxy'·'jobs_stream'·'tools')"""
    p = ctx.p
    if pages.web_path(p):
        return pages.web_file(ctx, p)
    if p == "/login":
        return pages.login_page(ctx)
    if p == config.LANDING_IMG[0] or p.startswith("/theme/"):
        return pages.theme_file(ctx, p)
    if p == "/api/depts":
        with db.db() as c:
            return ctx.send({"depts": accounts.visible_depts(c)})
    if p == "/logout":
        sessions.end_session(ctx.cookie)
        return ctx.bare(302, [("Location", "/login"), ("Set-Cookie", sessions.cookie("", 0))])
    if not ctx.auth():
        return ctx.need_login()
    if p == "/api/me":
        with db.db() as c:
            return ctx.send(accounts.me(c, ctx.uid, ctx.uname))
    if p in ("/account", "/approvals", "/admin"):
        return pages.account_page(ctx, p)
    if p == "/api/account" or p.startswith(("/api/account/", "/api/approvals", "/api/admin")):
        return accounts.manage_get(ctx, p)
    if p.startswith("/t/"):
        return "proxy"
    if p == "/api/events":   # 예전 모양(파동이 펫 pet.js 등) — 작업 표에서 끝난 것만
        try:
            since = int((ctx.raw.split("since=") + ["0"])[1].split("&")[0] or 0)
        except ValueError:
            since = 0
        return ctx.send(jobs.legacy_events(ctx.agent_user, since))
    if p == "/api/jobs":
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
        try:
            lim, since = int(q.get("limit", ["50"])[0]), int(q.get("since", ["0"])[0])
        except ValueError:
            return ctx.send({"error": "limit·since 는 숫자"}, code=400)
        return ctx.send(jobs.listing(ctx.agent_user, q.get("unread", ["0"])[0] in ("1", "true"), lim, since))
    if p == "/api/jobs/stream":
        return "jobs_stream"
    if p == "/api/gpu":
        return ctx.send(tools.gpu_status())
    if p == "/api/tools":
        return "tools"
    if p.startswith("/api/guide/"):
        d = p.split("/")[-1]
        return ctx.send(tools.guide(d).encode() if (tools.tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
    if p.startswith("/api/readme/"):
        d = p.split("/")[-1]
        f = os.path.join(config.BASE, d, "README.md") if tools.tool(d) else ""
        return ctx.send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
    if p == "/guide" or p.startswith("/guide/"):
        return pages.guide_file(ctx, p)
    if p != "/":
        return ctx.send({"error": "not found"}, code=404)
    return pages.home(ctx)


def tools_sync(ctx, up):
    with db.db() as c:
        return ctx.send({"tools": tools.tool_list(c, up, ctx.uid), "rank": tools.user_rank(c), "posts": tools.posts(c, ctx.uid), "me": accounts.me(c, ctx.uid, ctx.uname)})


# ── POST ────────────────────────────────────────────────────────────────
def login_sync(ctx, body):
    """POST /api/login · /api/signup"""
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    uid, pin = str(req.get("id", "")).strip().lower(), str(req.get("pin", ""))
    try:
        with db.db() as c:
            if ctx.p == "/api/signup":
                accounts.check_new(c, uid, str(req.get("name", "")), pin, ctx.local_owner())   # 관리자 아이디 가입은 진짜 로컬(토큰)에서만
                d = str(req.get("dept") or "")
                if d and d not in accounts.visible_depts(c):
                    raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
                accounts.set_user(c, uid, str(req["name"]).strip()[:40], "", pin)
                if d:
                    accounts.new_request(c, uid, "dept_join", d)
            else:
                if sessions.limited(c, uid):
                    raise ValueError("PIN 을 여러 번 틀렸습니다. 10분 뒤에 다시 해 주세요")
                if not accounts.check_pin(c, uid, pin):
                    sessions.add_fail(uid)
                    raise ValueError("아이디 또는 PIN 이 틀립니다 (정지된 계정이면 관리자에게)")
                sessions.clear_fails(c, uid)
            keep = ctx.p == "/api/signup" or sessions.want_remember(req.get("remember", True))
            tok = sessions.new_session(c, uid, keep)
        return ctx.send({"ok": True, "id": uid}, headers=[("Set-Cookie", sessions.cookie(tok, persist=keep))])
    except (ValueError, KeyError) as e:
        return ctx.send({"error": str(e)}, code=400)


def change_pin(ctx, c, req):
    """POST /api/pin — 지금 PIN 확인(잠금 포함) → 새 PIN, 내 세션 모두 끊고 새 세션(로그인 유지 여부는 그대로)"""
    if ctx.uid == "owner":
        raise ValueError("로컬 접속은 계정이 없습니다 — 로그인 후 바꾸세요")
    if sessions.limited(c, ctx.uid) or not accounts.check_pin(c, ctx.uid, req.get("pin", "")):
        sessions.add_fail(ctx.uid)
        raise ValueError("지금 PIN 이 틀립니다 (10분에 5번까지)")
    if not accounts.PIN_RE.match(req.get("new", "")):
        raise ValueError("새 PIN 은 숫자 4~8자리")
    accounts.set_pin(c, ctx.uid, req["new"])
    keep = not sessions.is_short(c, ctx.cookie)
    sessions.drop_user_sessions(c, ctx.uid)
    tok = sessions.new_session(c, ctx.uid, keep)
    return ctx.send({"ok": True}, headers=[("Set-Cookie", sessions.cookie(tok, persist=keep))])


def tool_action(ctx, c, path, req):
    """POST /api/open · /api/like · /api/start · /api/stop {dir}"""
    t = tools.tool(req.get("dir"))
    if not t:
        return ctx.send({"error": "unknown tool"}, code=404)
    if path in ("/api/open", "/api/like"):
        c.execute("INSERT INTO events VALUES(?,?,?,?)", (path[5:], t["dir"], db.now(), ctx.uid))
    elif path in ("/api/start", "/api/stop") and tools.is_remote(t):
        return ctx.send({"error": "다른 컨테이너에서 항상 켜져 있는 도구입니다"}, code=409)
    elif path == "/api/start":
        if tools.alive_sync(tools.tool_addr(t)) and not ctx.is_owner:
            return ctx.send({"error": "이미 실행 중입니다"}, code=409)
        tools.start(t, jobs.tool_env(t)); tools.alive_reset()   # 켜짐 캐시 비움 — 다음 /api/tools 가 바로 다시 확인
    elif path == "/api/stop":
        if not ctx.is_owner:
            return ctx.send({"error": "도구 중지는 관리자만 할 수 있습니다"}, code=403)
        tools.stop(t); tools.alive_reset()
    else:
        return ctx.send({"error": "not found"}, code=404)
    return ctx.send({"ok": True})


def post_sync(ctx, body):
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    path = ctx.raw   # portal.py 처럼 쿼리까지 포함한 경로로 판정
    try:
        with db.db() as c:
            if path == "/api/pin":
                return change_pin(ctx, c, req)
            if path.startswith(("/api/account/", "/api/approvals/", "/api/admin/")):
                return accounts.manage_post(ctx, c, path, req)
            if path == "/api/post":
                if not (req.get("title") or "").strip():
                    raise ValueError("제목이 비었습니다")
                c.execute("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES(?,?,?,?,?,?)",
                          (req["title"].strip(), req.get("body", "").strip(), ctx.uname, req.get("dept", ""),
                           json.dumps(["#" + x.strip("#") for x in (req.get("tags") or "").split() if x]), db.now()))
                return ctx.send({"ok": True})
            if path == "/api/post_like":
                c.execute("INSERT OR IGNORE INTO post_likes(post,user) VALUES(?,?)", (int(req["id"]), ctx.uid))
                return ctx.send({"ok": True})
            if path == "/api/jobs/read":
                ids = req.get("ids")
                if not req.get("all") and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
                    raise ValueError("ids(문자열 목록) 또는 all: true")
                got = jobs.mark_read(ctx.agent_user, None if req.get("all") else ids)
                if got:
                    jobs.HUB.publish("read", {"ids": got}, ctx.agent_user, exact=True)   # 같은 사람의 다른 탭 배지도 맞춤
                return ctx.send({"ok": True, "read": len(got), "unread": jobs.listing(ctx.agent_user, limit=1)["unread"]})
            if path == "/api/tool_add":
                if not ctx.is_owner:
                    return ctx.send({"error": "앱 등록은 관리자만 할 수 있습니다"}, code=403)
                return ctx.send(tools.add_tool(req))
            return tool_action(ctx, c, path, req)
    except PermissionError as e:
        return ctx.send({"error": str(e)}, code=403)
    except (ValueError, KeyError, TypeError) as e:   # TypeError: 값 형식이 틀린 JSON(예: title 이 숫자) — portal.py 는 연결이 끊겼음
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다"}, code=400)


# ── 진입점: portal.py 의 do_GET·do_POST 와 같은 순서 ────────────────────────
async def handle(request: Request):
    ctx = Ctx(request)
    try:
        if ctx.method == "GET":
            r = await run_in_threadpool(get_sync, ctx)
            if r == "proxy":
                return await proxy.proxy(ctx)
            if r == "jobs_stream":
                return jobs.stream(ctx)
            if r == "tools":
                up = await tools.alive_map([tools.tool_addr(t) for t in tools.tools()])
                return await run_in_threadpool(tools_sync, ctx, up)
            return r
        if ctx.method == "POST":
            if ctx.raw == "/api/internal/jobs":   # 도구 → 포털(쿠키 없음, 접속지 + 도구 토큰)
                try:
                    body = await read_body(ctx, 64 * 1024)
                except TooBig:
                    return too_big(ctx, 64 * 1024)
                return await run_in_threadpool(jobs.internal_post, ctx, body)
            if ctx.raw in ("/api/login", "/api/signup"):
                try:
                    body = await read_body(ctx, config.MAX_JSON)
                except TooBig:
                    return too_big(ctx, config.MAX_JSON)
                return await run_in_threadpool(login_sync, ctx, body)
            if not await run_in_threadpool(ctx.auth):
                await drain(request, length(ctx) or 0)
                return ctx.need_login()
            if ctx.raw.startswith("/t/"):
                return await proxy.proxy(ctx)
            try:
                body = await read_body(ctx, config.MAX_JSON)
            except TooBig:
                return too_big(ctx, config.MAX_JSON)
            return await run_in_threadpool(post_sync, ctx, body)
        return not_implemented(ctx.method)
    except ValueError:   # Content-Length 형식 오류 등
        return ctx.send({"error": "요청 형식이 틀립니다"}, code=400)
    except Exception as e:   # 예상 못 한 오류: 내부 정보(경로·스택)는 로그에만, 응답은 짧게
        print("error:", ctx.method, ctx.p, type(e).__name__, e, flush=True)
        return ctx.send({"error": "서버 오류"}, code=500)


app.add_api_route("/{_path:path}", handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], include_in_schema=False)
