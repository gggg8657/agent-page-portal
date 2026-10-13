"""에이전트 페이지 포털 — FastAPI(uvicorn, 비동기) 판. portal.py(stdlib ThreadingHTTPServer)와 같은 주소·응답·DB.
  ~/.local/fastportal-venv/bin/python -m fastportal            # http://0.0.0.0:8720 (PORT 로 바꿈)
  uvicorn fastportal.app:app --host 0.0.0.0 --port 8720 --no-server-header --no-proxy-headers   # 같은 앱(TLS 최소 버전은 python -m 쪽만 1.2 로 고정)

왜: 스트리밍(SSE) 연결 하나마다 스레드 하나를 쓰던 구조를, 이벤트 루프 하나가 수백 개를 중계하는 구조로.
  - /t/<도구>/…  : httpx.AsyncClient(연결 풀)로 요청·응답 본문을 버퍼링 없이 흘림. 브라우저가 끊으면 도구 쪽 연결도 바로 닫음
  - DB·PIN 해시(PBKDF2 60만 번)·nvidia-smi 같은 막히는 일은 스레드 풀(run_in_threadpool)에서 — 이벤트 루프를 막지 않게
  - 주소 판정은 portal.py 처럼 원래 요청 경로(raw) 문자열로 한다(라우터의 디코딩·슬래시 규칙 차이로 응답이 달라지지 않게)
env: portal.py 와 같음(PORT 기본 8720, AGENT_DATA, AGENT_OWNERS, TLS_CERT·TLS_KEY, SHOW_HIDDEN) +
     PORTAL_LOCAL_OWNER(기본 token — 127.0.0.1 + 헤더 X-Local-Owner=_data/.local_owner_token 일 때만 주인; 1=예전 방식, 0=끔) TRUSTED_PROXIES(쉼표, 이 주소에서 온 접속만 X-Forwarded-For 를 믿음) +
     BACKUP=1(매일 백업 — 기본 꺼짐: 운영 포털이 돌림) PORTAL_MAX_BODY(도구로 넘기는 본문 상한 바이트, 기본 600MB → 413)
     PORTAL_MAX_JSON(포털 API JSON 본문 상한, 기본 1MB) TOOLS_ALIVE_TTL(도구 켜짐 확인 캐시 초, 기본 5)
     TOOL_BIND(포털이 띄우는 도구의 BIND, 기본 127.0.0.1 — 컨테이너에선 0.0.0.0) WEB_DIST·WEB_PREFIX(React 빌드를 같은 출처로, 기본 꺼짐·/app)
tools.json 의 "host": 그 도구는 다른 컨테이너·서버(http://<host>:<port>) — 상태 확인·프록시만, 시작·중지는 409"""
import asyncio, hashlib, http.cookiejar, json, os, re, time, urllib.parse
from contextlib import asynccontextmanager

import anyio
import httpx
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from . import core
from .core import LOCAL, OWNERS, TLS, HOP

MAX_BODY = int(os.environ.get("PORTAL_MAX_BODY", str(600 * 1024 * 1024)))   # 보안 설계서 '포털 업로드 상한' 제안값(meeting 음성 500MB 포함)
MAX_JSON = int(os.environ.get("PORTAL_MAX_JSON", str(1024 * 1024)))
DRAIN_MAX = 4 * 1024 * 1024 * 1024   # 이보다 큰 본문은 다 읽어 버리지 않고 바로 끊음
ALIVE_TTL = float(os.environ.get("TOOLS_ALIVE_TTL", "5"))
PROXY_TIMEOUT = 3600
DROP_REQ = HOP | {"x-agent-user", "x-forwarded-user", "x-user", "cookie", "x-local-owner"}   # 로컬 주인 토큰은 도구로 넘기지 않음
AG_COOKIE = re.compile(r"(?:^|;\s*)ag(?:sid|theme)=[^;]*")

core.init_db()

EVENTS = []          # 오래 걸린 도구 작업(1분 넘는 POST) 끝남 — /api/events. 프로세스 메모리(1워커 전제, README 참고)
CLIENT = {"c": None}


class _NoCookies(http.cookiejar.DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False


@asynccontextmanager
async def lifespan(_app):
    # 쿠키 저장소는 끔: 켜 두면 한 사람의 요청에 도구가 준 Set-Cookie 를 클라이언트가 기억했다가 다른 사람 요청에 붙인다.
    # 리디렉트는 직접 따라감(_send) — httpx 가 따라가면 Cookie 헤더를 빼서 portal.py(urllib)와 달라짐.
    jar = http.cookiejar.CookieJar(policy=_NoCookies())
    CLIENT["c"] = httpx.AsyncClient(timeout=httpx.Timeout(PROXY_TIMEOUT, connect=10.0), follow_redirects=False, trust_env=False, cookies=jar,
                                    limits=httpx.Limits(max_connections=None, max_keepalive_connections=64, keepalive_expiry=30))
    if os.environ.get("BACKUP", "0") == "1":
        import threading
        threading.Thread(target=core.backup_loop, daemon=True).start()
    print(f"fastportal → {'https' if TLS else 'http'} data={core.DATA} owners={sorted(OWNERS)} max_body={MAX_BODY} backup={os.environ.get('BACKUP', '0')}  {core._SIG}", flush=True)
    yield
    await CLIENT["c"].aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)   # /docs·/openapi.json 은 열지 않음(포털 주소 규칙 그대로)


