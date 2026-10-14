"""portal.db 연결·표 만들기·마이그레이션 + 매일 백업.
portal.py 와 같은 _data/portal.db 를 동시에 써도 되게 같은 표·열·마이그레이션 규칙을 따르고, 더한 것은 login_fails 표와
users 의 새 열(비활성·익명화)·depts.archived·jobs 표(jobs.py)뿐(예전 코드는 모르는 표·열 — 무시됨).
users 표는 '포털 쪽 사용자 정보'(이름·부서·테마·비활성 등)이고, 그중 salt·hash·iters(PIN)는 계정 저장소가 local 일 때만 쓴다(auth/local.py)."""
import contextlib, datetime, os, sqlite3, subprocess, time

from . import config


def _connect():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
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


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def init_db(seed_depts=()):
    """표·열 만들기(없을 때만). seed_depts: 부서 표가 비었을 때 넣을 부서 후보(tools.json 의 dept — app 이 넘김)"""
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
        # deactivated·deact_reason·anonymized·anon_id: 삭제 대신 비활성화 → 익명화(새 포털만 씀 — portal.py 는 모르는 열, disabled=1 로 같이 막힘)
        for col, ddl in (("iters", "INTEGER DEFAULT 200000"), ("disabled", "INTEGER DEFAULT 0"), ("seen", "TEXT"), ("theme", "TEXT"), ("onboarded", "INTEGER DEFAULT 0"),
                         ("deactivated", "TEXT"), ("deact_reason", "TEXT"), ("anonymized", "TEXT"), ("anon_id", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                if col == "onboarded":
                    c.execute("UPDATE users SET onboarded=1")
        if "archived" not in {r[1] for r in c.execute("PRAGMA table_info(depts)")}:   # 부서 보관(보관하면 hidden=1 도 — portal.py 목록에서도 빠짐)
            c.execute("ALTER TABLE depts ADD COLUMN archived TEXT")
        if not c.execute("SELECT 1 FROM depts").fetchone():
            seed = {r[0] for r in c.execute("SELECT DISTINCT dept FROM users")} | set(seed_depts)
            c.executemany("INSERT OR IGNORE INTO depts(name, ts) VALUES(?, datetime('now','localtime'))", [(d.strip(),) for d in seed if d and d.strip() and d.strip() != "개인"])


# ── 매일 백업 — 기본 꺼짐(BACKUP=1 로 켬; 운영 포털 하나만 돌려야 함) ─────────
def backup_loop():
    out = config.BACKUP_DIR
    kst = datetime.timezone(datetime.timedelta(hours=9))
    while True:
        try:
            ages = [time.time() - os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out) if f.startswith("agent-data-")] if os.path.isdir(out) else []
            age = min(ages) / 3600 if ages else 1e9
            n = datetime.datetime.now(kst)
            today = os.path.exists(os.path.join(out, f"agent-data-{n:%Y-%m-%d}.tar.gz"))
            if (n.hour == 3 and not today) or age > 30:
                sh = os.path.join(config.BASE, "backup.sh")
                subprocess.run(["bash", sh if os.path.exists(sh) else os.path.join(config.ROOT, "scripts", "backup.sh")], timeout=3600)
        except Exception as e:
            print("backup:", e, flush=True)
        time.sleep(600)
