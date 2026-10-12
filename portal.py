#!/usr/bin/env python3
"""에이전트 페이지 포털 — 로그인·도구 목록·상태·시작/중지·리버스 프록시(/t/<도구>/)·사용 통계·피드. stdlib + sqlite.
  python3 portal.py                 # http://localhost:8700
  python3 portal.py start-all|stop-all
  python3 portal.py --add-user <id> --name <이름> [--dept 부서] [--pin 숫자 | --password 문자]   # 계정 만들기·PIN 초기화(--pin 없으면 무작위 6자리를 화면에만)
env: PORT(8700) AGENT_DATA(모든 도구 데이터 루트, 기본 ../_data) TLS_CERT·TLS_KEY(둘 다 있으면 https, README 참고)
     AGENT_OWNERS(쉼표 — 서버 주인 계정 id, 기본 dongju,gggg8657) BACKUP(1=매일 03시 ../backup.sh, 기본 :8700 만)"""
import datetime, hashlib, hmac, json, os, re, secrets, socket, sqlite3, ssl, subprocess, sys, threading, time, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(ROOT)
PORT = int(os.environ.get("PORT", "8700"))
SHOW_HIDDEN = os.environ.get("SHOW_HIDDEN") == "1"  # 테스트 포털: 숨긴 도구도 목록에 (예: PORT=8701 HOST=127.0.0.1 SHOW_HIDDEN=1)
HOST = os.environ.get("HOST", "")
DATA = os.environ.get("AGENT_DATA") or os.path.join(BASE, "_data")  # 포털 DB + 모든 도구 workspace 가 이 아래로 모임
os.makedirs(DATA, exist_ok=True)
TOOLS_FILE = os.path.join(ROOT, "tools.json")
GUIDE_DIR = os.path.join(ROOT, "guide")  # 사용 설명서 (/guide)
THEME_DIR = os.path.join(ROOT, "theme")  # 화면 테마 (/theme) — theme/THEME_GUIDE.md
THEMES = {f"{a}-{b}" for a in ("classic", "trendy") for b in ("light", "dark", "auto")}  # classic-light = 예전 그대로(CSS 안 붙임)


# 서버 주인 → 도구에 X-Agent-User: owner (예전 기록 전부 보임). 나머지는 계정 id 별 폴더.
# 접속 IP 는 NAT 뒤라 모두 서버 주소로 보여서 사람 구분에 못 씀 → 로그인. 예외: 이 서버 안(127.0.0.1)에서 쿠키 없이 = 주인(SSH 터널 테스트 포털·내부 호출)
OWNERS = {x.strip() for x in os.environ.get("AGENT_OWNERS", "dongju,gggg8657").split(",") if x.strip()}
LOCAL = ("127.0.0.1", "::1")


def tools():  # 요청마다 다시 읽음 — 손으로 고치거나 다른 프로세스가 추가해도 새로고침이면 반영
    return json.load(open(TOOLS_FILE, encoding="utf-8"))

HTML = open(os.path.join(ROOT, "portal.html"), encoding="utf-8").read()
HELP = open(os.path.join(ROOT, "help.html"), encoding="utf-8").read()  # 도구 첫 화면에 끼워 넣는 ? 버튼 + 사용법
LOGIN = open(os.path.join(ROOT, "login.html"), encoding="utf-8").read()
ACCOUNT = open(os.path.join(ROOT, "account.html"), encoding="utf-8").read()  # /account · /approvals · /admin 한 파일
BOOT = open(os.path.join(THEME_DIR, "boot.js"), encoding="utf-8").read()  # 모든 화면 <head> 맨 앞: 테마를 첫 그림 전에 정함
TLS = bool(os.environ.get("TLS_CERT") and os.environ.get("TLS_KEY"))  # https 면 세션 쿠키에 Secure
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length", "accept-encoding"}


# ── DB (sqlite, 파일 하나) ───────────────────────────────────────────────
def db():
    c = sqlite3.connect(os.path.join(DATA, "portal.db"), timeout=10)
    c.row_factory = sqlite3.Row
    return c