# ── 응답 ────────────────────────────────────────────────────────────────
class Raw(Response):
    """헤더를 정해진 순서·중복(Set-Cookie 여러 개) 그대로 내보내는 응답. Content-Length 는 본문에서 계산"""

    def __init__(self, body=b"", status=200, headers=(), length=True):
        self.status_code, self.body, self.background = status, body, None
        self.raw_headers = [(k.encode("latin-1") if isinstance(k, str) else k, v.encode("latin-1") if isinstance(v, str) else v) for k, v in headers]
        if length:
            self.raw_headers.append((b"content-length", str(len(body)).encode()))


def _h(v):   # 헤더 값: 한글 등은 latin-1 로 못 담으므로 UTF-8 바이트 그대로(portal.py 의 send_header 와 같음)
    return v.encode("utf-8") if isinstance(v, str) else v


class Ctx:
    """요청 하나의 상태 — portal.py 의 핸들러 속성(uid·uname·_setcookie)과 같은 뜻"""

    def __init__(self, request: Request):
        self.request = request
        self.method = request.method
        qs = request.scope.get("query_string", b"")
        self.raw = request.scope.get("raw_path", request.url.path.encode()).decode("latin-1") + ("?" + qs.decode("latin-1") if qs else "")
        self.p = self.raw.split("?")[0]
        self.headers = request.headers
        self.cookie = "; ".join(request.headers.getlist("cookie"))   # Cookie 줄이 여럿(대소문자 다른 줄 포함)이면 모두 합쳐서 봄
        self.client = core.client_ip(request.client.host if request.client else "", request.headers.getlist("x-forwarded-for"))
        self.uid = self.uname = self.setcookie = None

    def send(self, body, ctype="application/json", code=200, headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        hd = [("X-Author", _h(core._SIG_A)), ("Content-Type", ctype)] + [(k, _h(v)) for k, v in headers or ()]
        if self.setcookie:
            hd.append(("Set-Cookie", self.setcookie))
        return Raw(b, code, hd)

    @staticmethod
    def bare(code, headers=()):
        return Raw(b"", code, list(headers))

    # 계정
    def auth(self):
        """쿠키 agsid → 계정. 쿠키 없이 이 서버 안(127.0.0.1)에서 + X-Local-Owner 토큰이 맞으면 주인(core.local_owner, PORTAL_LOCAL_OWNER). 밖에서 온 X-Agent-User 등은 안 믿음"""
        self.uid = self.uname = self.setcookie = None
        m = core.SID_RE.search(self.cookie)
        if m:
            self.uid, self.uname, self.setcookie = core.session_user(m[1])
        has_cookie = bool(core.SID_ANY.search(self.cookie))   # portal.py 는 빈 'agsid=' 를 '쿠키 없음'으로 봐서 로컬이면 주인이 됨 — 여기서는 무효 쿠키
        if not self.uid and not has_cookie and core.local_owner(self.client, self.headers.get("x-local-owner")):
            self.uid, self.uname = "owner", "서버 주인(로컬)"
        return self.uid

    @property
    def is_owner(self):
        return self.uid == "owner" or self.uid in OWNERS

    @property
    def agent_user(self):
        return "owner" if self.is_owner else self.uid

    def theme(self):
        return core.user_theme(self.uid, self.cookie)

    def html_page(self, html, kind, d="", v2=""):
        return core.html_page(html, kind, self.theme(), d, v2)

    def help_html(self, d):
        return core.help_html(d, self.agent_user)

    def need_login(self):
        if self.method == "GET" and "text/html" in (self.headers.get("accept") or ""):
            return self.bare(302, [("Location", "/login?next=" + urllib.parse.quote(self.raw, safe=""))])
        return self.send({"error": "로그인이 필요합니다", "login": "/login"}, code=401)

    def redirect(self, loc):
        hd = [("Location", urllib.parse.quote(loc, safe="/?&=#%:;@!$'()*+,~-._"))]
        if self.setcookie:
            hd.append(("Set-Cookie", self.setcookie))
        return self.bare(302, hd)


# ── 본문 읽기 ───────────────────────────────────────────────────────────
class TooBig(Exception):
    pass


async def _drain(request, n):
    if n <= DRAIN_MAX:   # 보안 설계서: 상한 초과는 본문을 끝까지 읽어 버린 뒤 413(브라우저가 '연결 끊김' 대신 413 을 받게)
        try:
            async for _ in request.stream():
                pass
        except Exception:
            pass


def _length(ctx):
    v = ctx.headers.get("content-length")
    if v is None:
        return None
    if not v.isdigit():
        raise ValueError
    return int(v)


async def read_body(ctx, cap):
    """Content-Length 또는 청크 본문을 cap 까지 읽음. 넘으면 끝까지 버리고 TooBig"""
    n = _length(ctx)
    if n is not None and n > cap:
        await _drain(ctx.request, n)
        raise TooBig
    buf = bytearray()
    async for chunk in ctx.request.stream():
        buf += chunk
        if len(buf) > cap:
            await _drain(ctx.request, 0)
            raise TooBig
    return bytes(buf)


def too_big(ctx, cap):
    return ctx.send({"error": f"요청 본문이 너무 큽니다 (최대 {cap // (1024 * 1024)}MB)"}, code=413)


def parse_json(b):
    req = json.loads(b or b"{}")
    if not isinstance(req, dict):
        raise ValueError
    return req


# ── 정적 파일 (/guide · /theme · /landing/home.png) ─────────────────────────
def guide_file(ctx, p):
    if p == "/guide":
        return ctx.bare(302, [("Location", "/guide/")])
    rel = urllib.parse.unquote(p[len("/guide/"):]) or "index.html"
    m = re.fullmatch(r"index\.html|agent-portal\.pdf|img/[A-Za-z0-9][\w.-]*\.(png|jpe?g|svg|webp)|fonts/[A-Za-z0-9][\w.-]*\.woff2|fonts/OFL\.txt", rel)
    top = core.GUIDE_DIR
    e = re.fullmatch(r"ext/([a-z0-9-]+-local)/([A-Za-z0-9][\w.-]*\.(png|jpe?g|webp))", rel)
    if e:
        m, top, rel = e, os.path.join(core.BASE, e[1], "static", "guide"), e[2]
    f = os.path.join(top, rel)
    if not m or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
        return ctx.send({"error": "not found"}, code=404)
    st = os.stat(f); lm = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(st.st_mtime)); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    cc = ("Cache-Control", "no-cache" if rel == "index.html" else "private, max-age=3600")
    if ctx.headers.get("if-none-match") == etag:
        return Raw(b"", 304, [("ETag", etag), cc])
    ext = rel.rsplit(".", 1)[1]
    ctype = {"html": "text/html; charset=utf-8", "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "svg": "image/svg+xml", "webp": "image/webp", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[ext]
    hd = [cc, ("ETag", etag), ("Last-Modified", lm), ("X-Content-Type-Options", "nosniff")]
    if ext == "pdf":
        hd.append(("Content-Disposition", "attachment; filename=agent-portal.pdf; filename*=UTF-8''" + urllib.parse.quote("에이전트-포털-사용-설명서.pdf")))
    body = open(f, "rb").read()
    return ctx.send(ctx.html_page(body.decode("utf-8"), "guide") if ext == "html" else body, ctype, headers=hd)


def theme_file(ctx, p):
    if p == core.LANDING_IMG[0]:
        f = core.LANDING_IMG[1]
        st = os.stat(f); etag = f'"{int(st.st_mtime)}-{st.st_size}"'; cc = ("Cache-Control", "public, max-age=86400")
        if ctx.headers.get("if-none-match") == etag:
            return Raw(b"", 304, [("ETag", etag), cc])
        return ctx.send(open(f, "rb").read(), "image/png", headers=[cc, ("ETag", etag), ("X-Content-Type-Options", "nosniff")])
    rel = urllib.parse.unquote(p[len("/theme/"):])
    m = re.fullmatch(r"[a-z][a-z0-9-]*\.(css|js)|tools/[a-z0-9][a-z0-9_-]*\.(css)|fonts/([A-Za-z0-9][\w.-]*\.(woff2|txt))", rel)
    top = os.path.join(core.GUIDE_DIR, "fonts") if m and m[3] else core.THEME_DIR
    f = os.path.join(top, m[3] if m and m[3] else rel)
    if not m or rel == "boot.js" or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
        return ctx.send({"error": "not found"}, code=404)
    st = os.stat(f); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    cc = ("Cache-Control", "public, max-age=86400")
    if ctx.headers.get("if-none-match") == etag:
        return Raw(b"", 304, [("ETag", etag), cc])
    ctype = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[f.rsplit(".", 1)[1]]
    return ctx.send(open(f, "rb").read(), ctype, headers=[cc, ("ETag", etag), ("X-Content-Type-Options", "nosniff")])


# ── 계정 설정·승인·관리 ───────────────────────────────────────────────────
def page(ctx, p):
    with core.db() as c:
        ok = p == "/account" or ctx.is_owner or (p == "/approvals" and core.my_heads(c, ctx.uid))
    if not ok:
        return ctx.bare(302, [("Location", "/account")])
    return ctx.send(ctx.html_page(core.ACCOUNT + ctx.help_html("portal"), "portal", v2="account"), "text/html; charset=utf-8")


def manage_get(ctx, p):
    import datetime
    with core.db() as c:
        if p == "/api/account":
            return ctx.send({"me": core.me(c, ctx.uid, ctx.uname), "depts": core.visible_depts(c), "requests": core.req_rows(c, "uid=?", (ctx.uid,))[:20]})
        if p == "/api/account/theme":
            return ctx.send({"theme": ctx.theme(), "themes": sorted(core.THEMES)})
        if p == "/api/account/notices":
            rows = core.req_rows(c, "uid=? AND status IN ('approved','rejected') AND notified=0", (ctx.uid,))
            c.execute("UPDATE requests SET notified=1 WHERE uid=? AND status IN ('approved','rejected')", (ctx.uid,))
            return ctx.send({"notices": rows, "approvals": core.me(c, ctx.uid, ctx.uname)["approvals"]})
        if p == "/api/approvals":
            heads = core.my_heads(c, ctx.uid)
            if not (ctx.is_owner or heads):
                return ctx.send({"error": "부서장·관리자만 볼 수 있습니다"}, code=403)
            pend = [r for r in core.req_rows(c, "status='pending'") if core.can_decide(c, ctx.uid, r)]
            cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
            members = {d: [dict(r) for r in c.execute(
                "SELECT u.id, u.name, u.seen, (SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open') total, "
                "(SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open' AND e.ts>=?) recent, "
                "EXISTS(SELECT 1 FROM dept_heads h WHERE h.dept=u.dept AND h.uid=u.id) head FROM users u WHERE u.dept=? AND NOT u.disabled ORDER BY u.name", (cut, d))]
                for d in heads}
            done = [r for r in core.req_rows(c, "status IN ('approved','rejected') AND approver=?", (ctx.uid,))][:20]
            return ctx.send({"pending": pend, "members": members, "done": done})
        if p == "/api/admin":
            if not ctx.is_owner:
                return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
            heads = {}
            for r in c.execute("SELECT dept, uid FROM dept_heads"):
                heads.setdefault(r["uid"], []).append(r["dept"])
            users = [dict(r) | {"role": "관리자" if r["id"] in OWNERS else "부서장" if r["id"] in heads else "일반", "heads": heads.get(r["id"], [])}
                     for r in c.execute("SELECT id, name, dept, ts, seen, disabled FROM users ORDER BY ts")]
            depts = [dict(r) | {"members": c.execute("SELECT COUNT(*) FROM users WHERE dept=?", (r["name"],)).fetchone()[0], "heads": core.heads_of(c, r["name"])}
                     for r in c.execute("SELECT name, hidden FROM depts ORDER BY hidden, name")]
            return ctx.send({"users": users, "depts": depts, "pending": core.req_rows(c, "status='pending'"), "owners": sorted(OWNERS),
                             "audit": [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY rowid DESC LIMIT 60")]})
    return ctx.send({"error": "not found"}, code=404)


def manage_post(ctx, c, p, req):
    uid = ctx.uid
    acct = c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
    if p == "/api/account/theme":
        t = str(req.get("theme", ""))
        if t not in core.THEMES:
            raise ValueError("없는 테마")
        if acct:
            c.execute("UPDATE users SET theme=? WHERE id=?", (t, uid))
        return ctx.send({"ok": True, "theme": t}, headers=[("Set-Cookie", f"agtheme={t}; Path=/; Max-Age=31536000; SameSite=Lax" + ("; Secure" if TLS else ""))])
    if p.startswith("/api/account/"):
        if not acct:
            raise ValueError("로컬 접속은 계정이 없습니다 — 계정으로 로그인하세요")
        if p == "/api/account/name":
            nm = str(req.get("name", "")).strip()[:40]
            if not nm:
                raise ValueError("이름을 적어 주세요")
            c.execute("UPDATE users SET name=? WHERE id=?", (nm, uid))
        elif p == "/api/account/dept":
            d = str(req.get("dept", ""))
            if d == (c.execute("SELECT dept FROM users WHERE id=?", (uid,)).fetchone()[0] or ""):
                raise ValueError("이미 그 부서입니다")
            core.new_request(c, uid, "dept_join", d)
        elif p == "/api/account/head":
            d = c.execute("SELECT dept FROM users WHERE id=?", (uid,)).fetchone()[0]
            if not d:
                raise ValueError("부서 소속이 승인된 뒤에 요청할 수 있습니다")
            if uid in core.heads_of(c, d):
                raise ValueError("이미 부서장입니다")
            core.new_request(c, uid, "dept_head", d)
        elif p == "/api/account/onboarded":
            c.execute("UPDATE users SET onboarded=? WHERE id=?", (1 if req.get("done", True) else 0, uid))
        elif p == "/api/account/cancel":
            if not c.execute("UPDATE requests SET status='cancelled', decided_ts=? WHERE id=? AND uid=? AND status='pending'", (core.now(), int(req.get("id") or 0), uid)).rowcount:
                raise ValueError("취소할 수 있는 요청이 아닙니다")
        else:
            return ctx.send({"error": "not found"}, code=404)
        return ctx.send({"ok": True})
    if p == "/api/approvals/decide":
        core.decide(c, uid, int(req.get("id") or 0), bool(req.get("ok")), str(req.get("note") or "").strip())
        return ctx.send({"ok": True})
    if not p.startswith("/api/admin/"):
        return ctx.send({"error": "not found"}, code=404)
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    if p == "/api/admin/dept":
        op, name, new = req.get("op"), str(req.get("name", "")).strip()[:40], str(req.get("new", "")).strip()[:40]
        if op == "add":
            if not name or re.search(r"[<>\"'&]", name):
                raise ValueError("부서 이름을 확인하세요")
            c.execute("INSERT INTO depts(name, ts) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET hidden=0", (name, core.now()))
        elif op == "rename":
            if not new or re.search(r"[<>\"'&]", new) or c.execute("SELECT 1 FROM depts WHERE name=?", (new,)).fetchone():
                raise ValueError("새 이름이 비었거나 이미 있습니다")
            if not c.execute("UPDATE depts SET name=? WHERE name=?", (new, name)).rowcount:
                raise ValueError("없는 부서")
            for t in ("users", "dept_heads", "requests"):
                c.execute(f"UPDATE {t} SET dept=? WHERE dept=?", (new, name))
        elif op in ("hide", "show"):
            c.execute("UPDATE depts SET hidden=? WHERE name=?", (op == "hide", name))
        else:
            raise ValueError("op")
        core.audit(c, uid, "dept_" + op, name, new)
    elif p == "/api/admin/head":
        d, u = str(req.get("dept", "")), str(req.get("uid", ""))
        if req.get("op") == "grant":
            if not c.execute("SELECT 1 FROM users WHERE id=? AND dept=?", (u, d)).fetchone():
                raise ValueError("그 부서 소속(승인된) 계정만 부서장이 될 수 있습니다")
            c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (d, u, core.now()))
        elif req.get("op") == "revoke":
            c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, u))
        else:
            raise ValueError("op")
        core.audit(c, uid, "head_" + req["op"], u, d)
    elif p in ("/api/admin/reset", "/api/admin/disable"):
        u = str(req.get("uid", ""))
        if not c.execute("SELECT 1 FROM users WHERE id=?", (u,)).fetchone():
            raise ValueError("없는 계정")
        if p.endswith("reset"):
            pin = f"{core.secrets.randbelow(10**6):06d}"
            core.set_pin(c, u, pin); c.execute("DELETE FROM sessions WHERE uid=?", (u,)); core.audit(c, uid, "pin_reset", u)
            return ctx.send({"ok": True, "pin": pin})
        off = bool(req.get("disabled"))
        if off and u in OWNERS:
            raise ValueError("관리자 계정은 정지할 수 없습니다")
        c.execute("UPDATE users SET disabled=? WHERE id=?", (off, u))
        if off:
            c.execute("DELETE FROM sessions WHERE uid=?", (u,))
        core.audit(c, uid, "disable" if off else "enable", u)
    else:
        return ctx.send({"error": "not found"}, code=404)
    return ctx.send({"ok": True})


# ── React 화면(agent-page-web 빌드) 같은 출처로 내보내기 ─────────────────────────
# WEB_DIST(빌드 폴더)가 있으면 WEB_PREFIX(기본 /app) 아래에서 dist 를 내보냄. 파일이 없는 경로는 index.html(SPA 폴백),
# 없는 자산(assets/·확장자 있는 파일)은 404. assets/ 는 해시 붙은 이름이라 1년 캐시, 그 밖(index.html 등)은 no-cache.
# 로그인 없이도 화면 파일은 내보냄(로그인 화면도 React) — API 는 지금처럼 401. 규칙은 agent-page-web/server.py static() 과 같음
WEB_DIST = os.path.realpath(os.environ["WEB_DIST"]) if os.environ.get("WEB_DIST") else ""
WEB_PREFIX = "/" + os.environ.get("WEB_PREFIX", "/app").strip("/")
if WEB_DIST and (WEB_PREFIX == "/" or not re.fullmatch(r"(/[A-Za-z0-9_-]+)+", WEB_PREFIX)):
    raise SystemExit(f"WEB_PREFIX 는 /app 처럼 하위 경로여야 합니다(지금 {WEB_PREFIX!r}) — '/' 로 옮기는 것은 아직 안 됨")
WEB_SPA = re.compile(r"/($|[?#]|(all|fav|recent|account)([/?#]|$)|tool/)")   # React 라우터 경로(agent-page-web Landing.tsx SPA 와 같음)
WEB_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
           "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
WEB_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
             ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
             ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json", ".wasm": "application/wasm"}


def web_path(p):
    return bool(WEB_DIST) and (p == WEB_PREFIX or p.startswith(WEB_PREFIX + "/") or p == "/web/landing.json")


def web_landing(ctx):
    """로그인 전 랜딩의 분류·도구 수(포털 /login 화면과 같은 값, 숨긴 도구 빼고) — React Landing 이 읽음(server.py /web/landing.json 과 같은 모양)"""
    gs = [{"name": k, "n": len(v), "names": " · ".join(v[:5]) + (" …" if len(v) > 5 else "")} for k, v in core.landing_groups()]
    return ctx.send({"groups": gs, "tools": sum(g["n"] for g in gs)}, "application/json; charset=utf-8", headers=[("Cache-Control", "no-cache")])


def web_file(ctx, p):
    if p == "/web/landing.json" or p == WEB_PREFIX + "/web/landing.json":
        return web_landing(ctx)
    if p == WEB_PREFIX:
        return ctx.bare(302, [("Location", WEB_PREFIX + "/" + ("?" + ctx.raw.split("?", 1)[1] if "?" in ctx.raw else ""))])
    rel = urllib.parse.unquote(p[len(WEB_PREFIX) + 1:])
    if rel == "login" and core.SID_RE.search(ctx.cookie) and ctx.auth() and ctx.uid != "owner":   # 이미 로그인 → 랜딩 대신 바로(포털 /login·server.py 와 같음)
        nxt = core.safe_next(urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query).get("next", ["/"])[0])
        m = re.fullmatch(r"/t/([a-z0-9-]+)/?", nxt)
        if m:
            nxt = f"/tool/{m[1]}"
        if WEB_SPA.match(nxt):   # React 의 next 는 라우터 기준(/all, /tool/x) — 화면 주소로
            nxt = WEB_PREFIX + nxt
        return ctx.redirect(nxt)
    f = os.path.realpath(os.path.join(WEB_DIST, rel)) if rel and "\x00" not in rel else ""
    if not (f and f.startswith(WEB_DIST + os.sep) and os.path.isfile(f)):
        if rel.startswith("assets/") or re.search(r"\.[A-Za-z0-9]{1,12}$", rel.rsplit("/", 1)[-1]):
            return ctx.send({"error": "not found"}, code=404)   # 없는 자산은 index.html 이 아니라 404(옛 해시 파일 요청 등)
        f = os.path.join(WEB_DIST, "index.html")               # SPA 라우팅(/app/login, /app/tool/…, /app/account 등)
        if not os.path.isfile(f):
            return ctx.send({"error": "WEB_DIST 에 index.html 이 없습니다"}, code=503)
    ext = os.path.splitext(f)[1].lower()
    st = os.stat(f)
    etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    immutable = f.startswith(os.path.join(WEB_DIST, "assets") + os.sep)
    hd = [("ETag", etag), ("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache"), ("X-Content-Type-Options", "nosniff"),
          ("Referrer-Policy", "same-origin")]
    if ext == ".html":
        hd += [("Content-Security-Policy", WEB_CSP), ("X-Frame-Options", "SAMEORIGIN")]
    if ctx.headers.get("if-none-match") == etag:
        return Raw(b"", 304, hd)
    with open(f, "rb") as fh:
        body = fh.read()
    ctx.setcookie = None   # 정적 파일 응답엔 세션 연장 쿠키를 싣지 않음(캐시되는 응답)
    return ctx.send(body, WEB_TYPES.get(ext, "application/octet-stream"), headers=hd)


