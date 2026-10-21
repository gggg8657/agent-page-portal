"""LocalBackend — 포털 내부 DB(portal.db 의 users 표) 계정 저장소. 지금(임시) 기본값.
PIN: PBKDF2-HMAC-SHA256 60만 번(예전 20만 번 해시는 로그인 성공 때 다시 해시), 비활성·정지·익명화(disabled=1)는 로그인 불가.
portal.py(예전 구현)와 같은 표·열·해시 규칙 — 두 구현이 같은 DB 를 써도 됨."""
import hashlib, hmac, re, secrets, sqlite3

from ..config import OWNERS
from ..db import now
from .base import AuthBackend, User

ID_RE, PIN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$"), re.compile(r"^[0-9]{4,8}$")
ITERS = 600_000


def _hash(pin, salt, iters=ITERS):
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iters)


class LocalBackend(AuthBackend):
    name = "local"
    label = "임시(포털 내부 DB)"
    supports_signup = True
    supports_password_change = True

    def verify(self, c, uid, secret):
        r = c.execute("SELECT salt, hash, iters, disabled, name, dept FROM users WHERE id=?", (uid,)).fetchone()
        h = _hash(secret, r["salt"] if r else b"x" * 16, (r["iters"] or 200_000) if r else ITERS)   # 없는 아이디도 같은 시간
        ok = bool(r) and hmac.compare_digest(h, r["hash"]) and not r["disabled"]
        if ok and (r["iters"] or 0) < ITERS:
            self.set_password(c, uid, secret)
        return User(uid, r["name"] or "", r["dept"] or "") if ok else None

    def get_user(self, c, uid):
        r = c.execute("SELECT id, name, dept, disabled FROM users WHERE id=?", (uid,)).fetchone()
        return User(r["id"], r["name"] or "", r["dept"] or "", bool(r["disabled"])) if r else None

    def list_users(self, c):
        return [User(r["id"], r["name"] or "", r["dept"] or "", bool(r["disabled"])) for r in c.execute("SELECT id, name, dept, disabled FROM users ORDER BY ts")]

    def check_new(self, c, uid, name, secret, local):
        if not ID_RE.match(uid):
            raise ValueError("아이디는 영문 소문자·숫자로 시작하고 영문 소문자·숫자·_.- 2~32자")
        if uid == "owner" or (uid in OWNERS and not local):
            raise ValueError("쓸 수 없는 아이디입니다")
        if not name.strip():
            raise ValueError("이름을 적어 주세요")
        if not PIN_RE.match(secret):
            raise ValueError("PIN 은 숫자 4~8자리")
        if c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            raise ValueError("이미 있는 아이디입니다")

    def create_user(self, c, uid, name, secret, dept=""):
        salt = secrets.token_bytes(16)
        try:   # check_new 뒤 해시 계산(약 0.5초) 사이에 같은 아이디 가입이 끼면 — 덮어쓰지 않고 거절
            c.execute("INSERT INTO users(id,name,dept,salt,hash,iters,ts) VALUES(?,?,?,?,?,?,?)", (uid, name, dept, salt, _hash(secret, salt), ITERS, now()))
        except sqlite3.IntegrityError:
            raise ValueError("이미 있는 아이디입니다") from None

    def check_secret_rule(self, secret):
        if not PIN_RE.match(secret):
            raise ValueError("새 PIN 은 숫자 4~8자리")

    def set_password(self, c, uid, secret):
        salt = secrets.token_bytes(16)
        c.execute("UPDATE users SET salt=?, hash=?, iters=? WHERE id=?", (salt, _hash(secret, salt), ITERS, uid))
