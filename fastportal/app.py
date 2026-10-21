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
     AUTH_BACKEND(계정 저장소: local 기본 — 포털 내부 DB, 임시 | external — 사내 챗봇 계정 DB, auth/README.md | <모듈>:<클래스>)
     작업 알림 센터(jobs.py): PORTAL_JOBS_AUTO_S(자동 기록 기준 초, 60) PORTAL_JOBS_URL(도구에 넘길 내부 주소, 기본 http://127.0.0.1:PORT/api/internal/jobs)
     PORTAL_JOBS_SECRET(도구 토큰을 만드는 비밀값 — 없으면 _data/.portal_jobs_secret) PORTAL_JOBS_ALLOW(내부 경로를 더 받을 IP, 쉼표) PORTAL_JOBS_KEEPALIVE(SSE 빈 줄 초, 25)
     모두의 에이전트(flows.py·devsubmit.py): FLOW_RUN_MAX_BYTES(흐름 실행 본문 상한, 40MB) FLOW_MAX_RUNS(포털 전체 동시 흐름, 4)
     DEVSUBMIT_ZIP_MAX(개발자 zip 상한, 50MB) DEVCHECK_DOCKER_HOST(tcp://127.0.0.1:2375) DEVCHECK_IMAGE(dongjukim/agent-portal:v2) DEVCHECK_PYTHON DEVCHECK_TIMEOUT(180초)
     할 일 검색·물어보기(match.py·ask.py): MATCH_RATE_N·MATCH_EMBED_TIMEOUT·MATCH_QCACHE_S · ASK_KEEP_DAYS(90)·ASK_RATE_N(30/10분)·ASK_MAX_TOKENS(1024)·ASK_MAX_GLOBAL(4)·ASK_LLM_TIMEOUT
     F12 코드 수집·엿보기 막기: PORTAL_PROBE·PORTAL_PROBE_MAX·PORTAL_PROBE_WINDOW·PORTAL_PROBE_BLOCK_S·PORTAL_PROBE_BLOCK_MAX_S(probe.py) PORTAL_LEGACY_STRIP(proxy.py·strip.py) — 빌드 자산 권한은 webguard.py
     (모두 config.py 한곳에서 읽음 — 모두의 에이전트 값은 그 모듈 머리에서)
tools.json 의 "host": 그 도구는 다른 컨테이너·서버(http://<host>:<port>) — 상태 확인·프록시만, 시작·중지는 409"""
import asyncio, json, os, re, secrets, threading, time, urllib.parse

import anyio, anyio.to_thread
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool

from . import accounts, admintools, ask, auth, community, config, db, relay, devsubmit, dwell, flows, idea, jobs, lists, match, pages, pkgindex, probe, proxy, sessions, tools, toolstate, workspace
from .config import OWNERS, TLS
from .ctx import Ctx, TooBig, drain, length, not_implemented, parse_json, read_body, too_big

db.init_db(t.get("dept") for t in tools.tools())
with db.db() as _c:
    jobs.init(_c)   # 작업 알림 센터(jobs 표) — 예전 메모리 EVENTS 를 대체(재시작해도 남음)
    workspace.init(_c)
    lists.init(_c)   # 계정별 즐겨찾기·최근 사용
    flows.init(_c)   # 모두의 에이전트 — 에이전트 연결(흐름·실행 기록)
    devsubmit.init(_c)   # 모두의 에이전트 — 개발자 역할·제출
    community.init(_c)   # 모두의 에이전트 — 리뷰·개선 요청·투표
    pkgindex.init(_c)   # 폐쇄망 pip 패키지 목록(비어 있으면 _data/pkg-index/*.txt 에서)
    idea.init(_c)   # 모두의 에이전트 — 아이디어로 만들기(최근 아이디어)
    toolstate.init(_c)   # 관리자 도구 켜기·끄기(tool_state — 없으면 켜짐)
    ask.init(_c)   # 물어보기 — 계정별 대화 기록(보관 기한 지난 것 지움)
toolstate.write_file()   # 파동이 펫 등이 읽는 꺼진 도구 목록(DATA/tools-disabled.json)
flows.recover(); devsubmit.recover()
jobs.load_self_report()
_AB = auth.backend()   # 계정 저장소(AUTH_BACKEND) — 설정이 틀리면 여기서 멈춤
if not auth.is_local() and hasattr(_AB, "ready") and not _AB.ready():
    print(f"경고: AUTH_BACKEND={config.AUTH_BACKEND} 연결 설정이 없음 — 로그인이 503 으로 실패함(fastportal/auth/README.md)", flush=True)


# ── 스레드 풀 ───────────────────────────────────────────────────────────
# 화면·API 용 기본 풀(anyio 기본 40)은 PORTAL_THREADS(기본 4)로 줄임 — 1프로세스 + GIL 에서 스레드가 많으면 서로 GIL 을 다투느라
# 동시 16 이상에서 처리량이 1/6 로 떨어짐(_audit/loadtest-2026-10-20). 오래 걸리는 동기 일(아이디어 LLM·이력 가져오기·PIN 해시·
# 개발자 업로드·패키지 목록 올리기)은 따로 PORTAL_SLOW_THREADS(기본 16)개까지 — 화면용 풀을 막지 않게
THREADS = max(1, int(os.environ.get("PORTAL_THREADS") or 4))
SLOW_THREADS = max(1, int(os.environ.get("PORTAL_SLOW_THREADS") or 16))
_SLOW = None


def slow_limiter():
    global _SLOW
    if _SLOW is None:
        _SLOW = anyio.CapacityLimiter(SLOW_THREADS)
    return _SLOW


async def run_slow(fn, *args):
    """오래 걸리는 동기 일 — 따로 둔 풀에서(화면·API 풀과 나눔)"""
    return await anyio.to_thread.run_sync(fn, *args, limiter=slow_limiter())


SLOW_POST = re.compile(r"/api/(idea/|workspace/sync$|pin$|admin/reset$)")   # post_sync 중 오래 걸리는 것(LLM·도구 이력·PIN 해시)
SLOW_GET = re.compile(r"/api/workspace/[0-9a-f]{32}$|/api/match$")   # 작업 하나 열기(도구에서 상세 결과를 다시 읽음) · 할 일 검색(검색어 임베딩 — 최대 MATCH_EMBED_TIMEOUT 초)


@asynccontextmanager
async def lifespan(_app):
    jobs.HUB.loop = asyncio.get_running_loop()
    anyio.to_thread.current_default_thread_limiter().total_tokens = THREADS
    proxy.open_client()
    match.warm()   # 할 일 검색 — 할 일 벡터를 뒤에서 준비(_data/match/ 캐시가 있으면 바로)
    if config.BACKUP:
        threading.Thread(target=db.backup_loop, daemon=True).start()
    if config.ANON_AUTO:   # 비활성 계정 익명화 정리 — 기본 꺼짐(PORTAL_ANON_AUTO=1 로 켬, 기한 PORTAL_ANON_AFTER_DAYS)
        threading.Thread(target=accounts.anon_loop, daemon=True).start()
    print(f"fastportal → {'https' if TLS else 'http'} data={config.DATA} owners={sorted(OWNERS)} max_body={config.MAX_BODY} auth={_AB.name} backup={os.environ.get('BACKUP', '0')} "
          f"threads={THREADS}/{SLOW_THREADS} session_cache={sessions.CACHE_S}s tools_cache={tools.STATS_TTL}s  {pages._SIG}", flush=True)
    yield
    await proxy.close_client()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)   # /docs·/openapi.json 은 열지 않음(포털 주소 규칙 그대로)


# ── GET ─────────────────────────────────────────────────────────────────
KNOWN = re.compile(r"/($|api/|t/|guide(/|$)|account$|approvals$|admin$)")   # 로그인 뒤 GET 이 갈 수 있는 주소(그 밖은 404)


def get_sync(ctx):
    """막히는 일(DB·파일)을 하는 GET 전부 — 스레드 풀에서. 비동기로 할 일은 문자열 표시로 돌려줌('proxy'·'jobs_stream'·'tools')"""
    p = ctx.p
    if pages.web_path(p):
        return pages.web_file(ctx, p)
    if p == "/login":
        return pages.login_page(ctx)
    if p == config.LANDING_IMG[0] or p.startswith("/theme/"):
        return pages.theme_file(ctx, p)
    if p == "/api/auth":   # 계정 저장소가 가입·PIN 변경을 받는지(로그인 전에도)
        return ctx.send(auth_public())
    if p == "/api/depts":
        with db.db() as c:
            return ctx.send({"depts": accounts.visible_depts(c)})
    if p == "/api/version":   # 로그인 전에도 — 화면이 이 서버와 맞는 API 계약인지 확인(docs/api/README.md '버전')
        return ctx.send(version_info())
    if p == "/api/landing":   # 로그인 전 랜딩의 분류·도구 수(WEB_DIST 와 상관없이 늘)
        return pages.web_landing(ctx)
    if p == "/logout":   # 예전 화면용 GET 로그아웃. 다른 사이트에서 건 링크(Sec-Fetch-Site: cross-site)로는 로그아웃하지 않음 — 새 화면은 POST /api/logout
        if (ctx.headers.get("sec-fetch-site") or "").lower() == "cross-site":
            return ctx.bare(302, [("Location", "/")])
        sessions.end_session(ctx.cookie)
        return ctx.bare(302, [("Location", "/login"), ("Set-Cookie", sessions.cookie("", 0))])
    if not KNOWN.match(p):   # 포털에 없는 주소(/.env·/.git/HEAD·/config.py·/src/ 등)는 로그인 여부와 상관없이 404 — 로그인 화면으로 보내지 않음(있는지 알리지 않음)
        return ctx.send({"error": "not found"}, code=404)
    if not ctx.auth():
        return ctx.need_login()
    if p == "/api/me":
        with db.db() as c:
            return ctx.send(accounts.me(c, ctx.uid, ctx.uname))
    if p == "/api/workspace" or p.startswith("/api/workspace/"):
        return workspace.handle_get(ctx)
    if p == "/api/lists":   # 즐겨찾기·최근 사용(계정 저장)
        with db.db() as c:
            return ctx.send(lists.get(c, ctx.uid))
    if p == "/api/admin/dwell":
        return dwell.report(ctx)
    if p == "/api/admin/tools":   # 에이전트 관리 — 켜짐 확인(비동기) 뒤 표
        return "admin_tools"
    if p == "/api/admin/agents":
        return admintools.agents_get(ctx)
    if p == "/api/flows/adapters":
        return "flow_adapters"
    if p == "/api/flows" or p.startswith("/api/flows/"):   # 모두의 에이전트 — 에이전트 연결(내 흐름만)
        return flows.handle_get(ctx)
    if p.startswith("/api/idea/"):   # 아이디어로 만들기 — 최근 아이디어·저장된 결과(내 것만)
        return idea.handle_get(ctx)
    if p == "/api/match":   # 할 일로 도구 찾기(capabilities.json — 숨김 빼고, 팀 밸런스 맵은 관리자에게만)
        return match.handle(ctx)
    if p.startswith("/api/ask/"):   # 물어보기 — 내 대화 목록·대화 하나(남의 것은 404)
        return ask.handle_get(ctx)
    if p.startswith("/api/community/"):   # 리뷰·요청·기여 순위(볼 수 있는 대상만 — community.target)
        return community.handle_get(ctx)
    if p in ("/api/pkg-index", "/api/pkg-index/check"):   # 폐쇄망 pip 패키지 찾기·확인(로그인)
        return devsubmit.pkg_get(ctx)
    if p == "/api/dev/submissions" or p.startswith("/api/dev/submissions/"):   # 개발자 제출(개발자는 자기 것, 관리자는 전부)
        return devsubmit.handle_get(ctx)
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
        ok_ = (tools.tool(d) and (ctx.is_owner or not toolstate.user_hidden(tools.tool(d)))) or d == "portal"   # 숨긴·관리자가 끈 도구는 일반 계정에 빈 글
        return ctx.send(tools.guide(d).encode() if ok_ else b"", "text/plain; charset=utf-8")
    if p.startswith("/api/readme/"):
        d = p.split("/")[-1]
        f = os.path.join(config.BASE, d, "README.md") if tools.tool(d) and (ctx.is_owner or not toolstate.user_hidden(tools.tool(d))) else ""
        return ctx.send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
    if p == "/guide" or p.startswith("/guide/"):
        return pages.guide_file(ctx, p)
    if p != "/":
        return ctx.send({"error": "not found"}, code=404)
    return pages.home(ctx)


_VER = {}


def version_info():
    """GET /api/version — api: 계약 버전(config.API_VERSION, 응답 모양이 바뀌면 올림), build: 포털 저장소 git describe(시작 때 한 번)"""
    if "build" not in _VER:
        import subprocess
        try:
            _VER["build"] = subprocess.run(["git", "-C", config.ROOT, "describe", "--tags", "--always", "--dirty"], capture_output=True, text=True, timeout=3).stdout.strip() or None
        except Exception:
            _VER["build"] = None
        _VER["build"] = os.environ.get("PORTAL_BUILD") or _VER["build"]
    return {"api": config.API_VERSION, "build": _VER["build"], "auth": auth_public(), "cors": bool(config.CORS_ORIGINS)}


def tools_sync(ctx, up):
    with db.db() as c:
        return ctx.send({**tools.home_data(c, up, ctx.uid), "me": accounts.me(c, ctx.uid, ctx.uname)})


# ── POST ────────────────────────────────────────────────────────────────
class Busy(Exception):
    pass


_HASH_SLOTS = threading.BoundedSemaphore(config.LOGIN_SLOTS)   # PIN 해시 동시 계산 수(로그인 폭주가 스레드 풀을 다 잡지 않게)
_SIGNUPS = {}   # 접속지 → 최근 가입 시각들(한 시간에 SIGNUP_PER_IP 번까지). shortcut: 프로세스 메모리 — 재시작하면 0, 포털이 여럿이면 그만큼 느슨
SIGNUP_PER_IP = 5


def _signup_ok(ip):
    t = time.time()
    v = [x for x in _SIGNUPS.get(ip, ()) if x > t - 3600]
    _SIGNUPS[ip] = v
    return len(v) < SIGNUP_PER_IP


def login_sync(ctx, body):
    """POST /api/login · /api/signup — 아이디·PIN 확인은 계정 저장소(auth.backend()), 세션·잠금은 포털(sessions).
    오류에는 code(bad_request·bad_credentials·locked·ip_locked·busy·unsupported·unavailable)를 같이 줌 — 화면이 문구가 아니라 code 로 구분"""
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다", "code": "bad_request"}, code=400)
    if not _HASH_SLOTS.acquire(timeout=10):
        return ctx.send({"error": "로그인 요청이 몰려 있습니다 — 잠시 뒤 다시 해 주세요", "code": "busy"}, code=429, headers=[("Retry-After", "5")])
    try:
        return _login(ctx, req)
    finally:
        _HASH_SLOTS.release()


def _login(ctx, req):
    b = auth.backend()
    ip = ctx.client or ""
    uid, pin = b.normalize_id(str(req.get("id", ""))), str(req.get("pin", ""))
    try:
        with db.db() as c:
            if sessions.ip_limited(c, ip):
                return ctx.send({"error": "이 접속지에서 로그인을 너무 많이 틀렸습니다. 10분 뒤에 다시 해 주세요", "code": "ip_locked"}, code=429, headers=[("Retry-After", "600")])
            if ctx.p == "/api/signup":
                if not b.supports_signup:
                    raise auth.Unsupported(b.guide)
                if not _signup_ok(ip) and not ctx.local_owner():
                    return ctx.send({"error": "이 접속지에서 가입을 너무 많이 했습니다. 한 시간 뒤에 다시 해 주세요", "code": "rate_limited"}, code=429, headers=[("Retry-After", "3600")])
                b.check_new(c, uid, str(req.get("name", "")), pin, ctx.local_owner())   # 관리자 아이디 가입은 진짜 로컬(토큰)에서만
                d = str(req.get("dept") or "")
                if d and d not in accounts.visible_depts(c):
                    raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
                b.create_user(c, uid, str(req["name"]).strip()[:40], pin)
                if d:
                    accounts.new_request(c, uid, "dept_join", d)
                _SIGNUPS.setdefault(ip, []).append(time.time())
                accounts.audit(c, uid, "signup", uid, ip)
            else:
                if sessions.limited(c, uid, ip):
                    return ctx.send({"error": "PIN 을 여러 번 틀렸습니다. 10분 뒤에 다시 해 주세요", "code": "locked"}, code=429, headers=[("Retry-After", "600")])
                local = auth.is_local()
                user = None if (uid == "owner" and not local) else b.verify(c, uid, pin)   # owner 는 로컬 주인 자리(외부 아이디로 못 씀)
                if user and not local and not accounts.ensure_profile(c, user):   # 포털에서 비활성·정지한 계정
                    user = None
                if not user:
                    sessions.add_fail(uid, ip)
                    accounts.audit(c, uid, "login_fail", uid, ip)
                    return ctx.send({"error": "아이디 또는 PIN 이 틀립니다 (정지된 계정이면 관리자에게)", "code": "bad_credentials"}, code=400)
                sessions.clear_fails(c, uid, ip)
                accounts.audit(c, user.id, "login", user.id, ip)
                uid = user.id   # 저장소가 정한 아이디(local 은 같음)
            keep = ctx.p == "/api/signup" or sessions.want_remember(req.get("remember", True))
            tok = sessions.new_session(c, uid, keep)
        return ctx.send({"ok": True, "id": uid}, headers=[("Set-Cookie", sessions.cookie(tok, persist=keep))])
    except auth.Unsupported as e:
        return ctx.send({"error": str(e), "auth": auth_public(), "code": "unsupported"}, code=403)
    except auth.AuthError as e:   # 외부 저장소 장애·미설정 — 상세는 로그에만
        print("auth:", type(e).__name__, e, flush=True)
        return ctx.send({"error": str(e), "code": "unavailable"}, code=503)
    except (ValueError, KeyError) as e:
        return ctx.send({"error": str(e), "code": "bad_request"}, code=400)


def auth_public():
    """GET /api/auth(로그인 전) — 화면이 가입·PIN 변경을 보일지 정함. 저장소 이름·주소는 안 내보냄(관리자는 /api/admin 의 auth_backend)"""
    i = auth.backend().info()
    return {"signup": i["signup"], "password_change": i["password_change"], "guide": i["guide"]}


def change_pin(ctx, c, req):
    """POST /api/pin — 지금 PIN 확인(잠금 포함) → 새 PIN, 내 세션 모두 끊고 새 세션(로그인 유지 여부는 그대로)"""
    if ctx.uid == "owner":
        raise ValueError("로컬 접속은 계정이 없습니다 — 로그인 후 바꾸세요")
    b = auth.backend()
    if not b.supports_password_change:
        raise auth.Unsupported(b.guide)
    if sessions.limited(c, ctx.uid, ctx.client or "") or not b.verify(c, ctx.uid, req.get("pin", "")):
        sessions.add_fail(ctx.uid, ctx.client or "")
        raise ValueError("지금 PIN 이 틀립니다 (10분에 5번까지)")
    b.check_secret_rule(req.get("new", ""))
    b.set_password(c, ctx.uid, req["new"])
    keep = not sessions.is_short(c, ctx.cookie)
    sessions.drop_user_sessions(c, ctx.uid)
    tok = sessions.new_session(c, ctx.uid, keep)
    return ctx.send({"ok": True}, headers=[("Set-Cookie", sessions.cookie(tok, persist=keep))])


def tool_action(ctx, c, path, req):
    """POST /api/open · /api/like · /api/start · /api/stop {dir}"""
    t = tools.tool(req.get("dir"))
    if not t:
        return ctx.send({"error": "unknown tool"}, code=404)
    if toolstate.is_off(t["dir"]) and not ctx.is_owner and path in ("/api/open", "/api/like", "/api/start"):   # 관리자가 끈 도구
        return ctx.send({"error": toolstate.MSG, "code": toolstate.CODE}, code=403)
    if path == "/api/like" and c.execute("SELECT 1 FROM events WHERE kind='like' AND dir=? AND user=?", (t["dir"], ctx.uid)).fetchone():
        return ctx.send({"ok": True, "liked": True, "already": True})   # 좋아요는 1인 1회(예전엔 누를 때마다 쌓였음)
    if path in ("/api/open", "/api/like"):
        c.execute("INSERT INTO events VALUES(?,?,?,?)", (path[5:], t["dir"], db.now(), ctx.uid))
    elif path in ("/api/start", "/api/stop") and tools.is_remote(t):
        return ctx.send({"error": "다른 컨테이너에서 항상 켜져 있는 도구입니다"}, code=409)
    elif path == "/api/start":
        if t.get("hidden") and not ctx.is_owner:   # 숨긴 도구는 목록에도 없음 — 일반 사용자는 못 켬
            return ctx.send({"error": "unknown tool"}, code=404)
        if tools.alive_sync(tools.tool_addr(t)) and not ctx.is_owner:
            return ctx.send({"error": "이미 실행 중입니다"}, code=409)
        if not tools.claim_start(t) and not ctx.is_owner:   # 켜지는 중에 또 누르면 여러 벌이 뜸(관리자는 바로 다시 띄울 수 있음)
            return ctx.send({"error": "켜는 중입니다 — 잠시 뒤 다시 확인하세요", "code": "starting"}, code=409)
        tools.start(t, jobs.tool_env(t)); tools.alive_reset()   # 켜짐 캐시 비움 — 다음 /api/tools 가 바로 다시 확인
        accounts.audit(c, ctx.uid, "tool_start", t["dir"])
    elif path == "/api/stop":
        if not ctx.is_owner:
            return ctx.send({"error": "도구 중지는 관리자만 할 수 있습니다"}, code=403)
        tools.stop(t); tools.alive_reset()
        accounts.audit(c, ctx.uid, "tool_stop", t["dir"])
    else:
        return ctx.send({"error": "not found"}, code=404)
    return ctx.send({"ok": True})


def post_sync(ctx, body):
    try:
        req = parse_json(body)
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    path = ctx.raw   # portal.py 처럼 쿼리까지 포함한 경로로 판정
    if ctx.p.startswith("/api/workspace/"):
        return workspace.handle_post(ctx, req)
    if ctx.p == "/api/admin/tool":   # 에이전트 관리 — 도구 켜기·끄기(자기 DB 연결로 커밋한 뒤 캐시 비움)
        return admintools.tool_post(ctx, req)
    if ctx.p == "/api/admin/agent":   # 에이전트 관리 — 공유 빌더 에이전트 끄기·켜기(빌더 호출 — DB 연결을 잡지 않음)
        return admintools.agent_post(ctx, req)
    if ctx.p.startswith("/api/idea/"):   # 아이디어로 만들기(LLM 이 수십 초 — DB 연결을 잡지 않고 idea 가 필요할 때만 엶)
        return idea.handle_post(ctx, req)
    if ctx.p.startswith("/api/ask/"):   # 물어보기 — 대화 이름 바꾸기·지우기·모두 지우기(보내기는 route 에서 스트림으로)
        return ask.handle_post(ctx, req)
    try:
        with db.db() as c:
            if path == "/api/pin":
                return change_pin(ctx, c, req)
            if path == "/api/lists":
                return ctx.send(lists.put(c, ctx.uid, req))
            if path == "/api/dwell":
                return dwell.record(ctx, c, req)
            if ctx.p.startswith("/api/flows/"):
                return flows.handle_post(ctx, req)
            if ctx.p.startswith("/api/community/"):
                return community.handle_post(ctx, req)
            if ctx.p in ("/api/admin/dev/decide", "/api/admin/developer"):
                return devsubmit.handle_post(ctx, req)
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
                r = tools.add_tool(req)
                accounts.audit(c, ctx.uid, "tool_add", str(req.get("dir", "")))
                return ctx.send(r)
            return tool_action(ctx, c, path, req)
    except PermissionError as e:   # auth.Unsupported(외부 저장소에서 PIN 변경·초기화) 포함
        return ctx.send({"error": str(e)}, code=403)
    except auth.AuthError as e:
        print("auth:", type(e).__name__, e, flush=True)
        return ctx.send({"error": str(e)}, code=503)
    except (ValueError, KeyError, TypeError) as e:   # TypeError: 값 형식이 틀린 JSON(예: title 이 숫자) — portal.py 는 연결이 끊겼음
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다"}, code=400)
    finally:
        if path in STATS_PATHS:   # 커밋한 뒤 /api/tools 집계 캐시 비움(다음 홈 화면이 바로 새 수를 봄)
            tools.stats_reset()


STATS_PATHS = ("/api/open", "/api/like", "/api/post", "/api/post_like")


# ── 진입점 ──────────────────────────────────────────────────────────────
SAFE = ("GET", "HEAD", "OPTIONS")
METHODS = "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"


def _netloc(url):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    return u.netloc.lower() if u.scheme in ("http", "https") and u.netloc else None


def csrf_ok(ctx):
    """상태를 바꾸는 요청(POST·PUT·DELETE·PATCH)이 우리 화면에서 왔는지 — 요청 위조(CSRF) 방지.
    브라우저는 이런 요청에 Origin 을 붙임: 요청의 Host 와 같은 출처이거나 config.ALLOWED_ORIGINS 면 통과, 'null'(sandbox·파일)·다른 출처는 거부.
    Origin 이 없으면 Referer 로, 그것도 없으면 Sec-Fetch-Site 가 cross-site 일 때만 거부(curl·스크립트·도구는 이 헤더들을 안 붙임 → 통과)"""
    if ctx.method in SAFE:
        return True
    host = (ctx.headers.get("host") or "").lower()
    origin = ctx.headers.get("origin")
    if origin is not None:
        return origin.rstrip("/") in config.ALLOWED_ORIGINS or (_netloc(origin) or "\0") == host
    ref = ctx.headers.get("referer")
    if ref:
        n = _netloc(ref)
        return n == host or any(n == _netloc(o) for o in config.ALLOWED_ORIGINS)
    return (ctx.headers.get("sec-fetch-site") or "").lower() != "cross-site"


def cors_headers(ctx):
    """다른 출처 화면(config.CORS_ORIGINS)에만 CORS 머리 — 쿠키 포함(credentials). 목록이 비면 아무것도 안 붙음(같은 출처만)"""
    o = (ctx.headers.get("origin") or "").rstrip("/")
    if not o or o not in config.CORS_ORIGINS:
        return []
    return [(b"access-control-allow-origin", o.encode()), (b"access-control-allow-credentials", b"true"), (b"vary", b"Origin"),
            (b"access-control-expose-headers", b"Content-Disposition, Content-Length, Retry-After, X-Author")]


def preflight(ctx):
    """OPTIONS: 허용한 다른 출처면 CORS 사전 요청 응답, 아니면 허용 메서드만(브라우저는 CORS 머리가 없으니 막음)"""
    hd = [("Allow", METHODS)]
    if cors_headers(ctx):
        want = ctx.headers.get("access-control-request-headers") or ""
        ok = ", ".join(h for h in (x.strip() for x in want.split(",")) if h and h.lower() not in ("cookie", "x-agent-user", "x-agent-auth", "x-local-owner"))
        hd += [("Access-Control-Allow-Methods", METHODS), ("Access-Control-Max-Age", "600")] + ([("Access-Control-Allow-Headers", ok)] if ok else [])
    return ctx.bare(204, hd)


async def _unauth(ctx, request):
    """로그인 안 된 POST·PUT 등: 작은 본문만 읽어 버리고 401, 크거나 길이를 모르면 읽지 않고 연결을 닫음(느린 업로드로 붙잡지 못하게)"""
    n = length(ctx)
    r = ctx.need_login()
    if n is not None and n <= 64 * 1024:
        await drain(request, n)
    else:
        r.raw_headers.append((b"connection", b"close"))
    return r


async def route(ctx, request):
    if ctx.method == "OPTIONS":
        return preflight(ctx)
    if not csrf_ok(ctx):
        return ctx.send({"error": "다른 사이트에서 온 요청은 받지 않습니다", "code": "csrf"}, code=403)
    if ctx.method in ("GET", "HEAD"):
        r = await (run_slow if SLOW_GET.match(ctx.p) else run_in_threadpool)(get_sync, ctx)
        if r == "proxy":
            return await proxy.proxy(ctx)
        if r == "jobs_stream":
            return jobs.stream(ctx)
        if r == "tools":
            up = await tools.alive_map([tools.tool_addr(t) for t in tools.tools()])
            return await run_in_threadpool(tools_sync, ctx, up)
        if r == "admin_tools":
            up = await tools.alive_map([tools.tool_addr(t) for t in tools.tools()])
            return await run_in_threadpool(admintools.tools_get, ctx, up)
        if r == "flow_adapters":
            up = await tools.alive_map([tools.tool_addr(t) for t in tools.tools()])
            return await run_in_threadpool(flows.handle_get, ctx, up)
        return r
    if ctx.method == "POST":
        if ctx.raw == "/api/internal/jobs":   # 도구 → 포털(쿠키 없음, 접속지 + 도구 토큰)
            try:
                body = await read_body(ctx, 64 * 1024)
            except TooBig:
                return too_big(ctx, 64 * 1024)
            return await run_in_threadpool(jobs.internal_post, ctx, body)
        if ctx.raw == "/api/internal/match":   # 도구 → 포털 할 일 검색(쿠키 없음, 접속지 + 도구 토큰 — 파동이 펫 추천)
            try:
                body = await read_body(ctx, 8 * 1024)
            except TooBig:
                return too_big(ctx, 8 * 1024)
            return await run_slow(match.internal_post, ctx, body)
        if ctx.raw in ("/api/login", "/api/signup"):
            try:
                body = await read_body(ctx, config.MAX_JSON)
            except TooBig:
                return too_big(ctx, config.MAX_JSON)
            return await run_slow(login_sync, ctx, body)   # PIN 해시(PBKDF2 60만 번)
        if ctx.raw == "/api/logout":   # JSON 로그아웃(로그인 안 돼 있어도 200 — 쿠키를 지움)
            await drain(request, length(ctx) or 0)
            await run_in_threadpool(sessions.end_session, ctx.cookie)
            return ctx.send({"ok": True}, headers=[("Set-Cookie", sessions.cookie("", 0))])
    if ctx.method in ("POST", "PUT", "DELETE", "PATCH"):
        if not await run_in_threadpool(ctx.auth):
            return await _unauth(ctx, request)
        if ctx.raw.startswith("/t/"):
            return await proxy.proxy(ctx)
        if ctx.method != "POST":   # 포털 자체 API 는 GET·POST 만
            await drain(request, length(ctx) or 0)
            return ctx.send({"error": "이 주소는 GET·POST 만 받습니다", "code": "method_not_allowed"}, code=405, headers=[("Allow", "GET, POST")])
        if ctx.raw == "/api/dev/submit":   # 개발자 업로드(본문 = zip, 상한 devsubmit.ZIP_MAX) — 권한을 본문보다 먼저 확인(아니면 본문을 읽지 않고 403)
            deny = await run_in_threadpool(devsubmit.precheck, ctx)
            if deny is not None:
                return deny
            try:
                body = await read_body(ctx, devsubmit.ZIP_MAX)
            except TooBig:
                return too_big(ctx, devsubmit.ZIP_MAX)
            return await run_slow(devsubmit.handle_submit, ctx, body)
        if ctx.p == "/api/admin/pkg-index":   # 폐쇄망 패키지 목록 파일 올리기(본문 = 글자, 8MB) — 관리자 확인을 본문보다 먼저
            if not ctx.is_owner:
                return ctx.send({"error": "관리자만 할 수 있습니다", "code": "forbidden"}, code=403)
            try:
                body = await read_body(ctx, devsubmit.PKG_LIST_MAX)
            except TooBig:
                return too_big(ctx, devsubmit.PKG_LIST_MAX)
            return await run_slow(devsubmit.pkg_upload, ctx, body)
        if ctx.raw == "/api/ask/send":   # 물어보기 — 답을 SSE 로(비동기 httpx, 사용자당 동시 1·10분 ASK_RATE_N 번). 본문은 글 + 파일 정보만(64KB)
            try:
                body = await read_body(ctx, 64 * 1024)
            except TooBig:
                return too_big(ctx, 64 * 1024)
            return ask.send_stream(ctx, body)
        if ctx.raw == "/api/flows/run":   # 흐름 실행(파일 입력 base64 포함 — 상한 flows.RUN_MAX). 이미 실행 중이면 본문(40MB)을 읽기 전에 400(start_run 과 같은 문구)
            busy = flows.busy(ctx.uid)
            if busy:
                return ctx.send({"error": busy, "code": "bad_request"}, code=400)
            try:
                body = await read_body(ctx, flows.RUN_MAX)
            except TooBig:
                return too_big(ctx, flows.RUN_MAX)
            return await run_in_threadpool(post_sync, ctx, body)
        try:
            body = await read_body(ctx, config.MAX_JSON)
        except TooBig:
            return too_big(ctx, config.MAX_JSON)
        return await (run_slow if SLOW_POST.match(ctx.p) else run_in_threadpool)(post_sync, ctx, body)
    return not_implemented(ctx.method)


# ── 엿보기 탐지(probe.py) ───────────────────────────────────────────────
STATIC_EXT = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|css|js|mjs|woff2?|ttf|mp3|wav|ogg|mp4|webm|pdf|glb|gltf)$", re.I)


def _probe_exempt(ctx):
    """상태 확인·브라우저 자동 요청·이 서버 안의 로컬 주인 토큰 요청은 세지도 막지도 않음"""
    if probe.exempt_path(ctx.p):
        return True
    h = ctx.headers.get("x-local-owner")
    return bool(h) and sessions.local_owner(ctx.client, h)


def _probe_user(ctx):
    """세는 단위의 계정 — 경로가 아직 계정을 안 봤으면(로그인 전 404 등) 쿠키로 확인(거절 응답·차단 IP 일 때만 부름)"""
    if ctx.uid is None and sessions.SID_RE.search(ctx.cookie):
        ctx.auth()
    return ctx.uid or ""


def _probe_alert(ctx, user, hit):
    """차단을 새로 걸었을 때: 감사 기록 + 관리자 알림함(작업 알림 'security' — 링크는 관리 화면)"""
    sec, total, distinct, sample = hit
    ip = ctx.client or "?"
    detail = f"ip={ip} 계정={user or '(로그인 안 함)'} · {int(probe.WINDOW)}초에 거절 {total}번·다른 주소 {distinct}개 → {sec}초 차단 · 예: {', '.join(sample)}"[:1000]
    print("probe:", detail, flush=True)
    try:
        with db.db() as c:
            accounts.audit(c, user or "-", "probe_block", ip, detail)
        j = jobs.upsert("owner", "security", "failed", f"probe-{int(time.time())}-{secrets.token_hex(3)}",
                        f"의심 요청 차단: {user or '로그인 안 함'} ({ip})", "/admin", detail[:500])
        if j:
            jobs.HUB.publish("job", j, j["user"])
    except Exception as e:
        print("probe: 기록 실패", type(e).__name__, e, flush=True)


def _blocked_response(ctx, left):
    r = ctx.send({"error": f"짧은 시간에 없는·권한 없는 주소 요청이 너무 많아 잠시 막았습니다. {left}초 뒤에 다시 해 주세요",
                  "code": "probe_blocked", "retry_after": left}, code=429, headers=[("Retry-After", str(left)), ("Cache-Control", "no-store")])
    if ctx.method in ("POST", "PUT", "PATCH", "DELETE"):
        r.raw_headers.append((b"connection", b"close"))   # 본문은 읽지 않고 끊음
    return r


def _relay_audit(bad, ctx):
    uid, name = bad
    print(f"relay: 다른 계정(uid {uid} {name})의 중계 요청 거절 — {ctx.method} {ctx.p}", flush=True)
    with db.db() as c:
        h = ctx.headers
        hint = f" · ua={(h.get('user-agent') or '-')[:40]} · sfs={h.get('sec-fetch-site') or '-'} · ref={'있음' if h.get('referer') else '-'} · xff={'있음' if h.get('x-forwarded-for') else '-'}"
        accounts.audit(c, "system", "relay_block", f"uid {uid} ({name})", (f"{ctx.method} {ctx.p}" + hint)[:300])


async def handle(request: Request):
    ctx = Ctx(request)
    bad = relay.check(request)   # 이 서버의 다른 OS 계정 프로그램이 대신 부르는 중계 → 거절(relay.py)
    if bad:
        if relay.should_log(bad[0]):
            await run_in_threadpool(_relay_audit, bad, ctx)
        return ctx.send({"error": "이 서버의 다른 프로그램을 거친 요청(중계)은 받지 않습니다 — 포털 주소로 직접 접속하세요", "code": "relay_blocked"}, code=403)
    exempt = not probe.ENABLED or _probe_exempt(ctx)
    r = None
    if not exempt and probe.ip_has_block(ctx.client or ""):   # 이 IP 에 차단이 있을 때만 계정을 봄(평소엔 딕셔너리 한 번)
        left = probe.blocked(ctx.client or "", await run_in_threadpool(_probe_user, ctx))
        if left:
            r = _blocked_response(ctx, left)
    if r is None:
        try:
            r = await route(ctx, request)
        except ValueError:   # Content-Length 형식 오류 등
            r = ctx.send({"error": "요청 형식이 틀립니다", "code": "bad_request"}, code=400)
        except Exception as e:   # 예상 못 한 오류: 내부 정보(경로·스택)는 로그에만, 응답은 짧게
            print("error:", ctx.method, ctx.p, type(e).__name__, e, flush=True)
            r = ctx.send({"error": "서버 오류", "code": "server_error"}, code=500)
        st = getattr(r, "status_code", 200)
        if not exempt and probe.is_counted(st) and not (st == 404 and ctx.p.startswith("/t/") and STATIC_EXT.search(ctx.p)):   # 도구 화면의 없는 그림·글꼴은 안 셈
            user = await run_in_threadpool(_probe_user, ctx)
            hit = probe.record(ctx.client or "", user, ctx.method, ctx.p)
            if hit:
                await run_in_threadpool(_probe_alert, ctx, user, hit)
    extra = cors_headers(ctx)
    if extra:
        r.raw_headers = [(k, v) for k, v in r.raw_headers if k.lower() not in (b"access-control-allow-origin", b"vary")] + extra
    try:
        r.raw_headers.append((b"x-robots-tag", b"noindex, nofollow"))   # 검색·수집기 색인 안 함(내부 포털)
    except AttributeError:
        pass
    return r


app.add_api_route("/{_path:path}", handle, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], include_in_schema=False)