# ── GET ─────────────────────────────────────────────────────────────────
def get_sync(ctx):
    """막히는 일(DB·파일)을 하는 GET 전부 — 스레드 풀에서. 비동기로 할 일은 문자열 표시로 돌려줌('proxy'·'events'·'tools')"""
    p = ctx.p
    if web_path(p):
        return web_file(ctx, p)
    if p == "/login":
        if ctx.auth() and ctx.uid != "owner":
            return ctx.redirect(core.safe_next(urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query).get("next", ["/"])[0]))
        who = ctx.uname if ctx.uid == "owner" else ""
        ctx.setcookie, ctx.uid = None, None
        return ctx.send(ctx.html_page(core.landing(core.LOGIN, who), "portal"), "text/html; charset=utf-8")
    if p == core.LANDING_IMG[0] or p.startswith("/theme/"):
        return theme_file(ctx, p)
    if p == "/api/depts":
        with core.db() as c:
            return ctx.send({"depts": core.visible_depts(c)})
    if p == "/logout":
        m = core.SID_RE.search(ctx.cookie)
        if m:
            with core.db() as c:
                c.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(m[1].encode()).hexdigest(),))
        return ctx.bare(302, [("Location", "/login"), ("Set-Cookie", core.cookie("", 0))])
    if not ctx.auth():
        return ctx.need_login()
    if p == "/api/me":
        with core.db() as c:
            return ctx.send(core.me(c, ctx.uid, ctx.uname))
    if p in ("/account", "/approvals", "/admin"):
        return page(ctx, p)
    if p == "/api/account" or p.startswith(("/api/account/", "/api/approvals", "/api/admin")):
        return manage_get(ctx, p)
    if p.startswith("/t/"):
        return "proxy"
    if p == "/api/events":
        return "events"
    if p == "/api/gpu":
        return ctx.send(core.gpu_status())
    if p == "/api/tools":
        return "tools"
    if p.startswith("/api/guide/"):
        d = p.split("/")[-1]
        return ctx.send(core.guide(d).encode() if (core.tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
    if p.startswith("/api/readme/"):
        d = p.split("/")[-1]
        f = os.path.join(core.BASE, d, "README.md") if core.tool(d) else ""
        return ctx.send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
    if p == "/guide" or p.startswith("/guide/"):
        return guide_file(ctx, p)
    if p != "/":
        return ctx.send({"error": "not found"}, code=404)
    tour = f'<script src="/theme/tour.js?v={core.theme_ver()}"></script>'
    return ctx.send(ctx.html_page(core.HTML + tour + ctx.help_html("portal"), "portal", v2="home"), "text/html; charset=utf-8")


# 도구 켜짐 확인: 5초 캐시 — 탭 수백 개가 /api/tools 를 폴링해도 포트 확인은 5초에 한 번(동시에 와도 한 번)
_ALIVE = {"t": 0.0, "v": {}}
_ALIVE_LOCK = asyncio.Lock()


async def _alive(addr):
    host, port = addr
    if not host:   # tools.json 의 host 형식이 틀림 — 접속하지 않음
        return False
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 0.5)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def alive_map(addrs):
    """(host, port) → 켜짐. host 는 core.tool_addr(없으면 127.0.0.1, 형식 틀리면 None)"""
    async with _ALIVE_LOCK:
        if time.monotonic() - _ALIVE["t"] < ALIVE_TTL and set(addrs) <= set(_ALIVE["v"]):
            return _ALIVE["v"]
        addrs = sorted(set(addrs), key=lambda a: (a[0] or "", a[1]))
        res = await asyncio.gather(*(_alive(a) for a in addrs))
        _ALIVE.update(t=time.monotonic(), v=dict(zip(addrs, res)))
        return _ALIVE["v"]


def tools_sync(ctx, up):
    with core.db() as c:
        u = core.usage(c)
        return ctx.send({"tools": [{**t, "up": up.get(core.tool_addr(t), False), "installed": core.is_remote(t) or os.path.isdir(os.path.join(core.BASE, t["dir"])),
                                    "url": f"/t/{t['dir']}/", "usage": u.get(t["dir"], {"recent": 0, "total": 0, "users": 0, "likes": 0})}
                                   for t in core.tools() if core.SHOW_HIDDEN or not t.get("hidden")],
                         "rank": core.user_rank(c), "posts": core.posts(c, ctx.uid), "me": core.me(c, ctx.uid, ctx.uname)})


# ── POST ────────────────────────────────────────────────────────────────
def login_sync(ctx, body):
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    uid, pin = str(req.get("id", "")).strip().lower(), str(req.get("pin", ""))
    try:
        with core.db() as c:
            if ctx.p == "/api/signup":
                core.check_new(c, uid, str(req.get("name", "")), pin, core.local_owner(ctx.client, ctx.headers.get("x-local-owner")))   # 관리자 아이디 가입은 진짜 로컬(토큰)에서만
                d = str(req.get("dept") or "")
                if d and d not in core.visible_depts(c):
                    raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
                core.set_user(c, uid, str(req["name"]).strip()[:40], "", pin)
                if d:
                    core.new_request(c, uid, "dept_join", d)
            else:
                if core.limited(c, uid):
                    raise ValueError("PIN 을 여러 번 틀렸습니다. 10분 뒤에 다시 해 주세요")
                if not core.check_pin(c, uid, pin):
                    core.add_fail(uid)
                    raise ValueError("아이디 또는 PIN 이 틀립니다 (정지된 계정이면 관리자에게)")
                core.clear_fails(c, uid)
            keep = ctx.p == "/api/signup" or core.want_remember(req.get("remember", True))
            tok = core.new_session(c, uid, keep)
        return ctx.send({"ok": True, "id": uid}, headers=[("Set-Cookie", core.cookie(tok, persist=keep))])
    except (ValueError, KeyError) as e:
        return ctx.send({"error": str(e)}, code=400)


def post_sync(ctx, body):
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    path = ctx.raw   # portal.py 처럼 쿼리까지 포함한 경로로 판정
    try:
        with core.db() as c:
            if path == "/api/pin":
                if ctx.uid == "owner":
                    raise ValueError("로컬 접속은 계정이 없습니다 — 로그인 후 바꾸세요")
                if core.limited(c, ctx.uid) or not core.check_pin(c, ctx.uid, req.get("pin", "")):
                    core.add_fail(ctx.uid)
                    raise ValueError("지금 PIN 이 틀립니다 (10분에 5번까지)")
                if not core.PIN_RE.match(req.get("new", "")):
                    raise ValueError("새 PIN 은 숫자 4~8자리")
                core.set_pin(c, ctx.uid, req["new"])
                m = core.SID_RE.search(ctx.cookie)
                keep = not (m and c.execute("SELECT 1 FROM sess_short WHERE token=?", (hashlib.sha256(m[1].encode()).hexdigest(),)).fetchone())
                c.execute("DELETE FROM sessions WHERE uid=?", (ctx.uid,))
                tok = core.new_session(c, ctx.uid, keep)
                return ctx.send({"ok": True}, headers=[("Set-Cookie", core.cookie(tok, persist=keep))])
            if path.startswith(("/api/account/", "/api/approvals/", "/api/admin/")):
                return manage_post(ctx, c, path, req)
            if path == "/api/post":
                if not (req.get("title") or "").strip():
                    raise ValueError("제목이 비었습니다")
                c.execute("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES(?,?,?,?,?,?)",
                          (req["title"].strip(), req.get("body", "").strip(), ctx.uname, req.get("dept", ""),
                           json.dumps(["#" + x.strip("#") for x in (req.get("tags") or "").split() if x]), core.now()))
                return ctx.send({"ok": True})
            if path == "/api/post_like":
                c.execute("INSERT OR IGNORE INTO post_likes(post,user) VALUES(?,?)", (int(req["id"]), ctx.uid))
                return ctx.send({"ok": True})
            if path == "/api/tool_add":
                if not ctx.is_owner:
                    return ctx.send({"error": "앱 등록은 관리자만 할 수 있습니다"}, code=403)
                return ctx.send(core.add_tool(req))
            t = core.tool(req.get("dir"))
            if not t:
                return ctx.send({"error": "unknown tool"}, code=404)
            if path in ("/api/open", "/api/like"):
                c.execute("INSERT INTO events VALUES(?,?,?,?)", (path[5:], t["dir"], core.now(), ctx.uid))
            elif path in ("/api/start", "/api/stop") and core.is_remote(t):
                return ctx.send({"error": "다른 컨테이너에서 항상 켜져 있는 도구입니다"}, code=409)
            elif path == "/api/start":
                if _alive_sync(core.tool_addr(t)) and not ctx.is_owner:
                    return ctx.send({"error": "이미 실행 중입니다"}, code=409)
                core.start(t); _ALIVE["t"] = 0.0   # 켜짐 캐시 비움 — 다음 /api/tools 가 바로 다시 확인
            elif path == "/api/stop":
                if not ctx.is_owner:
                    return ctx.send({"error": "도구 중지는 관리자만 할 수 있습니다"}, code=403)
                core.stop(t); _ALIVE["t"] = 0.0
            else:
                return ctx.send({"error": "not found"}, code=404)
            return ctx.send({"ok": True})
    except PermissionError as e:
        return ctx.send({"error": str(e)}, code=403)
    except (ValueError, KeyError, TypeError) as e:   # TypeError: 값 형식이 틀린 JSON(예: title 이 숫자) — portal.py 는 연결이 끊겼음
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다"}, code=400)


def _alive_sync(addr):
    import socket
    if not addr[0]:
        return False
    try:
        with socket.create_connection(addr, timeout=0.5):
            return True
    except OSError:
        return False


# ── 프록시 /t/<도구>/… ───────────────────────────────────────────────────
def _record(ctx, t0, status):
    """오래 걸린 도구 작업(1분 넘는 POST)이 끝나면 기록 → 파동이 펫이 '작업 끝났어요!' (/api/events)"""
    parts, sec = ctx.raw.split("/", 3), time.time() - t0
    if ctx.method == "POST" and sec > 60 and len(parts) > 2 and parts[2] != "padong-local":
        t = core.tool(parts[2])
        if t:
            EVENTS.append({"id": (EVENTS[-1]["id"] + 1) if EVENTS else int(t0), "dir": parts[2], "name": t.get("name", parts[2]), "user": ctx.agent_user,
                           "ok": status < 400, "sec": round(sec), "t": int(time.time())})
            del EVENTS[:-30]


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


REDIRECTS = (301, 302, 303, 307, 308)


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


async def proxy(ctx):
    t0, status = time.time(), [200]
    parts = ctx.raw.split("/", 3)
    t = core.tool(parts[2]) if len(parts) > 2 else None
    if not t:
        return ctx.send({"error": "unknown tool"}, code=404)
    if len(parts) < 4:
        return ctx.bare(302, [("Location", f"/t/{t['dir']}/")])
    rec = lambda: _record(ctx, t0, status[0])
    # 요청 본문: Content-Length 가 있으면 그 길이로 흘려 넘김(버퍼 없음), 청크면 상한까지 모아 길이를 붙여 넘김(stdlib 도구는 청크 본문을 못 읽음)
    try:
        n = _length(ctx)
    except ValueError:
        return ctx.send({"error": "Content-Length 가 틀립니다"}, code=400)
    chunked = n is None and "chunked" in (ctx.headers.get("transfer-encoding") or "").lower()
    content, length = None, None
    if n and n > MAX_BODY:
        await _drain(ctx.request, n); rec()
        return too_big(ctx, MAX_BODY)
    if n:
        async def body_iter():
            async for chunk in ctx.request.stream():
                yield chunk
        content, length = body_iter(), n
    elif chunked:
        try:
            content = await read_body(ctx, MAX_BODY)
        except TooBig:
            rec(); return too_big(ctx, MAX_BODY)
        length = len(content) or None
        content = content or None
    hd = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in ctx.request.headers.raw if k.decode("latin-1").lower() not in DROP_REQ]
    hd.append(("X-Agent-User", ctx.agent_user))
    ck = AG_COOKIE.sub("", ctx.cookie).strip("; ")
    if ck:
        hd.append(("Cookie", ck))
    hd.append(("Accept-Encoding", "identity"))   # 압축 안 받음(portal.py 의 urllib 와 같음) — 첫 화면 HTML 에 끼워 넣어야 하므로
    if length is not None:
        hd.append(("Content-Length", str(length)))
        if not any(k.lower() == "content-type" for k, _ in hd):
            hd.append(("Content-Type", "application/x-www-form-urlencoded"))   # urllib 기본값과 같게
    host = core.tool_host(t)
    if not host:   # tools.json 의 host 형식이 틀림 — 엉뚱한 주소로 보내지 않음(값은 응답에 안 넣음)
        print("proxy: tools.json host 형식 틀림:", t["dir"], flush=True)
        if n:
            await _drain(ctx.request, n)
        rec(); return ctx.send({"error": f"{t['dir']} 주소 설정이 틀립니다 — 관리자에게 알려 주세요"}, code=502)
    try:
        r = await _send(ctx.method, f"http://{host}:{t['port']}/{parts[3]}", hd, content)
    except Exception as e:   # 도구 꺼짐·연결 실패·시간 초과 — 내부 주소·스택은 내보내지 않음
        rec()
        return ctx.send({"error": f"{t['dir']} 응답 없음 ({_errname(e)}) — 포털에서 '시작'을 누르세요"}, code=502)
    status[0] = r.status_code
    out = [(b"set-cookie", ctx.setcookie.encode())] if ctx.setcookie else []
    out += [(k, v) for k, v in r.headers.raw if k.decode("latin-1").lower() not in HOP]
    inject = ctx.method == "GET" and core.first_html(parts[3]) and "text/html" in (r.headers.get("content-type") or "")
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


