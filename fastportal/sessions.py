"""세션·쿠키·로그인 유지·실패 잠금·로컬 주인 토큰·접속지 판정 — '누가 요청했나'를 정하는 곳.
아이디·비밀번호(PIN) 확인 자체는 계정 저장소(auth/) 몫이고, 여기는 그 결과로 포털 세션을 만들고 쿠키를 읽는다."""
import datetime, hashlib, hmac, os, re, secrets, time

from . import config
from .config import LOCAL, SESSION_S, SHORT_S, FAIL_WINDOW, FAIL_MAX
from .db import db, now


# ── 로컬 주인 토큰 ──────────────────────────────────────────────────────
def _local_token():
    """토큰 파일을 읽음(없으면 무작위 32바이트로 만듦, 권한 600). 값은 어디에도 출력하지 않음"""
    if config.LOCAL_OWNER_MODE != "token":
        return ""
    f_ = config.LOCAL_TOKEN_FILE
    try:
        fd = os.open(f_, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    os.chmod(f_, 0o600)
    with open(f_, encoding="utf-8") as f:
        tok = f.read().strip()
    if len(tok) < 16:
        raise RuntimeError(f"{f_} 가 비었거나 너무 짧음 — 지우고 다시 띄우면 새로 만듦")
    return tok


LOCAL_TOKEN = _local_token()


def local_owner(client, header):
    """접속지가 이 서버 안이고(그리고 token 모드면 비밀 헤더가 맞으면) True — 쿠키 확인은 부르는 쪽에서"""
    if config.LOCAL_OWNER_MODE == "0" or client not in LOCAL:
        return False
    if config.LOCAL_OWNER_MODE == "1":
        return True
    return bool(header) and hmac.compare_digest(header.strip().encode(), LOCAL_TOKEN.encode())


def client_ip(peer, xff_lines):
    """실제 클라이언트 주소 — 바로 앞 접속이 TRUSTED_PROXIES 일 때만 X-Forwarded-For 를 봄(config.py 주석)"""
    if peer not in config.TRUSTED_PROXIES:
        return peer
    hops = [h.strip() for line in xff_lines for h in line.split(",") if h.strip()]
    for h in reversed(hops):
        if h not in config.TRUSTED_PROXIES:
            return h
    return ""   # 프록시만 거쳤거나 X-Forwarded-For 없음 — 로컬로 보지 않음


# ── 쿠키 ────────────────────────────────────────────────────────────────
SID_RE = re.compile(r"(?:^|;\s*)agsid=([A-Za-z0-9_-]+)")
SID_ANY = re.compile(r"(?:^|;\s*)agsid=")   # 값이 비었거나 형식이 틀려도 '쿠키 있음'(→ 무효 → 401, 로컬 주인으로 보지 않음)


def cookie(tok, age=SESSION_S, persist=True):
    return f"agsid={tok}; Path=/;" + (f" Max-Age={age};" if persist else "") + " HttpOnly; SameSite=Lax" + ("; Secure" if config.TLS else "")


def want_remember(v):
    return v not in (False, 0, "0", "false", "off", "no")


def safe_next(raw):
    raw = raw or "/"
    return raw if re.match(r"^/(?![/\\])", raw) and not re.search(r"[\x00-\x1f\x7f\\]", raw) else "/"


def _h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


# ── 세션 ────────────────────────────────────────────────────────────────
def new_session(c, uid, remember=True):
    tok = secrets.token_urlsafe(32)
    h = _h(tok)
    c.execute("DELETE FROM sessions WHERE exp<?", (time.time(),))
    c.execute("DELETE FROM sess_short WHERE token NOT IN (SELECT token FROM sessions)")
    c.execute("INSERT INTO sessions(token, uid, exp) VALUES(?,?,?)", (h, uid, time.time() + (SESSION_S if remember else SHORT_S)))
    if not remember:
        c.execute("INSERT OR IGNORE INTO sess_short VALUES(?)", (h,))
    return tok


def session_user(tok):
    """쿠키 값 → (uid, name, 새 Set-Cookie 또는 None). 마지막 접속(5분 단위)·하루 한 번 만료 연장(로그인 유지 켠 세션만)"""
    h = _h(tok)
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


def is_short(c, cookie_header):
    """지금 쿠키의 세션이 '로그인 유지 끔'(12시간)이면 True"""
    m = SID_RE.search(cookie_header)
    return bool(m and c.execute("SELECT 1 FROM sess_short WHERE token=?", (_h(m[1]),)).fetchone())


def end_session(cookie_header):
    """로그아웃: 쿠키의 세션 하나를 지움"""
    m = SID_RE.search(cookie_header)
    if m:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (_h(m[1]),))


def drop_user_sessions(c, uid):
    c.execute("DELETE FROM sessions WHERE uid=?", (uid,))


def resolve(cookie_header, client, local_header):
    """요청 → (uid, uname, 새 Set-Cookie). 쿠키 agsid → 계정. 쿠키 없이 이 서버 안(127.0.0.1)에서 + X-Local-Owner 토큰이 맞으면
    주인(local_owner, PORTAL_LOCAL_OWNER). 밖에서 온 X-Agent-User 등은 안 믿음"""
    uid = uname = setc = None
    m = SID_RE.search(cookie_header)
    if m:
        uid, uname, setc = session_user(m[1])
    has_cookie = bool(SID_ANY.search(cookie_header))   # portal.py 는 빈 'agsid=' 를 '쿠키 없음'으로 봐서 로컬이면 주인이 됨 — 여기서는 무효 쿠키
    if not uid and not has_cookie and local_owner(client, local_header):
        uid, uname = "owner", "서버 주인(로컬)"
    return uid, uname, setc


# ── 로그인 실패 잠금 ────────────────────────────────────────────────────
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
