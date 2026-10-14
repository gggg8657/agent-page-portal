"""포털 도메인 로직 — portal.py 와 같은 DB·규칙(계정·세션·부서·역할·요청·도구·테마·랜딩·서명).
웹 서버와 무관한 함수만 둔다(app.py 가 FastAPI 로 감쌈). portal.py 와 같은 _data/portal.db 를 동시에 써도 되게
같은 표·열·마이그레이션 규칙을 따르고, 더한 것은 login_fails 표 하나(예전 코드는 모르는 표 — 무시됨)."""
import base64, contextlib, datetime, hashlib, hmac, json, os, re, secrets, sqlite3, subprocess, sys, threading, time, urllib.parse, urllib.request

ROOT = os.environ.get("PORTAL_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agent-page-portal (시험: 임시 사본)
BASE = os.path.dirname(ROOT)                                          # 도구들을 나란히 clone 한 곳
SHOW_HIDDEN = os.environ.get("SHOW_HIDDEN") == "1"
DATA = os.environ.get("AGENT_DATA") or os.path.join(BASE, "_data")
os.makedirs(DATA, exist_ok=True)
TOOLS_FILE = os.path.join(ROOT, "tools.json")
GUIDE_DIR = os.path.join(ROOT, "guide")
THEME_DIR = os.path.join(ROOT, "theme")
THEMES = {f"{a}-{b}" for a in ("classic", "trendy", "v2") for b in ("light", "dark", "auto")}
LANDING_IMG = ("/landing/home.png", os.path.join(ROOT, "docs", "img", "landing-home.png"))
V2_DIR = os.path.join(ROOT, "v2")
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


def _local_token():
    """토큰 파일을 읽음(없으면 무작위 32바이트로 만듦, 권한 600). 값은 어디에도 출력하지 않음"""
    if LOCAL_OWNER_MODE != "token":
        return ""
    try:
        fd = os.open(LOCAL_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    os.chmod(LOCAL_TOKEN_FILE, 0o600)
    with open(LOCAL_TOKEN_FILE, encoding="utf-8") as f:
        tok = f.read().strip()
    if len(tok) < 16:
        raise RuntimeError(f"{LOCAL_TOKEN_FILE} 가 비었거나 너무 짧음 — 지우고 다시 띄우면 새로 만듦")
    return tok


LOCAL_TOKEN = _local_token()


def local_owner(client, header):
    """접속지가 이 서버 안이고(그리고 token 모드면 비밀 헤더가 맞으면) True — 쿠키 확인은 부르는 쪽에서"""
    if LOCAL_OWNER_MODE == "0" or client not in LOCAL:
        return False
    if LOCAL_OWNER_MODE == "1":
        return True
    return bool(header) and hmac.compare_digest(header.strip().encode(), LOCAL_TOKEN.encode())


# 믿을 프록시 주소(쉼표). 바로 앞 접속이 이 목록일 때만 X-Forwarded-For 를 읽어 실제 클라이언트 주소로 판정(오른쪽부터 믿을 프록시가 아닌 첫 주소).
# 목록의 프록시를 거쳤는데 X-Forwarded-For 가 없으면 '알 수 없음'(로컬 아님). 목록이 비면 X-Forwarded-For 는 무시(portal.py 와 같음)
TRUSTED_PROXIES = {x.strip() for x in os.environ.get("TRUSTED_PROXIES", "").split(",") if x.strip()}


def client_ip(peer, xff_lines):
    if peer not in TRUSTED_PROXIES:
        return peer
    hops = [h.strip() for line in xff_lines for h in line.split(",") if h.strip()]
    for h in reversed(hops):
        if h not in TRUSTED_PROXIES:
            return h
    return ""   # 프록시만 거쳤거나 X-Forwarded-For 없음 — 로컬로 보지 않음
TLS = bool(os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"))
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length", "accept-encoding"}


def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


HTML, HELP, LOGIN, ACCOUNT, BOOT = _read("portal.html"), _read("help.html"), _read("login.html"), _read("account.html"), _read("theme", "boot.js")

_TOOLS = {"m": None, "v": []}
_TOOLS_LOCK = threading.Lock()


def tools():
    """tools.json — 바뀌었을 때만 다시 읽음(수정 시각 비교). 손으로 고치거나 다른 프로세스가 추가해도 새로고침이면 반영"""
    m = os.stat(TOOLS_FILE).st_mtime_ns
    with _TOOLS_LOCK:
        if _TOOLS["m"] != m:
            _TOOLS.update(m=m, v=json.load(open(TOOLS_FILE, encoding="utf-8")))
        return [dict(t) for t in _TOOLS["v"]]   # 복사본 — 호출한 쪽이 고쳐도 캐시는 그대로


def tool(d):
    return next((t for t in tools() if t["dir"] == d), None)


# 혼합형(도구가 다른 컨테이너·서버에): tools.json 항목의 "host"(예: "tools-gpu", "10.0.0.5"). 있으면 상태 확인·프록시는 http://<host>:<port>,
# 시작·중지는 하지 않음(그쪽이 늘 켜 둠). 값은 관리자만 고치는 tools.json 에서만 오고(/api/tool_add 는 host 를 받지 않음),
# 호스트명/IPv4 글자만 허용 — 스킴·경로·포트·@·공백·IPv6 는 거절(틀리면 꺼진 도구로 보이고 프록시하지 않음)
HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")


def is_remote(t):
    return bool(t.get("host"))


def tool_host(t):
    """도구에 접속할 주소. host 없으면 127.0.0.1, 형식이 틀리면 None(접속하지 않음)"""
    h = t.get("host")
    if not h:
        return "127.0.0.1"
    return h if isinstance(h, str) and HOST_RE.fullmatch(h) else None


def tool_addr(t):
    return (tool_host(t), t["port"])


# ── DB ───────────────────────────────────────────────────────────────────
def _connect():
    c = sqlite3.connect(os.path.join(DATA, "portal.db"), timeout=10)
    c.row_factory = sqlite3.Row
    return c


@contextlib.contextmanager
def db():
    """portal.py 의 `with db() as c` 와 같음(성공 commit, 예외 rollback) + 끝나면 닫음"""
    c = _connect()
    try:
        with c:
            yield c
    finally:
        c.close()


def init_db():
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events(kind TEXT, dir TEXT, ts TEXT, user TEXT);
        CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
        CREATE TABLE IF NOT EXISTS post_likes(post INTEGER, user TEXT, UNIQUE(post, user));
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, salt BLOB, hash BLOB, ts TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid TEXT, exp REAL);
        CREATE TABLE IF NOT EXISTS sess_short(token TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS depts(name TEXT PRIMARY KEY, hidden INTEGER DEFAULT 0, ts TEXT);
        CREATE TABLE IF NOT EXISTS dept_heads(dept TEXT, uid TEXT, ts TEXT, UNIQUE(dept, uid));
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT, approver TEXT, note TEXT,
                                            ts TEXT, decided_ts TEXT, notified INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
        -- 새 포털만: 로그인 실패 기록(프로세스·워커가 여럿이어도, 재시작해도 잠금이 맞게). portal.py 는 이 표를 모르고 안 씀
        CREATE TABLE IF NOT EXISTS login_fails(uid TEXT, ts REAL);
        CREATE INDEX IF NOT EXISTS login_fails_uid ON login_fails(uid);""")
        cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
        for col, ddl in (("iters", "INTEGER DEFAULT 200000"), ("disabled", "INTEGER DEFAULT 0"), ("seen", "TEXT"), ("theme", "TEXT"), ("onboarded", "INTEGER DEFAULT 0")):
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                if col == "onboarded":
                    c.execute("UPDATE users SET onboarded=1")
        if not c.execute("SELECT 1 FROM depts").fetchone():
            seed = {r[0] for r in c.execute("SELECT DISTINCT dept FROM users")} | {t.get("dept") for t in tools()}
            c.executemany("INSERT OR IGNORE INTO depts(name, ts) VALUES(?, datetime('now','localtime'))", [(d.strip(),) for d in seed if d and d.strip() and d.strip() != "개인"])


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── 계정·세션 ────────────────────────────────────────────────────────────
ID_RE, PIN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$"), re.compile(r"^[0-9]{4,8}$")
SESSION_S, SHORT_S, ITERS = 30 * 86400, 12 * 3600, 600_000
FAIL_WINDOW, FAIL_MAX = 600, 5


def _hash(pin, salt, iters=ITERS):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iters)


def set_user(c, uid, name, dept, pin):
    salt = secrets.token_bytes(16)
    c.execute("INSERT INTO users(id,name,dept,salt,hash,iters,ts) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, dept=excluded.dept, "
              "salt=excluded.salt, hash=excluded.hash, iters=excluded.iters", (uid, name, dept, salt, _hash(pin, salt), ITERS, now()))


def set_pin(c, uid, pin):
    salt = secrets.token_bytes(16)
    c.execute("UPDATE users SET salt=?, hash=?, iters=? WHERE id=?", (salt, _hash(pin, salt), ITERS, uid))


def check_pin(c, uid, pin):
    r = c.execute("SELECT salt, hash, iters, disabled FROM users WHERE id=?", (uid,)).fetchone()
    h = _hash(pin, r["salt"] if r else b"x" * 16, (r["iters"] or 200_000) if r else ITERS)
    ok = bool(r) and hmac.compare_digest(h, r["hash"]) and not r["disabled"]
    if ok and (r["iters"] or 0) < ITERS:
        set_pin(c, uid, pin)
    return ok


def limited(c, uid):
    """10분에 5번 틀리면 잠금 — DB(login_fails)에 둬서 워커·프로세스가 여럿이어도, 재시작해도 같은 판정"""
    return c.execute("SELECT COUNT(*) FROM login_fails WHERE uid=? AND ts>?", (uid, time.time() - FAIL_WINDOW)).fetchone()[0] >= FAIL_MAX   # 읽기만(쓰기 잠금 안 잡음)


def add_fail(uid):
    """실패 기록은 따로 연결·commit — 요청 트랜잭션은 ValueError 로 rollback 되므로. 그 트랜잭션은 이때 쓰기 잠금이 없어야 함(limited 는 읽기만)"""
    with db() as c:
        c.execute("DELETE FROM login_fails WHERE ts<?", (time.time() - FAIL_WINDOW,))
        c.execute("INSERT INTO login_fails VALUES(?,?)", (uid, time.time()))


def clear_fails(c, uid):
    c.execute("DELETE FROM login_fails WHERE uid=?", (uid,))


def new_session(c, uid, remember=True):
    tok = secrets.token_urlsafe(32)
    h = hashlib.sha256(tok.encode()).hexdigest()
    c.execute("DELETE FROM sessions WHERE exp<?", (time.time(),))
    c.execute("DELETE FROM sess_short WHERE token NOT IN (SELECT token FROM sessions)")
    c.execute("INSERT INTO sessions(token, uid, exp) VALUES(?,?,?)", (h, uid, time.time() + (SESSION_S if remember else SHORT_S)))
    if not remember:
        c.execute("INSERT OR IGNORE INTO sess_short VALUES(?)", (h,))
    return tok


def cookie(tok, age=SESSION_S, persist=True):
    return f"agsid={tok}; Path=/;" + (f" Max-Age={age};" if persist else "") + " HttpOnly; SameSite=Lax" + ("; Secure" if TLS else "")


def want_remember(v):
    return v not in (False, 0, "0", "false", "off", "no")


def safe_next(raw):
    raw = raw or "/"
    return raw if re.match(r"^/(?![/\\])", raw) and not re.search(r"[\x00-\x1f\x7f\\]", raw) else "/"


def check_new(c, uid, name, pin, local):
    if not ID_RE.match(uid):
        raise ValueError("아이디는 영문 소문자·숫자로 시작하고 영문 소문자·숫자·_.- 2~32자")
    if uid == "owner" or (uid in OWNERS and not local):
        raise ValueError("쓸 수 없는 아이디입니다")
    if not name.strip():
        raise ValueError("이름을 적어 주세요")
    if not PIN_RE.match(pin):
        raise ValueError("PIN 은 숫자 4~8자리")
    if c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        raise ValueError("이미 있는 아이디입니다")


SID_RE = re.compile(r"(?:^|;\s*)agsid=([A-Za-z0-9_-]+)")
SID_ANY = re.compile(r"(?:^|;\s*)agsid=")   # 값이 비었거나 형식이 틀려도 '쿠키 있음'(→ 무효 → 401, 로컬 주인으로 보지 않음)
THEME_RE = re.compile(r"(?:^|;\s*)agtheme=([a-z0-9-]+)")


def session_user(tok):
    """쿠키 값 → (uid, name, 새 Set-Cookie 또는 None). 마지막 접속(5분 단위)·하루 한 번 만료 연장(로그인 유지 켠 세션만)"""
    h = hashlib.sha256(tok.encode()).hexdigest()
    with db() as c:
        r = c.execute("SELECT s.uid, s.exp, u.name, u.seen, EXISTS(SELECT 1 FROM sess_short x WHERE x.token=s.token) short FROM sessions s "
                      "JOIN users u ON u.id=s.uid WHERE s.token=? AND s.exp>? AND NOT u.disabled", (h, time.time())).fetchone()
        if not r:
            return None, None, None
        setc = None
        if (r["seen"] or "") < (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds"):
            c.execute("UPDATE users SET seen=? WHERE id=?", (now(), r["uid"]))
        if not r["short"] and r["exp"] - time.time() < SESSION_S - 86400:
            c.execute("UPDATE sessions SET exp=? WHERE token=?", (time.time() + SESSION_S, h))
            setc = cookie(tok)
        return r["uid"], r["name"], setc


def user_theme(uid, cookie_header):
    t = None
    if uid and uid != "owner":
        with db() as c:
            r = c.execute("SELECT theme FROM users WHERE id=?", (uid,)).fetchone()
            t = r and r[0]
    if not t:
        m = THEME_RE.search(cookie_header or "")
        t = m and m[1]
    return t if t in THEMES else "classic-light"


# ── 부서·역할·요청 ───────────────────────────────────────────────────────
KIND = {"dept_join": "부서 소속", "dept_head": "부서장 권한"}


def audit(c, actor, action, target, detail=""):
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))


def heads_of(c, dept):
    return [r[0] for r in c.execute("SELECT uid FROM dept_heads WHERE dept=?", (dept,))]


def my_heads(c, uid):
    return [r[0] for r in c.execute("SELECT dept FROM dept_heads WHERE uid=? ORDER BY dept", (uid,))]


def role(c, uid):
    return "관리자" if uid == "owner" or uid in OWNERS else "부서장" if my_heads(c, uid) else "일반"


def visible_depts(c):
    return [r[0] for r in c.execute("SELECT name FROM depts WHERE hidden=0 ORDER BY name")]


def names(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, name FROM users")}


def req_rows(c, where, args=()):
    nm = names(c) | {"owner": "관리자(로컬)"}
    return [dict(r) | {"uname": nm.get(r["uid"], r["uid"]), "approver_name": nm.get(r["approver"] or "", r["approver"] or ""), "label": KIND.get(r["kind"], r["kind"]),
                       "route": ("관리자" if r["kind"] == "dept_head" or not heads_of(c, r["dept"]) else "부서장 " + ", ".join(nm.get(h, h) for h in heads_of(c, r["dept"])))}
            for r in c.execute(f"SELECT * FROM requests WHERE {where} ORDER BY id DESC LIMIT 200", args)]


def new_request(c, uid, kind, dept):
    if dept not in visible_depts(c):
        raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
    if c.execute("SELECT 1 FROM requests WHERE uid=? AND kind=? AND status='pending'", (uid, kind)).fetchone():
        raise ValueError("이미 대기 중인 요청이 있습니다 — 취소한 뒤 다시 요청하세요")
    c.execute("INSERT INTO requests(kind,uid,dept,status,ts) VALUES(?,?,?,?,?)", (kind, uid, dept, "pending", now()))


def can_decide(c, actor, r):
    if actor == "owner" or actor in OWNERS:
        return True
    return r["kind"] == "dept_join" and r["uid"] != actor and actor in heads_of(c, r["dept"])


def decide(c, actor, rid, ok, note=""):
    r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] != "pending":
        raise ValueError("이미 처리됐거나 없는 요청입니다")
    if not can_decide(c, actor, r):
        raise PermissionError("이 요청을 처리할 권한이 없습니다")
    c.execute("UPDATE requests SET status=?, approver=?, note=?, decided_ts=?, notified=0 WHERE id=?", ("approved" if ok else "rejected", actor, note[:200], now(), rid))
    if ok and r["kind"] == "dept_join":
        c.execute("UPDATE users SET dept=? WHERE id=?", (r["dept"], r["uid"]))
        for d in my_heads(c, r["uid"]):
            if d != r["dept"]:
                c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, r["uid"])); audit(c, actor, "head_revoke", r["uid"], f"{d} (부서 이동)")
    if ok and r["kind"] == "dept_head":
        c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (r["dept"], r["uid"], now()))
    audit(c, actor, ("approve" if ok else "reject") + ":" + r["kind"], r["uid"], f"{r['dept']} #{rid} {note[:200]}".strip())


def usage(c):
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    out = {}
    for r in c.execute("SELECT dir, SUM(kind='open') total, SUM(kind='open' AND ts>=?) recent, "
                       "COUNT(DISTINCT CASE WHEN kind='open' THEN user END) users, SUM(kind='like') likes FROM events GROUP BY dir", (cut,)):
        out[r["dir"]] = {k: r[k] or 0 for k in ("recent", "total", "users", "likes")}
    return out


def user_rank(c):
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    return [{"user": r["name"], "n": r["n"]} for r in
            c.execute("SELECT u.name, COUNT(*) n FROM events e JOIN users u ON u.id=e.user WHERE e.kind='open' AND e.ts>=? GROUP BY e.user ORDER BY n DESC LIMIT 5", (cut,))]


def posts(c, user):
    return [dict(r) | {"tags": json.loads(r["tags"] or "[]"), "liked": bool(r["liked"])} for r in
            c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes, "
                      "(SELECT COUNT(*) FROM post_likes WHERE post=p.id AND user=?) liked FROM posts p ORDER BY id DESC LIMIT 50", (user,))]


def me(c, uid, uname):
    is_owner = uid == "owner" or uid in OWNERS
    u = c.execute("SELECT dept, onboarded FROM users WHERE id=?", (uid,)).fetchone()
    pend = c.execute("SELECT dept FROM requests WHERE uid=? AND kind='dept_join' AND status='pending'", (uid,)).fetchone()
    n = sum(can_decide(c, uid, r) for r in c.execute("SELECT * FROM requests WHERE status='pending'"))
    return {"id": uid, "name": uname, "owner": is_owner, "agent_user": "owner" if is_owner else uid, "role": role(c, uid),
            "dept": u["dept"] if u else "", "dept_pending": pend["dept"] if pend else "", "heads": my_heads(c, uid), "approvals": n,
            "account": bool(u), "onboarded": bool(u["onboarded"]) if u else True}


# ── 도구 시작·중지·등록 (portal.py 와 같은 환경변수·BIND·GPU) ──────────────
# venv 없는 도구(saju-local)를 띄울 파이썬: portal.py 는 자기 파이썬(sys.executable)을 썼다. 새 포털은 fastportal 전용 venv 에서
# 돌므로 그 venv 의 바탕 파이썬(= portal.py 를 돌리던 시스템 python3)을 쓴다. TOOL_PYTHON 으로 바꿀 수 있음
TOOL_PY = os.environ.get("TOOL_PYTHON") or getattr(sys, "_base_executable", None) or sys.executable


TOOL_BIND = os.environ.get("TOOL_BIND") or "127.0.0.1"   # 도구가 열 주소(BIND). 컨테이너에선 0.0.0.0(다른 컨테이너의 포털이 붙게)


def start(t, extra_env=None):
    if is_remote(t):
        return   # 다른 컨테이너의 도구 — 그쪽이 띄움
    cwd = os.path.join(BASE, t["dir"])
    env = {**os.environ, "PORT": str(t["port"]), "WORKSPACE": os.path.join(DATA, t["dir"]), "BIND": TOOL_BIND, **(extra_env or {})}   # + 작업 알림(PORTAL_JOBS_*, jobs.tool_env)
    env.pop("PORTAL_JOBS_SECRET", None)   # 포털 비밀값은 도구로 안 넘김(도구는 자기 토큰만)
    if t.get("gpus"):
        env["CUDA_VISIBLE_DEVICES"] = str(t["gpus"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        cmd = ["bash", "setup.sh"]
    else:
        py = os.path.join(cwd, "venv", "bin", "python")
        cmd = [py if os.path.exists(py) else TOOL_PY, "app.py"]
    env.pop("VIRTUAL_ENV", None)   # 새 포털은 자기 venv 에서 돈다 — 도구가 그 venv 를 물려받지 않게
    subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(cwd, "server.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)


def port_pids(port):
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f).read().splitlines()[1:]:
                c = line.split()
                if c[3] == "0A" and int(c[1].rsplit(":", 1)[1], 16) == port: inodes.add(c[9])
        except OSError:
            pass
    pids = set()
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid(): continue
        try:
            for fd in os.listdir(f"/proc/{p}/fd"):
                if os.readlink(f"/proc/{p}/fd/{fd}")[8:-1] in inodes: pids.add(int(p)); break
        except OSError:
            pass
    return pids


def stop(t):
    if is_remote(t):
        return
    cwd = os.path.join(BASE, t["dir"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        subprocess.run(["bash", "setup.sh", "stop"], cwd=cwd, capture_output=True)
    for pid in port_pids(t["port"]):
        try:
            os.kill(pid, 15)
        except OSError:
            pass


_ADD_LOCK = threading.Lock()


def add_tool(t):
    d = t.get("dir", "")
    if not d.replace("-", "").replace("_", "").isalnum():
        raise ValueError("폴더 이름은 영문·숫자·-_ 만")
    with _ADD_LOCK:
        ts = tools()
        if tool(d):
            raise ValueError("이미 있는 도구")
        port = int(t.get("port") or 0)
        if not (1024 < port < 65536) or any(x["port"] == port for x in ts):
            raise ValueError("포트가 비어 있지 않거나 범위 밖")
        t = {k: re.sub(r"[<>\"'`\\]", "", str(v))[:200] for k, v in t.items()}
        row = {"dir": d, "port": port, "name": t.get("name") or d, "title": t.get("title") or d, "desc": t.get("desc", ""), "group": t.get("group") or "기타",
               "tags": [x for x in (t.get("tags") or "").replace("#", "").split() if x], "dept": t.get("dept", ""), "author": t.get("author", ""),
               "added": datetime.date.today().isoformat()}
        row["tags"] = ["#" + x for x in row["tags"]]
        ts.append(row)
        json.dump(ts, open(TOOLS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return row


def landing_groups():
    order, g = [], {}
    for t in tools():
        if t.get("hidden"):
            continue
        k = t.get("group") or "기타"
        if k not in g:
            order.append(k); g[k] = []
        g[k].append(t.get("name") or t["dir"])
    return sorted(((k, g[k]) for k in order), key=lambda kv: -len(kv[1]))


def landing(html, who=""):
    gs = landing_groups()
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    cards = "".join(f'<li class="lp-g"><div class="lp-gh"><b>{esc(k)}</b><span>{len(v)}</span></div><p>{esc(" · ".join(v[:5]))}{" …" if len(v) > 5 else ""}</p></li>' for k, v in gs)
    name, mail = _SIG.split(" · ")
    sig = f'<span data-sig title="{mail}">{name} · 내부 시험용 · 배포용 아님</span>'
    return (html.replace("%N_TOOLS%", str(sum(len(v) for _, v in gs))).replace("%N_GROUPS%", str(len(gs)))
            .replace("%GROUPS%", cards).replace("%SIG%", sig)
            .replace("%FOOT%", f'지금 <b>{esc(who)}</b> 로 접속 중 · <a href="/">포털로</a> · <a href="/account">계정 설정·PIN 바꾸기</a> · <a href="/logout">로그아웃</a>' if who else ""))


def guide(d):
    p = os.path.join(ROOT, "guides", f"{d}.md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else f"## {d}\n\n사용법이 아직 없습니다. `portal/guides/{d}.md` 를 만들면 여기에 뜹니다."


# ── GPU 현황 (5초 캐시, 동시에 여러 탭이 불러도 nvidia-smi 는 한 번) ─────────
_GPU = {"t": 0, "v": None}
_GPU_LOCK = threading.Lock()


def _smi(q, kind):
    r = subprocess.run(["nvidia-smi", f"--query-{kind}={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    return [[x.strip() for x in l.split(",")] for l in r.stdout.splitlines() if l.strip()]


def _owner_of(pid):
    nm = {t["dir"]: t.get("name") or t["dir"] for t in tools()}
    for _ in range(6):
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
        if "ollama" in cwd:
            return "LLM (Ollama)"
        if "vllm" in cwd:   # ~/.local/vllm/start.sh 가 cd 해서 띄움
            return "LLM (vLLM)"
        if cwd.startswith(BASE + os.sep):
            d = cwd[len(BASE) + 1:].split(os.sep)[0]
            if d in nm:
                return nm[d]
        try:
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if pid <= 1:
            return None


def _local_gpu_procs():
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            g = {int(m) for m in re.findall(r"/dev/nvidia(\d+)", open(f"/proc/{p}/maps").read())}
            if g:
                out.append((int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[19]), int(p), g))
        except (OSError, ValueError, IndexError):
            pass
    return sorted(out)


def llm_queue():
    """vLLM /metrics(Prometheus 글자)에서 LLM 대기열: 처리 중·대기 요청 수, KV 캐시 사용률(0~1). LLM_API=openai 일 때만, 못 읽으면 ok=False."""
    if os.environ.get("LLM_API", "ollama") != "openai":
        return None
    base = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    out = {"ok": False, "model": os.environ.get("LLM_MODEL") or None, "running": None, "waiting": None, "kv_cache": None}
    try:
        with urllib.request.urlopen(base + "/metrics", timeout=2) as r:
            text = r.read().decode(errors="replace")
    except (OSError, ValueError):
        return out
    m = {}
    for line in text.splitlines():
        if line.startswith("vllm:") and "{" in line:
            k, _, v = line.partition("{")
            try:
                m[k] = m.get(k, 0.0) + float(v.rsplit("}", 1)[1])   # 엔진(데이터 병렬)이 여럿이면 더함
            except (ValueError, IndexError):
                pass
    kv = m.get("vllm:kv_cache_usage_perc", m.get("vllm:gpu_cache_usage_perc"))   # 이름은 perc 지만 값은 0~1
    if "vllm:num_requests_running" in m:
        out.update(ok=True, running=int(m["vllm:num_requests_running"]), waiting=int(m.get("vllm:num_requests_waiting", 0)),
                   kv_cache=round(kv, 4) if kv is not None else None)
    return out


def gpu_status():
    with _GPU_LOCK:
        if time.time() - _GPU["t"] < 5 and _GPU["v"]:
            return _GPU["v"]
        try:
            gpus = [{"index": int(i), "uuid": u, "name": n, "used": int(mu), "total": int(mt), "util": int(ut) if ut.isdigit() else None, "procs": []}
                    for i, u, n, mu, mt, ut in _smi("index,uuid,name,memory.used,memory.total,utilization.gpu", "gpu")]
            apps = _smi("gpu_uuid,pid,used_memory", "compute-apps")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {"gpus": [], "error": f"nvidia-smi 실패: {type(e).__name__}"}
        by_uuid = {g["uuid"]: g for g in gpus}
        host = {}
        for u, pid, mem in apps:
            if u in by_uuid and pid.isdigit():
                host.setdefault(int(pid), {})[by_uuid[u]["index"]] = int(mem) if mem.isdigit() else 0
        local, li, who = _local_gpu_procs(), 0, {}
        for hp in sorted(host):
            for j in range(li, len(local)):
                if set(host[hp]) <= local[j][2]:
                    who[hp], li = _owner_of(local[j][1]), j + 1
                    break
        for hp, per in host.items():
            for gi, mem in per.items():
                g = next(g for g in gpus if g["index"] == gi)
                name = who.get(hp) or "기타 (이 서버 밖·알 수 없음)"
                hit = next((p for p in g["procs"] if p["tool"] == name), None)
                if hit:
                    hit["mem"] += mem
                else:
                    g["procs"].append({"tool": name, "mem": mem})
        for g in gpus:
            g.pop("uuid"); g["procs"].sort(key=lambda p: -p["mem"])
        _GPU.update(t=time.time(), v={"gpus": gpus, "t": int(time.time()), "llm": llm_queue()})
        return _GPU["v"]


# ── 매일 백업 — 새 포털은 기본 꺼짐(BACKUP=1 로 켬; 운영 포털이 돌리는 동안 둘이 같이 돌면 안 됨) ──
def backup_loop():
    out = os.environ.get("AGENT_BACKUP_DIR") or os.path.expanduser("~/agent-page-backup")
    kst = datetime.timezone(datetime.timedelta(hours=9))
    while True:
        try:
            ages = [time.time() - os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out) if f.startswith("agent-data-")] if os.path.isdir(out) else []
            age = min(ages) / 3600 if ages else 1e9
            n = datetime.datetime.now(kst)
            today = os.path.exists(os.path.join(out, f"agent-data-{n:%Y-%m-%d}.tar.gz"))
            if (n.hour == 3 and not today) or age > 30:
                sh = os.path.join(BASE, "backup.sh")
                subprocess.run(["bash", sh if os.path.exists(sh) else os.path.join(ROOT, "scripts", "backup.sh")], timeout=3600)
        except Exception as e:
            print("backup:", e, flush=True)
        time.sleep(600)


# ── 저작권 표기 ──────────────────────────────────────────────────────────
_SIG = base64.b64decode("wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==").decode()
_SIG_A = base64.b64decode("Z2dnZzg2NTcgPGRvbmdqdWtpbS5kZXZAZ21haWwuY29tPg==").decode()


def signed(html):
    name, mail = _SIG.split(" · ")
    if 'name="author"' not in html:
        meta = f'<meta name="author" content="{name[7:]} <{mail}>">'
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + meta, 1)
        else:
            m = re.match(r"\s*<!doctype[^>]*>", html, re.I)
            html = html[:m.end()] + meta + html[m.end():] if m else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name} · 내부 시험용 · 배포용 아님</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


# ── 화면 테마 ───────────────────────────────────────────────────────────
def theme_files(pref, kind, d=""):
    if pref == "classic-light":
        return []
    f, light = ["tokens.css"], pref.endswith("-light")
    if kind == "tool":
        f += ({"trendy": ["base-trendy.css"], "v2": ["base-v2.css"]}.get(pref.split("-")[0], [])) + ([] if light else ["base-dark.css", "dark.js"]) + ["chrome.css"]
        f += [f"tools/{d}.css"] if os.path.isfile(os.path.join(THEME_DIR, "tools", f"{d}.css")) else []
    else:
        f += ["guide.css"] if kind == "guide" else ["portal-v2.css" if pref.startswith("v2") else "portal.css", "chrome.css"]
    return f


def theme_ver():
    return str(int(max(os.path.getmtime(os.path.join(r, x)) for r, _, fs in os.walk(THEME_DIR) for x in fs)))


def theme_head(pref, kind, d=""):
    v = theme_ver()
    tags = "".join(f'<script src="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}"></script>' if f.endswith(".js") else
                   f'<link rel="stylesheet" href="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}">' for f in theme_files(pref, kind, d))
    js = BOOT.replace("%KIND%", kind).replace("%DIR%", d).replace("%V%", v).replace("%PREF%", pref).replace("%TOOLCSS%", "true" if f"tools/{d}.css" in theme_files("trendy-dark", kind, d) else "false")
    if kind == "portal":
        tags += f'<script src="/theme/picker.js?v={v}" defer></script>'
        if pref.startswith("v2"):
            tags += f'<script src="/theme/palette-v2.js?v={v}" defer></script>'
    return f"<script>{js}</script>{tags}"


def v2_page(html, name):
    f = os.path.join(V2_DIR, f"{name}.html")
    a, b = html.find("<style>"), html.find("<script>")
    if not os.path.isfile(f) or a < 0 or b < a:
        return html
    return html[:a] + open(f, encoding="utf-8").read() + html[b:]


def first_html(rest):
    path, _, q = rest.partition("?")
    return path in ("", "index.html") and "1" not in urllib.parse.parse_qs(q).get("pet", [])


def themed(html, head):
    m = re.search(r"<meta\s+charset[^>]*>", html[:2048], re.I) or re.search(r"<head(\s[^>]*)?>", html[:4096], re.I) or re.search(r"<!doctype[^>]*>", html[:512], re.I)
    i = m.end() if m else 0
    return html[:i] + head + html[i:]


def html_page(html, kind, theme, d="", v2=""):
    if v2 and theme.startswith("v2"):
        html = v2_page(html, v2)
    return signed(themed(html, theme_head(theme, kind, d))).encode()


def help_html(d, agent_user):
    return HELP.replace("%DIR%", d).replace("%USER%", agent_user)