# ── 진입점: portal.py 의 do_GET·do_POST 와 같은 순서 ────────────────────────
def _501(method):
    """portal.py(BaseHTTPRequestHandler)는 GET·POST 말고는 501 — 같은 응답(HEAD 는 uvicorn 이 본문을 뺌)"""
    from http.server import BaseHTTPRequestHandler as B
    from http import HTTPStatus
    body = (B.error_message_format % {"code": 501, "message": f"Unsupported method ('{method}')", "explain": HTTPStatus(501).description}).encode("utf-8", "replace")
    return Raw(body, 501, [("Connection", "close"), ("Content-Type", B.error_content_type)])


async def handle(request: Request):
    ctx = Ctx(request)
    try:
        if ctx.method == "GET":
            r = await run_in_threadpool(get_sync, ctx)
            if r == "proxy":
                return await proxy(ctx)
            if r == "events":
                try:
                    since = int((ctx.raw.split("since=") + ["0"])[1].split("&")[0] or 0)
                except ValueError:
                    since = 0
                me = ctx.agent_user
                return ctx.send({"events": [e for e in EVENTS if e["id"] > since and me in ("owner", e["user"])], "last": EVENTS[-1]["id"] if EVENTS else since})
            if r == "tools":
                up = await alive_map([core.tool_addr(t) for t in core.tools()])
                return await run_in_threadpool(tools_sync, ctx, up)
            return r
        if ctx.method == "POST":
            if ctx.raw in ("/api/login", "/api/signup"):
                try:
                    body = await read_body(ctx, MAX_JSON)
                except TooBig:
                    return too_big(ctx, MAX_JSON)
                return await run_in_threadpool(login_sync, ctx, body)
            if not await run_in_threadpool(ctx.auth):
                n = _length(ctx) or 0
                await _drain(request, n)
                return ctx.need_login()
            if ctx.raw.startswith("/t/"):
                return await proxy(ctx)
            try:
                body = await read_body(ctx, MAX_JSON)
            except TooBig:
                return too_big(ctx, MAX_JSON)
            return await run_in_threadpool(post_sync, ctx, body)
        return _501(ctx.method)
    except ValueError:   # Content-Length 형식 오류 등
        return ctx.send({"error": "요청 형식이 틀립니다"}, code=400)
    except Exception as e:   # 예상 못 한 오류: 내부 정보(경로·스택)는 로그에만, 응답은 짧게
        print("error:", ctx.method, ctx.p, type(e).__name__, e, flush=True)
        return ctx.send({"error": "서버 오류"}, code=500)


app.add_api_route("/{_path:path}", handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], include_in_schema=False)
