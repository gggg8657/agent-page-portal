#!/usr/bin/env python3
"""에이전트 페이지 포털 — 도구 목록·상태·시작/중지·리버스 프록시(/t/<도구>/)·사용 통계·피드. stdlib + sqlite.
  python3 portal.py                 # http://localhost:8700
  python3 portal.py start-all|stop-all
env: PORT(8700) AGENT_DATA(모든 도구 데이터 루트, 기본 ../_data)
     AGENT_ADMINS(쉼표 — 서버 주인으로 볼 IP·사용자, 기본 127.0.0.1 + 서버 자기 IP) BACKUP(1=매일 03시 ../backup.sh, 기본 :8700 만)"""
import datetime, json, os, re, socket, sqlite3, subprocess, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(ROOT)
PORT = int(os.environ.get("PORT", "8700"))
SHOW_HIDDEN = os.environ.get("SHOW_HIDDEN") == "1"  # 테스트 포털: 숨긴 도구도 목록에 (예: PORT=8701 HOST=127.0.0.1 SHOW_HIDDEN=1)
HOST = os.environ.get("HOST", "")
DATA = os.environ.get("AGENT_DATA") or os.path.join(BASE, "_data")  # 포털 DB + 모든 도구 workspace 가 이 아래로 모임
os.makedirs(DATA, exist_ok=True)
TOOLS_FILE = os.path.join(ROOT, "tools.json")


def _own_ips():
    ips = {"127.0.0.1", "::1"}
    try:
        ips |= set(subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split())
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        ips |= {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None)}
    except OSError:
        pass
    return ips


# 서버 주인 → 도구에 X-Agent-User: owner (예전 기록 전부 보임). 나머지 접속자는 자기 IP/사용자 이름별 폴더
ADMINS = {x.strip() for x in os.environ["AGENT_ADMINS"].split(",") if x.strip()} if os.environ.get("AGENT_ADMINS") else _own_ips()


def tools():  # 요청마다 다시 읽음 — 손으로 고치거나 다른 프로세스가 추가해도 새로고침이면 반영
    return json.load(open(TOOLS_FILE, encoding="utf-8"))

HTML = open(os.path.join(ROOT, "portal.html"), encoding="utf-8").read()
HELP = open(os.path.join(ROOT, "help.html"), encoding="utf-8").read()  # 도구 첫 화면에 끼워 넣는 ? 버튼 + 사용법
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
    CREATE TABLE IF NOT EXISTS post_likes(post INTEGER, user TEXT, UNIQUE(post, user));""")


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
    return [{"user": r["user"], "n": r["n"]} for r in
            c.execute("SELECT user, COUNT(*) n FROM events WHERE kind='open' AND ts>=? GROUP BY user ORDER BY n DESC LIMIT 5", (cut,))]


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
        html = html.replace("<head>", "<head>" + meta, 1) if "<head>" in html else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name}</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def user(self):  # ponytail: 로그인 없음 → IP. 사용자 헤더(X-Forwarded-User/X-User)는 이 서버 안의 리버스프록시가 보낸 것만 믿음(밖에서 보내면 위조 가능)
        ip = self.client_address[0]
        return (self.headers.get("X-Forwarded-User") or self.headers.get("X-User") or ip) if ip in ("127.0.0.1", "::1") else ip

    @property
    def agent_user(self):  # 도구로 넘기는 사용자 id — 서버 주인은 owner, 나머지는 [A-Za-z0-9_.-] 로 정리
        return "owner" if self.user in ADMINS else (re.sub(r"[^A-Za-z0-9_.-]", "_", self.user)[:64] or "_")

    def _send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("X-Author", _SIG_A); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

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
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != "x-agent-user"} | {"X-Agent-User": self.agent_user},
                                     method=self.command)  # 클라이언트가 보낸 X-Agent-User 는 버리고 포털이 정한 값만
        try:
            r = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            return self._send({"error": f"{t['dir']} 응답 없음 ({type(e).__name__}) — 포털에서 '시작'을 누르세요"}, code=502)
        inject = self.command == "GET" and parts[3] in ("", "index.html") and "text/html" in (r.headers.get("Content-Type") or "")
        body = signed(r.read().decode("utf-8", "replace") + HELP.replace("%DIR%", t["dir"])).encode() if inject else None  # 도구 화면에도 저작자 표기
        self._status = r.status
        self.send_response(r.status)
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

    def do_GET(self):
        p = self.path.split("?")[0]
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
                                   "rank": user_rank(c), "posts": posts(c, self.user), "me": self.user})
        if p.startswith("/api/guide/"):
            d = p.split("/")[-1]
            return self._send(guide(d).encode() if (tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
        if p.startswith("/api/readme/"):
            d = p.split("/")[-1]
            f = os.path.join(BASE, d, "README.md") if tool(d) else ""
            return self._send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
        if p != "/":
            return self._send({"error": "not found"}, code=404)
        self._send(signed(HTML + HELP.replace("%DIR%", "portal")).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.startswith("/t/"):
            return self.proxy()
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        try:
            with db() as c:
                if self.path == "/api/post":
                    if not (req.get("title") or "").strip():
                        raise ValueError("제목이 비었습니다")
                    c.execute("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES(?,?,?,?,?,?)",
                              (req["title"].strip(), req.get("body", "").strip(), req.get("author") or self.user, req.get("dept", ""),
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
                elif self.path == "/api/start":
                    start(t)
                elif self.path == "/api/stop":
                    stop(t)
                else:
                    return self._send({"error": "not found"}, code=404)
                self._send({"ok": True})
        except (ValueError, KeyError) as e:
            self._send({"error": str(e)}, code=400)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("start-all", "stop-all"):
        for t in tools():
            if os.path.isdir(os.path.join(BASE, t["dir"])):
                print(sys.argv[1], t["dir"], t["port"]); (start if sys.argv[1] == "start-all" else stop)(t)
        sys.exit(0)
    if os.environ.get("BACKUP", "1" if PORT == 8700 else "0") == "1":
        threading.Thread(target=backup_loop, daemon=True).start()
    print(f"portal → http://localhost:{PORT}  data={DATA}  admins={sorted(ADMINS)}  {_SIG}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
