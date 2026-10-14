"""설정값·환경변수 한곳 — 다른 모듈은 여기서 읽기만 한다(import 할 때 한 번 정해짐).
portal.py 와 같은 이름·기본값을 따르고, 새 포털에만 있는 값은 주석에 적어 둔다. 비밀값은 여기 적지 않음(파일·환경변수에서만)."""
import os, re, sys

# ── 경로 ─────────────────────────────────────────────────────────────────
ROOT = os.environ.get("PORTAL_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-page-portal (시험: 임시 사본)
BASE = os.path.dirname(ROOT)                                          # 도구들을 나란히 clone 한 곳
DATA = os.environ.get("AGENT_DATA") or os.path.join(BASE, "_data")
os.makedirs(DATA, exist_ok=True)
DB_FILE = os.path.join(DATA, "portal.db")
TOOLS_FILE = os.path.join(ROOT, "tools.json")
GUIDE_DIR = os.path.join(ROOT, "guide")
THEME_DIR = os.path.join(ROOT, "theme")
V2_DIR = os.path.join(ROOT, "v2")
LANDING_IMG = ("/landing/home.png", os.path.join(ROOT, "docs", "img", "landing-home.png"))
THEMES = {f"{a}-{b}" for a in ("classic", "trendy", "v2") for b in ("light", "dark", "auto")}
SHOW_HIDDEN = os.environ.get("SHOW_HIDDEN") == "1"

# ── 사람·접속 ───────────────────────────────────────────────────────────
OWNERS = {x.strip() for x in os.environ.get("AGENT_OWNERS", "dongju,gggg8657").split(",") if x.strip()}
LOCAL = ("127.0.0.1", "::1")
# 로컬 주인(로그인 없이 관리자) 판정 — PORTAL_LOCAL_OWNER:
#   token(기본) 이 서버 안(127.0.0.1/::1)에서 쿠키 없이 + 헤더 X-Local-Owner 가 _data/.local_owner_token 과 같을 때만 주인.
#               브라우저(SSH 포트포워딩 localhost:8700 포함)는 이 헤더를 못 붙이므로 늘 로그인 필요 — 로그아웃이 제대로 됨.
#               스크립트: curl -H "X-Local-Owner: $(cat ~/agent-page/_data/.local_owner_token)" …   (토큰 파일은 처음 기동 때 만듦, 권한 600)
#   1           예전 방식(portal.py 와 같음): 127.0.0.1 쿠키 없음 = 주인 — 포트포워딩으로 붙은 브라우저도 로그아웃 뒤 주인이 되는 문제가 있음
#   0           끔(로컬 관리 작업도 관리자 계정으로 로그인)
_LO = (os.environ.get("PORTAL_LOCAL_OWNER") or "token").strip().lower()
LOCAL_OWNER_MODE = "0" if _LO in ("0", "off", "no", "false") else "1" if _LO in ("1", "on", "yes", "true") else "token"
LOCAL_OWNER = LOCAL_OWNER_MODE != "0"
LOCAL_TOKEN_FILE = os.path.join(DATA, ".local_owner_token")
# 믿을 프록시 주소(쉼표). 바로 앞 접속이 이 목록일 때만 X-Forwarded-For 를 읽어 실제 클라이언트 주소로 판정(오른쪽부터 믿을 프록시가 아닌 첫 주소).
# 목록의 프록시를 거쳤는데 X-Forwarded-For 가 없으면 '알 수 없음'(로컬 아님). 목록이 비면 X-Forwarded-For 는 무시(portal.py 와 같음)
TRUSTED_PROXIES = {x.strip() for x in os.environ.get("TRUSTED_PROXIES", "").split(",") if x.strip()}
TLS = bool(os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"))

# ── 계정 저장소(auth/) ──────────────────────────────────────────────────
# AUTH_BACKEND: local(기본 — 포털 내부 DB portal.db 의 users, 임시) | external(챗봇 계정 DB/API — auth/external.py, 아직 틀만)
#               | <모듈>:<클래스>(AuthBackend 를 이은 클래스 — 시험·사내 연동용). 자세히는 fastportal/auth/README.md
AUTH_BACKEND = (os.environ.get("AUTH_BACKEND") or "local").strip()

# ── 세션·로그인 ─────────────────────────────────────────────────────────
SESSION_S, SHORT_S = 30 * 86400, 12 * 3600          # 로그인 유지 30일 · 유지 끔 12시간
FAIL_WINDOW, FAIL_MAX = 600, 5                       # 10분에 5번 틀리면 잠금

# ── 비활성 계정 익명화 정리 ──────────────────────────────────────────────
ANON_AFTER_DAYS = int(os.environ.get("PORTAL_ANON_AFTER_DAYS") or 365)
ANON_AUTO = os.environ.get("PORTAL_ANON_AUTO", "0") == "1"

# ── 도구 시작·상태 ──────────────────────────────────────────────────────
# venv 없는 도구(saju-local)를 띄울 파이썬: portal.py 는 자기 파이썬(sys.executable)을 썼다. 새 포털은 fastportal 전용 venv 에서
# 돌므로 그 venv 의 바탕 파이썬(= portal.py 를 돌리던 시스템 python3)을 쓴다. TOOL_PYTHON 으로 바꿀 수 있음
TOOL_PY = os.environ.get("TOOL_PYTHON") or getattr(sys, "_base_executable", None) or sys.executable
TOOL_BIND = os.environ.get("TOOL_BIND") or "127.0.0.1"   # 도구가 열 주소(BIND). 컨테이너에선 0.0.0.0(다른 컨테이너의 포털이 붙게)
ALIVE_TTL = float(os.environ.get("TOOLS_ALIVE_TTL", "5"))  # 도구 켜짐 확인 캐시(초)

# ── 본문·프록시 ─────────────────────────────────────────────────────────
MAX_BODY = int(os.environ.get("PORTAL_MAX_BODY", str(600 * 1024 * 1024)))   # 보안 설계서 '포털 업로드 상한' 제안값(meeting 음성 500MB 포함)
MAX_JSON = int(os.environ.get("PORTAL_MAX_JSON", str(1024 * 1024)))
DRAIN_MAX = 4 * 1024 * 1024 * 1024   # 이보다 큰 본문은 다 읽어 버리지 않고 바로 끊음
PROXY_TIMEOUT = 3600
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length", "accept-encoding"}

# ── 작업 알림(jobs.py) ──────────────────────────────────────────────────
JOBS_KEEPALIVE = float(os.environ.get("PORTAL_JOBS_KEEPALIVE", "25"))   # SSE 빈 줄(주석) 간격 — 중간 프록시가 끊지 않게

# ── 백업 ────────────────────────────────────────────────────────────────
BACKUP = os.environ.get("BACKUP", "0") == "1"        # 매일 백업 — 기본 꺼짐(운영 :8700 만 start-portal.sh 가 켬)
BACKUP_DIR = os.environ.get("AGENT_BACKUP_DIR") or os.path.expanduser("~/agent-page-backup")

# ── React 화면(agent-page-web 빌드) 같은 출처로 ─────────────────────────
# WEB_DIST(빌드 폴더)가 있으면 WEB_PREFIX(기본 /app) 아래에서 dist 를 내보냄 — 자세한 규칙은 pages.py web_file
WEB_DIST = os.environ.get("WEB_DIST", "")   # 심볼릭 링크면 요청마다 따라감(배포 = 링크를 새 빌드로 바꿔 끼우기 — 포털 재시작 없이, agent-page-web scripts/deploy.sh)
WEB_PREFIX = "/" + os.environ.get("WEB_PREFIX", "/app").strip("/")
if WEB_DIST and (WEB_PREFIX == "/" or not re.fullmatch(r"(/[A-Za-z0-9_-]+)+", WEB_PREFIX)):
    raise SystemExit(f"WEB_PREFIX 는 /app 처럼 하위 경로여야 합니다(지금 {WEB_PREFIX!r}) — '/' 는 WEB_ROOT=1·WEB_ROOT_DIST 로")
# '/' 전환(준비만 — 기본 꺼짐): WEB_ROOT=1 이면 포털 화면 주소를 WEB_ROOT_DIST(WEB_BASE=/ 로 만든 빌드)에서 내보낸다. 되돌리기: WEB_ROOT 를 지우고 재시작
WEB_ROOT = os.environ.get("WEB_ROOT", "0") == "1"
WEB_ROOT_DIST = os.environ.get("WEB_ROOT_DIST", "")
if WEB_ROOT and not WEB_ROOT_DIST:
    raise SystemExit("WEB_ROOT=1 은 WEB_ROOT_DIST(WEB_BASE=/ 로 만든 React 빌드 폴더)가 필요합니다")
