"""portal.db 연결·표 만들기·마이그레이션 + 매일 백업.
portal.py 와 같은 _data/portal.db 를 동시에 써도 되게 같은 표·열·마이그레이션 규칙을 따르고, 더한 것은 login_fails 표와
users 의 새 열(비활성·익명화)·depts.archived·jobs 표(jobs.py)뿐(예전 코드는 모르는 표·열 — 무시됨).
users 표는 '포털 쪽 사용자 정보'(이름·부서·테마·비활성 등)이고, 그중 salt·hash·iters(PIN)는 계정 저장소가 local 일 때만 쓴다(auth/local.py)."""
import contextlib, datetime, os, re, sqlite3, subprocess, threading, time

from . import config, timeutil


def _connect():
    c = sqlite3.connect(config.DB_FILE, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# 연결 재사용(PORTAL_DB_REUSE, 기본 1): 스레드마다 쉬는 연결을 몇 개(_KEEP) 두고 다시 씀 — 요청마다 connect/close(약 0.5 ms + GIL)를 없앰.
# 한 스레드 안에서 db() 를 겹쳐 열면(예: 요청 트랜잭션 안에서 sessions.add_fail 이 따로 commit) 쉬는 연결이 없으므로 새 연결을 씀
# → 예전처럼 서로 다른 연결·트랜잭션. 돌려받을 때 트랜잭션이 남아 있으면(있으면 안 됨) rollback 하고 버림. DB 파일이 바뀌면(복원) 다시 엶
_REUSE = os.environ.get("PORTAL_DB_REUSE", "1") != "0"
_KEEP = 2
_TL = threading.local()


def _ident():
    try:
        st = os.stat(config.DB_FILE)
        return (config.DB_FILE, st.st_dev, st.st_ino, st.st_ctime_ns)   # ctime: 지우고 다시 만든 파일(같은 inode 재사용)도 구분 — WAL 이라 본 파일은 체크포인트 때만 바뀜
    except OSError:
        return None


@contextlib.contextmanager
def db():
    """portal.py 의 `with db() as c` 와 같음(성공 commit, 예외 rollback). 끝나면 닫거나(재사용 끔) 이 스레드의 쉬는 연결로 돌려놓음"""
    if not _REUSE:
        c = _connect()
        try:
            with c:
                yield c
        finally:
            c.close()
        return
    idle = getattr(_TL, "idle", None)
    if idle is None:
        idle = _TL.idle = []
    ident = _ident()
    c = None
    while idle:
        x, xid = idle.pop()
        if ident is not None and xid == ident:
            c = x
            break
        x.close()
    if c is None:
        c = _connect()
        ident = ident or _ident()   # 파일이 방금 만들어졌으면 그 정체로(그래도 없으면 돌려놓지 않음)
    try:
        with c:
            yield c
    finally:
        if ident is not None and not c.in_transaction and len(idle) < _KEEP:
            idle.append((c, ident))
        else:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            c.close()


ROW_ID = re.compile(r"[A-Za-z0-9_-]{6,40}")   # 흐름·아이디어·제출·리뷰·요청의 id


def own_row(c, table, rid, uid, missing, forbidden, owner=False):
    """id 로 한 줄 — 형식이 틀리거나 없으면 FileNotFoundError(missing), 남의 것이면 PermissionError(forbidden). owner=True 면 남의 것도 됨(관리자)"""
    if not isinstance(rid, str) or not ROW_ID.fullmatch(rid):
        raise FileNotFoundError(missing)
    r = c.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone()   # table 은 호출하는 코드의 상수
    if not r:
        raise FileNotFoundError(missing)
    if r["uid"] != uid and not owner:
        raise PermissionError(forbidden)
    return r


def now():
    """저장용 지금 시각 — UTC, 타임존 표시 없는 ISO(예전 값과 같은 형식). 규칙은 timeutil 머리말"""
    return timeutil.now_iso()


def init_db(seed_depts=()):
    """표·열 만들기(없을 때만). seed_depts: 부서 표가 비었을 때 넣을 부서 후보(tools.json 의 dept — app 이 넘김)"""
    with db() as c:
        c.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events(kind TEXT, dir TEXT, ts TEXT, user TEXT);
        CREATE INDEX IF NOT EXISTS events_user ON events(user, kind);
        CREATE TABLE IF NOT EXISTS dwell_segments(uid TEXT, visit TEXT, seq INTEGER, screen TEXT, tool TEXT, seconds INTEGER, ts TEXT, day TEXT,
                                                   PRIMARY KEY(uid, visit, seq));
        CREATE INDEX IF NOT EXISTS dwell_day_user ON dwell_segments(day, uid);
        CREATE INDEX IF NOT EXISTS dwell_visit ON dwell_segments(visit);
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
                         ("deactivated", "TEXT"), ("deact_reason", "TEXT"), ("anonymized", "TEXT"), ("anon_id", "TEXT"),
                         # tour_seen: 이 계정이 본(또는 닫은) 둘러보기 판(config.TOUR_VERSION). 열을 처음 더할 때 모두 0 → 기존 계정은 '새 기능 둘러보기' 안내를 판마다 한 번만
                         ("tour_seen", "INTEGER DEFAULT 0"),
                         # tut_seen: 본(또는 닫은) 따라 하기 {id: 판} JSON — 도구 첫 방문 자동 시작을 계정 단위로(PC·캐시와 무관)
                         ("tut_seen", "TEXT")):
            if col not in cols:
                c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
                if col == "onboarded":
                    c.execute("UPDATE users SET onboarded=1")
        from . import board, posts   # 커뮤니티 글 — posts 표 열 더하기·신고 표(posts.init), 자유게시판 표(board.init)
        posts.init(c)
        board.init(c)
        if "ip" not in {r[1] for r in c.execute("PRAGMA table_info(login_fails)")}:   # 잠금을 (아이디, 접속지) 단위로 + 접속지별 속도 제한(sessions.limited·ip_limited)
            c.execute("ALTER TABLE login_fails ADD COLUMN ip TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS login_fails_ip ON login_fails(ip)")
        if "archived" not in {r[1] for r in c.execute("PRAGMA table_info(depts)")}:   # 부서 보관(보관하면 hidden=1 도 — portal.py 목록에서도 빠짐)
            c.execute("ALTER TABLE depts ADD COLUMN archived TEXT")
        if not c.execute("SELECT 1 FROM depts").fetchone():
            seed = {r[0] for r in c.execute("SELECT DISTINCT dept FROM users")} | set(seed_depts)
            c.executemany("INSERT OR IGNORE INTO depts(name, ts) VALUES(?, datetime('now'))", [(d.strip(),) for d in seed if d and d.strip() and d.strip() != "개인"])
        from . import org   # 계층 조직(org_units·users.unit_id·dept_heads.unit_id) — 비어 있으면 기본 조직도 넣고 예전 부서 이름을 옮김
        org.init(c)


# ── 매일 백업 — 기본 꺼짐(BACKUP=1 로 켬; 운영 포털 하나만 돌려야 함) ─────────
def backup_loop():
    out = config.BACKUP_DIR
    while True:
        try:
            ages = [time.time() - os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out) if f.startswith("agent-data-")] if os.path.isdir(out) else []
            age = min(ages) / 3600 if ages else 1e9
            n = timeutil.now_kst()   # 03시 KST(= 18시 UTC) · 파일 이름도 KST 날짜(backup.sh 와 같음)
            today = os.path.exists(os.path.join(out, f"agent-data-{n:%Y-%m-%d}.tar.gz"))
            if (n.hour == 3 and not today) or age > 30:
                sh = os.path.join(config.BASE, "backup.sh")
                subprocess.run(["bash", sh if os.path.exists(sh) else os.path.join(config.ROOT, "scripts", "backup.sh")], timeout=3600)
        except Exception as e:
            print("backup:", e, flush=True)
        time.sleep(600)