with db() as _c:  # 스키마는 시작 시 1회
    _c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS events(kind TEXT, dir TEXT, ts TEXT, user TEXT);          -- open | like
    CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS post_likes(post INTEGER, user TEXT, UNIQUE(post, user));
    CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, salt BLOB, hash BLOB, ts TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid TEXT, exp REAL);          -- token = sha256(쿠키 값)
    CREATE TABLE IF NOT EXISTS depts(name TEXT PRIMARY KEY, hidden INTEGER DEFAULT 0, ts TEXT);
    CREATE TABLE IF NOT EXISTS dept_heads(dept TEXT, uid TEXT, ts TEXT, UNIQUE(dept, uid));   -- 부서장 (한 부서에 여럿 가능)
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT, approver TEXT, note TEXT,
                                        ts TEXT, decided_ts TEXT, notified INTEGER DEFAULT 0);  -- kind dept_join|dept_head, status pending|approved|rejected|cancelled
    CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);""")
    cols = {r[1] for r in _c.execute("PRAGMA table_info(users)")}
    for col, ddl in (("iters", "INTEGER DEFAULT 200000"), ("disabled", "INTEGER DEFAULT 0"), ("seen", "TEXT"), ("theme", "TEXT")):  # 예전 DB 이어 쓰기
        if col not in cols:
            _c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    if not _c.execute("SELECT 1 FROM depts").fetchone():  # 처음 한 번: 기존 계정·tools.json 의 부서로 목록 시작 ('개인' 은 부서가 아님)
        seed = {r[0] for r in _c.execute("SELECT DISTINCT dept FROM users")} | {t.get("dept") for t in json.load(open(TOOLS_FILE, encoding="utf-8"))}
        _c.executemany("INSERT OR IGNORE INTO depts(name, ts) VALUES(?, datetime('now','localtime'))", [(d.strip(),) for d in seed if d and d.strip() and d.strip() != "개인"])


# ── 계정·세션 ────────────────────────────────────────────────────────────
ID_RE, PIN_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$"), re.compile(r"^[0-9]{4,8}$")
SESSION_S, ITERS = 30 * 86400, 600_000   # 예전 계정(200k)은 다음 로그인 때 600k 로 다시 해시
FAILS, FAIL_LOCK = {}, threading.Lock()   # id → 최근 실패 시각들 (10분에 5번까지)


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
    """맞으면 True. 해시 반복 횟수가 예전 값이면 맞힌 김에 지금 값으로 다시 해시. 정지된 계정은 False"""
    r = c.execute("SELECT salt, hash, iters, disabled FROM users WHERE id=?", (uid,)).fetchone()
    h = _hash(pin, r["salt"] if r else b"x" * 16, (r["iters"] or 200_000) if r else ITERS)  # 없는 id 도 같은 시간
    ok = bool(r) and hmac.compare_digest(h, r["hash"]) and not r["disabled"]
    if ok and (r["iters"] or 0) < ITERS:
        set_pin(c, uid, pin)
    return ok


def limited(uid):
    with FAIL_LOCK:
        FAILS[uid] = [t for t in FAILS.get(uid, []) if time.time() - t < 600]
        return len(FAILS[uid]) >= 5


def new_session(c, uid):
    tok = secrets.token_urlsafe(32)
    c.execute("DELETE FROM sessions WHERE exp<?", (time.time(),))
    c.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(tok.encode()).hexdigest(), uid, time.time() + SESSION_S))
    return tok


def cookie(tok, age=SESSION_S):
    return f"agsid={tok}; Path=/; Max-Age={age}; HttpOnly; SameSite=Lax" + ("; Secure" if TLS else "")


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


# ── 부서·역할·요청 ───────────────────────────────────────────────────────
# 역할: 관리자(AGENT_OWNERS 계정) · 부서장(dept_heads, 부서마다) · 일반. 부서 변경·가입은 요청 → 그 부서 부서장(없으면 관리자) 승인 후 반영.
# 부서장 요청은 관리자만 승인. 부서장 권한: 자기 부서 가입 요청 승인·거절, 자기 부서 구성원 목록과 포털 사용 횟수 보기 — 도구 쪽은 바뀌는 것 없음.
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
        for d in my_heads(c, r["uid"]):  # 다른 부서로 옮기면 예전 부서의 부서장 권한은 내려놓음
            if d != r["dept"]:
                c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, r["uid"])); audit(c, actor, "head_revoke", r["uid"], f"{d} (부서 이동)")
    if ok and r["kind"] == "dept_head":
        c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (r["dept"], r["uid"], now()))
    audit(c, actor, ("approve" if ok else "reject") + ":" + r["kind"], r["uid"], f"{r['dept']} #{rid} {note[:200]}".strip())


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def usage(c):
    """dir → {recent, total, users, likes}"""
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    out = {}
    for r in c.execute("SELECT dir, SUM(kind='open') total, SUM(kind='open' AND ts>=?) recent, "
                       "COUNT(DISTINCT CASE WHEN kind='open' THEN user END) users, SUM(kind='like') likes FROM events GROUP BY dir", (cut,)):
        out[r["dir"]] = {k: r[k] or 0 for k in ("recent", "total", "users", "likes")}
    return out


def user_rank(c):
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    return [{"user": r["name"], "n": r["n"]} for r in   # 계정 있는 사람만, 이름으로(IP 로 남은 예전 기록은 안 보임)
            c.execute("SELECT u.name, COUNT(*) n FROM events e JOIN users u ON u.id=e.user WHERE e.kind='open' AND e.ts>=? GROUP BY e.user ORDER BY n DESC LIMIT 5", (cut,))]


def posts(c, user):
    return [dict(r) | {"tags": json.loads(r["tags"] or "[]"), "liked": bool(r["liked"])} for r in
            c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes, "
                      "(SELECT COUNT(*) FROM post_likes WHERE post=p.id AND user=?) liked FROM posts p ORDER BY id DESC LIMIT 50", (user,))]


# ── 도구 ────────────────────────────────────────────────────────────────
def alive(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def tool(d):
    return next((t for t in tools() if t["dir"] == d), None)


EVENTS, EV_LOCK = [], threading.Lock()


def start(t):
    cwd = os.path.join(BASE, t["dir"])
    env = {**os.environ, "PORT": str(t["port"]), "WORKSPACE": os.path.join(DATA, t["dir"]), "BIND": "127.0.0.1"}  # BIND: 도구는 포털을 거쳐서만(사용자 헤더 위조·우회 차단)
    if t.get("gpus"):  # 예외용 고정: tools.json 의 "gpus": "0". 보통은 비워 두고 도구가 그때그때 여유 많은 GPU 를 고른다(gpu_pick.py)
        env["CUDA_VISIBLE_DEVICES"] = str(t["gpus"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        cmd = ["bash", "setup.sh"]
    else:  # ponytail: setup.sh 없는 도구는 app.py 직접 (saju-local)
        py = os.path.join(cwd, "venv", "bin", "python")
        cmd = [py if os.path.exists(py) else sys.executable, "app.py"]
    subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(cwd, "server.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)


def port_pids(port):
    """그 TCP 포트에서 LISTEN 중인 프로세스 pid — /proc 만 본다(lsof·fuser·ss 없는 컨테이너용)"""
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f).read().splitlines()[1:]:
                c = line.split()
                if c[3] == "0A" and int(c[1].rsplit(":", 1)[1], 16) == port: inodes.add(c[9])  # 0A = LISTEN
        except OSError:
            pass
    pids = set()
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid(): continue
        try:
            for fd in os.listdir(f"/proc/{p}/fd"):
                if os.readlink(f"/proc/{p}/fd/{fd}")[8:-1] in inodes: pids.add(int(p)); break  # socket:[inode]
        except OSError:
            pass
    return pids


def stop(t):
    cwd = os.path.join(BASE, t["dir"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        subprocess.run(["bash", "setup.sh", "stop"], cwd=cwd, capture_output=True)
    for pid in port_pids(t["port"]):  # setup.sh 가 없거나 pid 파일이 없는 도구 — lsof 없는 서버에서도
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def add_tool(t):
    """내 에이전트 등록: tools.json 에 한 줄. 폴더가 생기면 카드가 '미설치'→'중지됨'으로 바뀐다."""
    d = t.get("dir", "")
    if not d.replace("-", "").replace("_", "").isalnum():
        raise ValueError("폴더 이름은 영문·숫자·-_ 만")
    ts = tools()
    if tool(d):
        raise ValueError("이미 있는 도구")
    port = int(t.get("port") or 0)
    if not (1024 < port < 65536) or any(x["port"] == port for x in ts):
        raise ValueError("포트가 비어 있지 않거나 범위 밖")
    t = {k: re.sub(r"[<>\"'`\\]", "", str(v))[:200] for k, v in t.items()}  # 카드에 그대로 그려지므로 HTML·따옴표 문자 제거(다른 사람 화면에서 스크립트 실행 방지)
    row = {"dir": d, "port": port, "name": t.get("name") or d, "title": t.get("title") or d, "desc": t.get("desc", ""), "group": t.get("group") or "기타",
           "tags": [x for x in (t.get("tags") or "").replace("#", "").split() if x], "dept": t.get("dept", ""), "author": t.get("author", ""),
           "added": datetime.date.today().isoformat()}
    row["tags"] = ["#" + x for x in row["tags"]]
    ts.append(row)
    json.dump(ts, open(TOOLS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return row


def guide(d):
    p = os.path.join(ROOT, "guides", f"{d}.md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else f"## {d}\n\n사용법이 아직 없습니다. `portal/guides/{d}.md` 를 만들면 여기에 뜹니다."


# ── GPU 현황 (/api/gpu, 5초 캐시) ─────────────────────────────────────────
_GPU = {"t": 0, "v": None}


def _smi(q, kind):
    r = subprocess.run(["nvidia-smi", f"--query-{kind}={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    return [[x.strip() for x in l.split(",")] for l in r.stdout.splitlines() if l.strip()]


def _owner_of(pid):
    """pid → 도구 이름: 자기·부모 프로세스의 작업 폴더가 ~/agent-page/<dir> 이면 그 도구"""
    names = {t["dir"]: t.get("name") or t["dir"] for t in tools()}
    for _ in range(6):
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
        if "ollama" in cwd:
            return "LLM (Ollama)"
        if cwd.startswith(BASE + os.sep):
            d = cwd[len(BASE) + 1:].split(os.sep)[0]
            if d in names:
                return names[d]
        try:
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])  # ppid
        except (OSError, ValueError, IndexError):
            return None
        if pid <= 1:
            return None


def _local_gpu_procs():
    """이 컨테이너에서 GPU 를 쓰는 프로세스: [(시작시각, pid, {gpu index})] — /proc/<pid>/maps 의 /dev/nvidiaN"""
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


def gpu_status():
    if time.time() - _GPU["t"] < 5 and _GPU["v"]:
        return _GPU["v"]
    try:
        gpus = [{"index": int(i), "uuid": u, "name": n, "used": int(mu), "total": int(mt), "util": int(ut) if ut.isdigit() else None, "procs": []}
                for i, u, n, mu, mt, ut in _smi("index,uuid,name,memory.used,memory.total,utilization.gpu", "gpu")]
        apps = _smi("gpu_uuid,pid,used_memory", "compute-apps")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"gpus": [], "error": f"nvidia-smi 실패: {type(e).__name__}"}
    by_uuid = {g["uuid"]: g for g in gpus}
    host = {}  # nvidia-smi 의 pid 는 호스트 pid(컨테이너 안 /proc 에 없음) → 생성 순서 + 쓰는 GPU 집합으로 이 컨테이너 프로세스와 짝짓기(추정)
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
    _GPU.update(t=time.time(), v={"gpus": gpus, "t": int(time.time())})
    return _GPU["v"]


# ── 매일 백업 (../backup.sh, 03시 KST) ────────────────────────────────────
def backup_loop():
    """cron·systemd 없는 컨테이너라 포털이 부른다: 03시대(KST)에 마지막 백업이 20시간 넘었으면, 또는 30시간 넘게 못 했으면 실행"""
    out = os.environ.get("AGENT_BACKUP_DIR") or os.path.expanduser("~/agent-page-backup")
    kst = datetime.timezone(datetime.timedelta(hours=9))
    while True:
        try:
            ages = [time.time() - os.path.getmtime(os.path.join(out, f)) for f in os.listdir(out) if f.startswith("agent-data-")] if os.path.isdir(out) else []
            age = min(ages) / 3600 if ages else 1e9
            if (datetime.datetime.now(kst).hour == 3 and age > 20) or age > 30:
                sh = os.path.join(BASE, "backup.sh")
                subprocess.run(["bash", sh if os.path.exists(sh) else os.path.join(ROOT, "scripts", "backup.sh")], timeout=3600)
        except Exception as e:  # 백업 실패가 포털을 죽이면 안 됨
            print("backup:", e, flush=True)
        time.sleep(600)


# ── HTTP ────────────────────────────────────────────────────────────────

# ── 저작권 표기 (LICENSE·NOTICE 참고) ─────────────────────────────────────
_SIG = __import__("base64").b64decode("wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==").decode()
_SIG_A = __import__("base64").b64decode("Z2dnZzg2NTcgPGRvbmdqdWtpbS5kZXZAZ21haWwuY29tPg==").decode()


def signed(html):
    """화면에 저작권 표기를 붙인다. ui.html 에서 지워져도 서버가 내보낼 때 다시 붙는다."""
    name, mail = _SIG.split(" · ")
    if 'name="author"' not in html:
        meta = f'<meta name="author" content="{name[7:]} <{mail}>">'
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + meta, 1)
        else:  # <head> 없는 화면: <!doctype> 앞에 넣으면 브라우저가 호환 모드(quirks)로 그림 → doctype 뒤에
            m = re.match(r"\s*<!doctype[^>]*>", html, re.I)
            html = html[:m.end()] + meta + html[m.end():] if m else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name} · 내부 시험용 · 배포용 아님</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


# ── 화면 테마 ───────────────────────────────────────────────────────────
def theme_files(pref, kind, d=""):
    """그 테마로 화면에 붙일 /theme 파일 — boot.js 의 files() 와 같은 목록"""
    if pref == "classic-light":
        return []
    f, light = ["tokens.css"], pref.endswith("-light")
    if kind == "tool":
        f += (["base-trendy.css"] if pref.startswith("trendy") else []) + ([] if light else ["base-dark.css", "dark.js"]) + ["chrome.css"]
        f += [f"tools/{d}.css"] if os.path.isfile(os.path.join(THEME_DIR, "tools", f"{d}.css")) else []
    else:
        f += ["guide.css"] if kind == "guide" else ["portal.css", "chrome.css"]
    return f


def theme_head(pref, kind, d=""):
    v = str(int(max(os.path.getmtime(os.path.join(r, x)) for r, _, fs in os.walk(THEME_DIR) for x in fs)))  # 고치면 바로 새 파일(캐시 무시)
    tags = "".join(f'<script src="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}"></script>' if f.endswith(".js") else
                   f'<link rel="stylesheet" href="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}">' for f in theme_files(pref, kind, d))
    js = BOOT.replace("%KIND%", kind).replace("%DIR%", d).replace("%V%", v).replace("%PREF%", pref).replace("%TOOLCSS%", "true" if f"tools/{d}.css" in theme_files("trendy-dark", kind, d) else "false")
    if kind == "portal":  # 테마 고르기(위쪽 막대·계정 설정) — 모든 테마에서
        tags += f'<script src="/theme/picker.js?v={v}" defer></script>'
    return f"<script>{js}</script>{tags}"


def first_html(rest):
    """/t/<도구>/ 뒤 부분이 도구 첫 화면인가(사용법·테마를 붙일 곳): 쿼리 빼고 '' 또는 index.html. 딥링크(?job=…)도 포함.
    단 파동이 펫(?pet=1)은 다른 화면 위에 뜨는 투명 iframe 이라 제외 — 테마 바탕색·? 버튼이 펫 뒤를 칠하면 안 됨"""
    path, _, q = rest.partition("?")
    return path in ("", "index.html") and "1" not in urllib.parse.parse_qs(q).get("pet", [])


def themed(html, head):
    """테마 머리를 <meta charset> 뒤(없으면 <head> 뒤, 문서 맨 앞)에 — 도구 스타일보다 먼저, 본문 그리기 전에"""
    m = re.search(r"<meta\s+charset[^>]*>", html[:2048], re.I) or re.search(r"<head(\s[^>]*)?>", html[:4096], re.I) or re.search(r"<!doctype[^>]*>", html[:512], re.I)
    i = m.end() if m else 0
    return html[:i] + head + html[i:]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def auth(self):
        """요청마다: 쿠키 agsid → 계정. 쿠키 없이 이 서버 안(127.0.0.1)에서 온 요청 = 주인. 밖에서 온 X-Agent-User·X-Forwarded-User 등은 안 믿음"""
        self.uid, self.uname, self._setcookie = None, None, None
        m = re.search(r"(?:^|;\s*)agsid=([A-Za-z0-9_-]+)", self.headers.get("Cookie") or "")
        if m:
            with db() as c:
                r = c.execute("SELECT s.uid, s.exp, u.name, u.seen FROM sessions s JOIN users u ON u.id=s.uid WHERE s.token=? AND s.exp>? AND NOT u.disabled",
                              (hashlib.sha256(m[1].encode()).hexdigest(), time.time())).fetchone()
                if r:
                    self.uid, self.uname = r["uid"], r["name"]
                    if (r["seen"] or "") < (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds"):  # 마지막 접속(5분 단위)
                        c.execute("UPDATE users SET seen=? WHERE id=?", (now(), r["uid"]))
                    if r["exp"] - time.time() < SESSION_S - 86400:  # 하루에 한 번 만료 연장(슬라이딩)
                        c.execute("UPDATE sessions SET exp=? WHERE token=?", (time.time() + SESSION_S, hashlib.sha256(m[1].encode()).hexdigest()))
                        self._setcookie = cookie(m[1])
        if not self.uid and not m and self.client_address[0] in LOCAL:  # 쿠키가 있는데 무효(로그아웃·정지·만료)면 로컬이어도 다시 로그인
            self.uid, self.uname = "owner", "서버 주인(로컬)"
        return self.uid

    def theme(self):
        """고른 테마: 계정 DB → 쿠키 agtheme(로그인 전·로컬 접속) → classic-light"""
        t = None
        if getattr(self, "uid", None) and self.uid != "owner":
            with db() as c:
                r = c.execute("SELECT theme FROM users WHERE id=?", (self.uid,)).fetchone()
                t = r and r[0]
        if not t:
            m = re.search(r"(?:^|;\s*)agtheme=([a-z-]+)", self.headers.get("Cookie") or "")
            t = m and m[1]
        return t if t in THEMES else "classic-light"

    def html_page(self, html, kind, d=""):
        return signed(themed(html, theme_head(self.theme(), kind, d))).encode()

    @property
    def user(self):  # 계정 id (통계·좋아요·피드 키)
        return self.uid

    @property
    def is_owner(self):
        return self.uid == "owner" or self.uid in OWNERS

    @property
    def agent_user(self):  # 도구로 넘기는 사용자 id — 서버 주인은 owner, 나머지는 계정 id
        return "owner" if self.is_owner else self.uid

    def setup(self):  # https: TLS 핸드셰이크는 요청 스레드에서(느린 접속이 accept 를 막지 않게). http 로 잘못 온 접속 등 실패는 조용히 끊음
        super().setup()
        self._tls_bad = False
        if isinstance(self.connection, ssl.SSLSocket):
            try:
                self.connection.settimeout(15); self.connection.do_handshake(); self.connection.settimeout(None)
            except (ssl.SSLError, OSError):
                self._tls_bad = True

    def handle(self):
        if not self._tls_bad:
            super().handle()

    def finish(self):
        try:
            super().finish()
        except OSError:
            pass

    def me(self, c):
        """화면 위쪽 막대·계정 설정용: 이름·역할·부서·승인 대기 수"""
        u = c.execute("SELECT dept FROM users WHERE id=?", (self.uid,)).fetchone()
        pend = c.execute("SELECT dept FROM requests WHERE uid=? AND kind='dept_join' AND status='pending'", (self.uid,)).fetchone()
        n = sum(can_decide(c, self.uid, r) for r in c.execute("SELECT * FROM requests WHERE status='pending'"))
        return {"id": self.uid, "name": self.uname, "owner": self.is_owner, "agent_user": self.agent_user, "role": role(c, self.uid),
                "dept": u["dept"] if u else "", "dept_pending": pend["dept"] if pend else "", "heads": my_heads(c, self.uid), "approvals": n,
                "account": bool(u)}

    def page(self, p):
        """/account · /approvals · /admin — 같은 화면 파일, 권한 없는 쪽은 /account 로"""
        with db() as c:
            ok = p == "/account" or self.is_owner or (p == "/approvals" and my_heads(c, self.uid))
        if not ok:
            self.send_response(302); self.send_header("Location", "/account"); self.send_header("Content-Length", "0"); self.end_headers(); return
        self._send(self.html_page(ACCOUNT + self.help_html("portal"), "portal"), "text/html; charset=utf-8")

    def manage_get(self, p):
        with db() as c:
            if p == "/api/account":
                return self._send({"me": self.me(c), "depts": visible_depts(c), "requests": req_rows(c, "uid=?", (self.uid,))[:20]})
            if p == "/api/account/theme":
                return self._send({"theme": self.theme(), "themes": sorted(THEMES)})
            if p == "/api/account/notices":  # 내 요청 결과(한 번만) — help.html 이 화면 알림으로
                rows = req_rows(c, "uid=? AND status IN ('approved','rejected') AND notified=0", (self.uid,))
                c.execute("UPDATE requests SET notified=1 WHERE uid=? AND status IN ('approved','rejected')", (self.uid,))
                return self._send({"notices": rows, "approvals": self.me(c)["approvals"]})
            if p == "/api/approvals":
                heads = my_heads(c, self.uid)
                if not (self.is_owner or heads):
                    return self._send({"error": "부서장·관리자만 볼 수 있습니다"}, code=403)
                pend = [r for r in req_rows(c, "status='pending'") if can_decide(c, self.uid, r)]
                cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
                members = {d: [dict(r) for r in c.execute(
                    "SELECT u.id, u.name, u.seen, (SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open') total, "
                    "(SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open' AND e.ts>=?) recent, "
                    "EXISTS(SELECT 1 FROM dept_heads h WHERE h.dept=u.dept AND h.uid=u.id) head FROM users u WHERE u.dept=? AND NOT u.disabled ORDER BY u.name", (cut, d))]
                    for d in heads}
                done = [r for r in req_rows(c, "status IN ('approved','rejected') AND approver=?", (self.uid,))][:20]
                return self._send({"pending": pend, "members": members, "done": done})
            if p == "/api/admin":
                if not self.is_owner:
                    return self._send({"error": "관리자만 볼 수 있습니다"}, code=403)
                heads = {}
                for r in c.execute("SELECT dept, uid FROM dept_heads"):
                    heads.setdefault(r["uid"], []).append(r["dept"])
                users = [dict(r) | {"role": "관리자" if r["id"] in OWNERS else "부서장" if r["id"] in heads else "일반", "heads": heads.get(r["id"], [])}
                         for r in c.execute("SELECT id, name, dept, ts, seen, disabled FROM users ORDER BY ts")]
                depts = [dict(r) | {"members": c.execute("SELECT COUNT(*) FROM users WHERE dept=?", (r["name"],)).fetchone()[0], "heads": heads_of(c, r["name"])}
                         for r in c.execute("SELECT name, hidden FROM depts ORDER BY hidden, name")]
                return self._send({"users": users, "depts": depts, "pending": req_rows(c, "status='pending'"), "owners": sorted(OWNERS),
                                   "audit": [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY rowid DESC LIMIT 60")]})
        return self._send({"error": "not found"}, code=404)

    def manage_post(self, c, p, req):
        """계정 설정·승인·관리 — 모든 호출에서 서버가 권한 확인(403), 승인·부여·회수·초기화·정지는 audit 에 남김"""
        acct = c.execute("SELECT 1 FROM users WHERE id=?", (self.uid,)).fetchone()
        if p == "/api/account/theme":  # 계정에 저장 + 쿠키(로그인 화면·로컬 접속용)
            t = str(req.get("theme", ""))
            if t not in THEMES:
                raise ValueError("없는 테마")
            if acct:
                c.execute("UPDATE users SET theme=? WHERE id=?", (t, self.uid))
            return self._send({"ok": True, "theme": t}, headers=[("Set-Cookie", f"agtheme={t}; Path=/; Max-Age=31536000; SameSite=Lax" + ("; Secure" if TLS else ""))])
        if p.startswith("/api/account/"):
            if not acct:
                raise ValueError("로컬 접속은 계정이 없습니다 — 계정으로 로그인하세요")
            if p == "/api/account/name":
                nm = str(req.get("name", "")).strip()[:40]
                if not nm:
                    raise ValueError("이름을 적어 주세요")
                c.execute("UPDATE users SET name=? WHERE id=?", (nm, self.uid))
            elif p == "/api/account/dept":
                d = str(req.get("dept", ""))
                if d == (c.execute("SELECT dept FROM users WHERE id=?", (self.uid,)).fetchone()[0] or ""):
                    raise ValueError("이미 그 부서입니다")
                new_request(c, self.uid, "dept_join", d)
            elif p == "/api/account/head":
                d = c.execute("SELECT dept FROM users WHERE id=?", (self.uid,)).fetchone()[0]
                if not d:
                    raise ValueError("부서 소속이 승인된 뒤에 요청할 수 있습니다")
                if self.uid in heads_of(c, d):
                    raise ValueError("이미 부서장입니다")
                new_request(c, self.uid, "dept_head", d)
            elif p == "/api/account/cancel":
                if not c.execute("UPDATE requests SET status='cancelled', decided_ts=? WHERE id=? AND uid=? AND status='pending'", (now(), int(req.get("id") or 0), self.uid)).rowcount:
                    raise ValueError("취소할 수 있는 요청이 아닙니다")
            else:
                return self._send({"error": "not found"}, code=404)
            return self._send({"ok": True})
        if p == "/api/approvals/decide":
            decide(c, self.uid, int(req.get("id") or 0), bool(req.get("ok")), str(req.get("note") or "").strip())
            return self._send({"ok": True})
        if not p.startswith("/api/admin/"):
            return self._send({"error": "not found"}, code=404)
        if not self.is_owner:
            raise PermissionError("관리자만 할 수 있습니다")
        if p == "/api/admin/dept":
            op, name, new = req.get("op"), str(req.get("name", "")).strip()[:40], str(req.get("new", "")).strip()[:40]
            if op == "add":
                if not name or re.search(r"[<>\"'&]", name):
                    raise ValueError("부서 이름을 확인하세요")
                c.execute("INSERT INTO depts(name, ts) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET hidden=0", (name, now()))
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
            audit(c, self.uid, "dept_" + op, name, new)
        elif p == "/api/admin/head":
            d, u = str(req.get("dept", "")), str(req.get("uid", ""))
            if req.get("op") == "grant":
                if not c.execute("SELECT 1 FROM users WHERE id=? AND dept=?", (u, d)).fetchone():
                    raise ValueError("그 부서 소속(승인된) 계정만 부서장이 될 수 있습니다")
                c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (d, u, now()))
            elif req.get("op") == "revoke":
                c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, u))
            else:
                raise ValueError("op")
            audit(c, self.uid, "head_" + req["op"], u, d)
        elif p in ("/api/admin/reset", "/api/admin/disable"):
            u = str(req.get("uid", ""))
            if not c.execute("SELECT 1 FROM users WHERE id=?", (u,)).fetchone():
                raise ValueError("없는 계정")
            if p.endswith("reset"):  # 무작위 PIN — 응답으로 한 번만 보여 주고 어디에도 안 남김
                pin = f"{secrets.randbelow(10**6):06d}"
                set_pin(c, u, pin); c.execute("DELETE FROM sessions WHERE uid=?", (u,)); audit(c, self.uid, "pin_reset", u)
                return self._send({"ok": True, "pin": pin})
            off = bool(req.get("disabled"))
            if off and u in OWNERS:
                raise ValueError("관리자 계정은 정지할 수 없습니다")
            c.execute("UPDATE users SET disabled=? WHERE id=?", (off, u))
            if off:
                c.execute("DELETE FROM sessions WHERE uid=?", (u,))
            audit(c, self.uid, "disable" if off else "enable", u)
        else:
            return self._send({"error": "not found"}, code=404)
        return self._send({"ok": True})

    def need_login(self):
        if self.command == "GET" and "text/html" in (self.headers.get("Accept") or ""):  # 화면 → 로그인 페이지로
            self.send_response(302); self.send_header("Location", "/login?next=" + urllib.parse.quote(self.path, safe=""))
            self.send_header("Content-Length", "0"); self.end_headers(); return
        self._send({"error": "로그인이 필요합니다", "login": "/login"}, code=401)

    def help_html(self, d):
        return HELP.replace("%DIR%", d).replace("%USER%", self.agent_user)

    def _send(self, body, ctype="application/json", code=200, headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("X-Author", _SIG_A); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        for k, v in headers or ():
            self.send_header(k, v)
        if getattr(self, "_setcookie", None):
            self.send_header("Set-Cookie", self._setcookie)
        self.end_headers(); self.wfile.write(b)

    def guide_file(self, p):
        """사용 설명서(guide/ — tools/build.py 가 만듦): /guide/ · /guide/img/<그림> · /guide/ext/<도구>/<그림> · /guide/agent-portal.pdf · /guide/fonts/<글꼴>.woff2. 이름 규칙에 맞는 파일만"""
        if p == "/guide":  # 상대 경로(img/…)가 맞게 / 붙여서. #도구 앵커는 브라우저가 유지
            self.send_response(302); self.send_header("Location", "/guide/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        rel = urllib.parse.unquote(p[len("/guide/"):]) or "index.html"
        m = re.fullmatch(r"index\.html|agent-portal\.pdf|img/[A-Za-z0-9][\w.-]*\.(png|jpe?g|svg|webp)|fonts/[A-Za-z0-9][\w.-]*\.woff2|fonts/OFL\.txt", rel)
        top = GUIDE_DIR
        e = re.fullmatch(r"ext/([a-z0-9-]+-local)/([A-Za-z0-9][\w.-]*\.(png|jpe?g|webp))", rel)
        if e:  # 공개 저장소에 넣으면 안 되는 그림(예: 파동이 — 캐릭터 권리)은 비공개 도구 저장소 static/guide/ 에 두고 여기서 내보냄
            m, top, rel = e, os.path.join(BASE, e[1], "static", "guide"), e[2]
        f = os.path.join(top, rel)
        if not m or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
            return self._send({"error": "not found"}, code=404)
        st = os.stat(f); lm = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(st.st_mtime)); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
        cc = ("Cache-Control", "no-cache" if rel == "index.html" else "private, max-age=3600")  # 로그인 뒤 내용 → 공용 캐시 금지
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.send_header("ETag", etag); self.send_header(*cc); self.send_header("Content-Length", "0"); self.end_headers(); return
        ext = rel.rsplit(".", 1)[1]
        ctype = {"html": "text/html; charset=utf-8", "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "svg": "image/svg+xml", "webp": "image/webp", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[ext]
        hd = [cc, ("ETag", etag), ("Last-Modified", lm), ("X-Content-Type-Options", "nosniff")]
        if ext == "pdf":
            hd.append(("Content-Disposition", "attachment; filename=agent-portal.pdf; filename*=UTF-8''" + urllib.parse.quote("에이전트-포털-사용-설명서.pdf")))
        body = open(f, "rb").read()
        self._send(self.html_page(body.decode("utf-8"), "guide") if ext == "html" else body, ctype, headers=hd)

    def theme_file(self, p):
        """/theme/<css|js> · /theme/tools/<도구>.css · /theme/fonts/<글꼴> (글꼴은 guide/fonts 와 같은 파일). 이름 규칙에 맞는 파일만"""
        rel = urllib.parse.unquote(p[len("/theme/"):])
        m = re.fullmatch(r"[a-z][a-z0-9-]*\.(css|js)|tools/[a-z0-9][a-z0-9_-]*\.(css)|fonts/([A-Za-z0-9][\w.-]*\.(woff2|txt))", rel)
        top = os.path.join(GUIDE_DIR, "fonts") if m and m[3] else THEME_DIR
        f = os.path.join(top, m[3] if m and m[3] else rel)
        if not m or rel == "boot.js" or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
            return self._send({"error": "not found"}, code=404)
        st = os.stat(f); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
        cc = ("Cache-Control", "public, max-age=86400")  # 화면에는 ?v=수정시각 으로 붙여서 고치면 바로 바뀜
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304); self.send_header("ETag", etag); self.send_header(*cc); self.send_header("Content-Length", "0"); self.end_headers(); return
        ctype = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[f.rsplit(".", 1)[1]]
        self._send(open(f, "rb").read(), ctype, headers=[cc, ("ETag", etag), ("X-Content-Type-Options", "nosniff")])

    def proxy(self):
        t0 = time.time()
        try:
            return self._proxy()
        finally:   # 오래 걸린 도구 작업(1분 넘는 POST)이 끝나면 기록 → 파동이 펫이 "작업 끝났어요!" (/api/events)
            parts = self.path.split("/", 3); sec = time.time() - t0
            if self.command == "POST" and sec > 60 and len(parts) > 2 and parts[2] != "padong-local" and tool(parts[2]):
                with EV_LOCK:
                    EVENTS.append({"id": (EVENTS[-1]["id"] + 1) if EVENTS else int(t0), "dir": parts[2], "name": tool(parts[2]).get("name", parts[2]), "user": self.agent_user,
                                   "ok": getattr(self, "_status", 200) < 400, "sec": round(sec), "t": int(time.time())})
                    del EVENTS[:-30]

    def _proxy(self):
        """/t/<dir>/<rest> → http://127.0.0.1:<port>/<rest>. 첫 화면 HTML 에는 사용법 팝업을 붙이고, 나머지(SSE 포함)는 그대로 흘린다."""
        parts = self.path.split("/", 3)
        t = tool(parts[2]) if len(parts) > 2 else None
        if not t:
            return self._send({"error": "unknown tool"}, code=404)
        if len(parts) < 4:
            self.send_response(302); self.send_header("Location", f"/t/{t['dir']}/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = int(self.headers.get("Content-Length") or 0)
        req = urllib.request.Request(f"http://127.0.0.1:{t['port']}/{parts[3]}", data=self.rfile.read(n) if n else None,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in HOP | {"x-agent-user", "x-forwarded-user", "x-user", "cookie"}}
                                     | {"X-Agent-User": self.agent_user} | ({"Cookie": ck} if (ck := re.sub(r"(?:^|;\s*)ag(?:sid|theme)=[^;]*", "", self.headers.get("Cookie") or "").strip("; ")) else {}),
                                     method=self.command)  # 클라이언트가 보낸 사용자 헤더는 버리고 포털이 정한 값만, 포털 세션 쿠키는 도구로 안 넘김
        try:
            r = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            return self._send({"error": f"{t['dir']} 응답 없음 ({type(e).__name__}) — 포털에서 '시작'을 누르세요"}, code=502)
        inject = self.command == "GET" and first_html(parts[3]) and "text/html" in (r.headers.get("Content-Type") or "")
        body = self.html_page(r.read().decode("utf-8", "replace") + self.help_html(t["dir"]), "tool", t["dir"]) if inject else None  # 도구 화면에도 저작자 표기 + 테마
        self._status = r.status
        self.send_response(r.status)
        if self._setcookie:
            self.send_header("Set-Cookie", self._setcookie)
        for k, v in r.headers.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        length = str(len(body)) if inject else r.headers.get("Content-Length")
        if length:
            self.send_header("Content-Length", length)
        else:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            if inject:
                self.wfile.write(body); return
            while chunk := r.read1(65536):
                self.wfile.write(chunk); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            r.close()
            if not length:
                self.close_connection = True

    def login(self):
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return self._send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
        uid, pin = str(req.get("id", "")).strip().lower(), str(req.get("pin", ""))
        try:
            with db() as c:
                if self.path == "/api/signup":
                    check_new(c, uid, str(req.get("name", "")), pin, self.client_address[0] in LOCAL)
                    d = str(req.get("dept") or "")
                    if d and d not in visible_depts(c):
                        raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
                    set_user(c, uid, str(req["name"]).strip()[:40], "", pin)  # 부서는 승인 뒤에 들어감
                    if d:
                        new_request(c, uid, "dept_join", d)
                else:
                    if limited(uid):
                        raise ValueError("PIN 을 여러 번 틀렸습니다. 10분 뒤에 다시 해 주세요")
                    if not check_pin(c, uid, pin):
                        with FAIL_LOCK:
                            FAILS.setdefault(uid, []).append(time.time())
                        raise ValueError("아이디 또는 PIN 이 틀립니다 (정지된 계정이면 관리자에게)")
                    with FAIL_LOCK:
                        FAILS.pop(uid, None)
                tok = new_session(c, uid)
            self._send({"ok": True, "id": uid}, headers=[("Set-Cookie", cookie(tok))])
        except (ValueError, KeyError) as e:
            self._send({"error": str(e)}, code=400)

    def do_GET(self):
        self._setcookie, self.uid = None, None  # keep-alive 로 같은 연결에서 앞 요청 값이 남지 않게
        p = self.path.split("?")[0]

        if p == "/login":
            return self._send(self.html_page(LOGIN, "portal"), "text/html; charset=utf-8")
        if p.startswith("/theme/"):  # 테마 CSS·글꼴 — 로그인 화면도 쓰므로 로그인 전에
            return self.theme_file(p)
        if p == "/api/depts":  # 가입 화면의 부서 목록(로그인 전)
            with db() as c:
                return self._send({"depts": visible_depts(c)})
        if p == "/logout":
            m = re.search(r"(?:^|;\s*)agsid=([A-Za-z0-9_-]+)", self.headers.get("Cookie") or "")
            if m:
                with db() as c:
                    c.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(m[1].encode()).hexdigest(),))
            self.send_response(302); self.send_header("Location", "/login"); self.send_header("Set-Cookie", cookie("", 0))
            self.send_header("Content-Length", "0"); self.end_headers(); return
        if not self.auth():
            return self.need_login()
        if p == "/api/me":
            with db() as c:
                return self._send(self.me(c))
        if p in ("/account", "/approvals", "/admin"):
            return self.page(p)
        if p == "/api/account" or p.startswith(("/api/account/", "/api/approvals", "/api/admin")):
            return self.manage_get(p)
        if p.startswith("/t/"):
            return self.proxy()
        if p == "/api/events":   # 오래 걸린 작업 끝남 (since=마지막 id)
            since = int((self.path.split("since=") + ["0"])[1].split("&")[0] or 0)
            me = self.agent_user  # 내 작업만(서버 주인은 전부)
            with EV_LOCK:
                return self._send({"events": [e for e in EVENTS if e["id"] > since and me in ("owner", e["user"])], "last": EVENTS[-1]["id"] if EVENTS else since})
        if p == "/api/gpu":
            return self._send(gpu_status())
        if p == "/api/tools":
            with db() as c:
                u = usage(c)
                return self._send({"tools": [{**t, "up": alive(t["port"]), "installed": os.path.isdir(os.path.join(BASE, t["dir"])),
                                              "url": f"/t/{t['dir']}/", "usage": u.get(t["dir"], {"recent": 0, "total": 0, "users": 0, "likes": 0})} for t in tools() if SHOW_HIDDEN or not t.get("hidden")],  # hidden: 토이 등 목록 비노출(프록시는 됨)
                                   "rank": user_rank(c), "posts": posts(c, self.user), "me": self.me(c)})
        if p.startswith("/api/guide/"):
            d = p.split("/")[-1]
            return self._send(guide(d).encode() if (tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
        if p.startswith("/api/readme/"):
            d = p.split("/")[-1]
            f = os.path.join(BASE, d, "README.md") if tool(d) else ""
            return self._send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
        if p == "/guide" or p.startswith("/guide/"):
            return self.guide_file(p)
        if p != "/":
            return self._send({"error": "not found"}, code=404)
        self._send(self.html_page(HTML + self.help_html("portal"), "portal"), "text/html; charset=utf-8")

    def do_POST(self):
        self._setcookie = None  # keep-alive 로 같은 연결에서 앞 요청 값이 남지 않게
        if self.path in ("/api/login", "/api/signup"):
            return self.login()
        if not self.auth():
            n = int(self.headers.get("Content-Length") or 0)
            n and self.rfile.read(n)
            return self.need_login()
        if self.path.startswith("/t/"):
            return self.proxy()
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            return self._send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
        try:
            with db() as c:
                if self.path == "/api/pin":  # PIN 바꾸기: 지금 PIN 확인 → 새 PIN, 다른 기기 세션은 끊음
                    if self.uid == "owner":
                        raise ValueError("로컬 접속은 계정이 없습니다 — 로그인 후 바꾸세요")
                    if limited(self.uid) or not check_pin(c, self.uid, req.get("pin", "")):
                        with FAIL_LOCK:
                            FAILS.setdefault(self.uid, []).append(time.time())
                        raise ValueError("지금 PIN 이 틀립니다 (10분에 5번까지)")
                    if not PIN_RE.match(req.get("new", "")):
                        raise ValueError("새 PIN 은 숫자 4~8자리")
                    set_pin(c, self.uid, req["new"])
                    c.execute("DELETE FROM sessions WHERE uid=?", (self.uid,))
                    tok = new_session(c, self.uid)
                    return self._send({"ok": True}, headers=[("Set-Cookie", cookie(tok))])
                if self.path.startswith(("/api/account/", "/api/approvals/", "/api/admin/")):
                    return self.manage_post(c, self.path, req)
                if self.path == "/api/post":
                    if not (req.get("title") or "").strip():
                        raise ValueError("제목이 비었습니다")
                    c.execute("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES(?,?,?,?,?,?)",
                              (req["title"].strip(), req.get("body", "").strip(), self.uname, req.get("dept", ""),  # 작성자는 로그인 계정 이름(남의 이름으로 쓰기 방지)
                               json.dumps(["#" + x.strip("#") for x in (req.get("tags") or "").split() if x]), now()))
                    return self._send({"ok": True})
                if self.path == "/api/post_like":
                    c.execute("INSERT OR IGNORE INTO post_likes(post,user) VALUES(?,?)", (int(req["id"]), self.user))
                    return self._send({"ok": True})
                if self.path == "/api/tool_add":
                    return self._send(add_tool(req))
                t = tool(req.get("dir"))
                if not t:
                    return self._send({"error": "unknown tool"}, code=404)
                if self.path in ("/api/open", "/api/like"):
                    c.execute("INSERT INTO events VALUES(?,?,?,?)", (self.path[5:], t["dir"], now(), self.user))
                elif self.path == "/api/start":  # 꺼진 도구 켜기는 누구나, 이미 켜진 도구 재시작은 관리자만
                    if alive(t["port"]) and not self.is_owner:
                        return self._send({"error": "이미 실행 중입니다"}, code=409)
                    start(t)
                elif self.path == "/api/stop":
                    if not self.is_owner:
                        return self._send({"error": "도구 중지는 관리자만 할 수 있습니다"}, code=403)
                    stop(t)
                else:
                    return self._send({"error": "not found"}, code=404)
                self._send({"ok": True})
        except PermissionError as e:
            self._send({"error": str(e)}, code=403)
        except (ValueError, KeyError) as e:
            self._send({"error": str(e)}, code=400)


if __name__ == "__main__":
    if "--add-user" in sys.argv:  # 계정 만들기·PIN 초기화 (관리자). PIN 은 화면에만 — 로그·파일에 안 남김
        a = sys.argv; arg = lambda k, d="": a[a.index(k) + 1] if k in a else d
        uid, pw = arg("--add-user").lower(), arg("--password")  # --password: 관리자 계정용 문자 비밀번호(6~64자, 로그인 칸에 PIN 대신)
        pin = pw or arg("--pin") or f"{secrets.randbelow(10**6):06d}"
        if not ID_RE.match(uid) or uid == "owner" or not (PIN_RE.match(pin) or (pw and 6 <= len(pw) <= 64)):
            sys.exit("아이디(영문 소문자·숫자 2~32자, owner 불가)·PIN(숫자 4~8) 또는 --password(6~64자) 확인")
        with db() as c:
            set_user(c, uid, arg("--name", uid), arg("--dept"), pin)
            c.execute("DELETE FROM sessions WHERE uid=?", (uid,))
        print(f"{uid} 계정 저장 (주인: {uid in OWNERS})" + ("" if "--pin" in a or pw else f"  PIN {pin}"))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("start-all", "stop-all"):
        for t in tools():
            if os.path.isdir(os.path.join(BASE, t["dir"])):
                print(sys.argv[1], t["dir"], t["port"]); (start if sys.argv[1] == "start-all" else stop)(t)
        sys.exit(0)
    if os.environ.get("BACKUP", "1" if PORT == 8700 else "0") == "1":
        threading.Thread(target=backup_loop, daemon=True).start()
    print(f"portal → {'https' if TLS else 'http'}://localhost:{PORT}  data={DATA}  owners={sorted(OWNERS)}  {_SIG}")
    srv = ThreadingHTTPServer((HOST, PORT), H)
    if TLS:  # 선택: 사내 CA 로 만든 인증서 (README 'https') — TLS 1.2 이상
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(os.environ["TLS_CERT"], os.environ["TLS_KEY"])
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        print("https 켜짐 —", os.environ["TLS_CERT"])
    srv.serve_forever()
