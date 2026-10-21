"""python -m fastportal — uvicorn 1워커로 띄움(README 'FastAPI 포털' 참고). TLS_CERT·TLS_KEY 가 있으면 https(TLS 1.2 이상)
python -m fastportal add-user <id> [--name 이름] [--dept 부서] [--pin 숫자 | --password 문자] — 계정 만들기·PIN 초기화(관리자 복구, local 저장소만)"""
import argparse, os, secrets, ssl, sys


def add_user(argv):
    """PIN 은 화면에만(로그·파일에 안 남김). 이미 있는 계정이면 PIN(과 준 값만) 바꾸고 세션을 끊음"""
    from . import accounts, auth, db, sessions
    from .auth.local import ID_RE, PIN_RE
    from .config import OWNERS
    p = argparse.ArgumentParser(prog="python -m fastportal add-user")
    p.add_argument("uid"); p.add_argument("--name"); p.add_argument("--dept")
    p.add_argument("--pin"); p.add_argument("--password", help="관리자 계정용 문자 비밀번호(6~64자, 로그인 칸에 PIN 대신)")
    o = p.parse_args(argv)
    if not auth.is_local():
        sys.exit("AUTH_BACKEND=local 일 때만 — 외부 계정 저장소의 비밀번호는 그쪽에서")
    uid, pin = o.uid.lower(), o.password or o.pin or f"{secrets.randbelow(10**6):06d}"
    if not ID_RE.match(uid) or uid == "owner" or not (PIN_RE.match(pin) or (o.password and 6 <= len(o.password) <= 64)):
        sys.exit("아이디(영문 소문자·숫자 2~32자, owner 불가)·PIN(숫자 4~8) 또는 --password(6~64자) 확인")
    b = auth.backend()
    db.init_db()   # 포털을 한 번도 안 띄운 새 DB 에서도(표가 있으면 그대로)
    with db.db() as c:
        if b.get_user(c, uid):
            b.set_password(c, uid, pin)
            c.execute("UPDATE users SET name=COALESCE(?,name), dept=COALESCE(?,dept) WHERE id=?", (o.name, o.dept, uid))
        else:
            b.create_user(c, uid, o.name or uid, pin, o.dept or "")
        sessions.drop_user_sessions(c, uid)
        sessions.clear_fails(c, uid)   # 잠겨서 복구하는 경우가 많음 — 새 PIN 이 바로 되게
        accounts.audit(c, "cli", "pin_reset", uid)
        off = c.execute("SELECT disabled FROM users WHERE id=?", (uid,)).fetchone()[0]
    print(f"{uid} 계정 저장 (관리자: {uid in OWNERS})" + ("" if o.pin or o.password else f"  PIN {pin}")
          + ("  — 정지·비활성 계정이라 로그인은 안 됨(관리 화면에서 해제)" if off else ""))


if __name__ == "__main__":
    if sys.argv[1:2] == ["add-user"]:
        add_user(sys.argv[2:])
        sys.exit(0)
    import uvicorn
    tls = bool(os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"))
    cfg = uvicorn.Config("fastportal.app:app", host=os.environ.get("HOST") or "0.0.0.0", port=int(os.environ.get("PORT", "8720")),
                         workers=1, proxy_headers=False, server_header=False, date_header=True, access_log=False,
                         timeout_keep_alive=int(os.environ.get("KEEPALIVE_S", "75")), log_level="info",
                         ssl_certfile=os.environ.get("TLS_CERT") if tls else None, ssl_keyfile=os.environ.get("TLS_KEY") if tls else None)
    cfg.load()
    if cfg.ssl:
        cfg.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    uvicorn.Server(cfg).run()
