#!/usr/bin/env python3
"""에이전트 페이지 포털 — 도구 목록·상태·시작/중지·리버스 프록시(/t/<도구>/)·사용 통계·피드. stdlib + sqlite.
  python3 portal.py                 # http://localhost:8700
  python3 portal.py start-all|stop-all
env: PORT(8700) PORTAL_HOST(링크 호스트) AGENT_DATA(모든 도구 데이터 루트, 기본 ../_data)"""
import datetime, json, os, socket, sqlite3, subprocess, sys, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(ROOT)
PORT = int(os.environ.get("PORT", "8700"))
HOST = os.environ.get("PORTAL_HOST", "localhost")
DATA = os.environ.get("AGENT_DATA") or os.path.join(BASE, "_data")  # 포털 DB + 모든 도구 workspace 가 이 아래로 모임
os.makedirs(DATA, exist_ok=True)
TOOLS_FILE = os.path.join(ROOT, "tools.json")
TOOLS = json.load(open(TOOLS_FILE, encoding="utf-8"))
HTML = open(os.path.join(ROOT, "portal.html"), encoding="utf-8").read()
HELP = open(os.path.join(ROOT, "help.html"), encoding="utf-8").read()  # 도구 첫 화면에 끼워 넣는 ? 버튼 + 사용법
HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length"}


# ── DB (sqlite, 파일 하나) ───────────────────────────────────────────────
def db():
    c = sqlite3.connect(os.path.join(DATA, "portal.db"), timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS events(kind TEXT, dir TEXT, ts TEXT, user TEXT);          -- open | like
    CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS post_likes(post INTEGER, user TEXT, UNIQUE(post, user));""")
    return c


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
    return next((t for t in TOOLS if t["dir"] == d), None)


def start(t):
    cwd = os.path.join(BASE, t["dir"])
    env = {**os.environ, "PORT": str(t["port"]), "WORKSPACE": os.path.join(DATA, t["dir"])}
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        cmd = ["bash", "setup.sh"]
    else:  # ponytail: setup.sh 없는 도구는 app.py 직접 (saju-local)
        py = os.path.join(cwd, "venv", "bin", "python")
        cmd = [py if os.path.exists(py) else sys.executable, "app.py"]
    subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(cwd, "server.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)


def stop(t):
    cwd = os.path.join(BASE, t["dir"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        subprocess.run(["bash", "setup.sh", "stop"], cwd=cwd, capture_output=True)
    subprocess.run(["bash", "-c", f"lsof -ti tcp:{t['port']} | xargs kill 2>/dev/null"], capture_output=True)


def add_tool(t):
    """내 에이전트 등록: tools.json 에 한 줄. 폴더가 생기면 카드가 '미설치'→'중지됨'으로 바뀐다."""
    d = t.get("dir", "")
    if not d.replace("-", "").replace("_", "").isalnum():
        raise ValueError("폴더 이름은 영문·숫자·-_ 만")
    if tool(d):
        raise ValueError("이미 있는 도구")
    port = int(t.get("port") or 0)
    if not (1024 < port < 65536) or any(x["port"] == port for x in TOOLS):
        raise ValueError("포트가 비어 있지 않거나 범위 밖")
    row = {"dir": d, "port": port, "name": t.get("name") or d, "title": t.get("title") or d, "desc": t.get("desc", ""), "group": t.get("group") or "기타",
           "tags": [x for x in (t.get("tags") or "").replace("#", "").split() if x], "dept": t.get("dept", ""), "author": t.get("author", ""),
           "added": datetime.date.today().isoformat()}
    row["tags"] = ["#" + x for x in row["tags"]]
    TOOLS.append(row)
    json.dump(TOOLS, open(TOOLS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return row


def guide(d):
    p = os.path.join(ROOT, "guides", f"{d}.md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else f"## {d}\n\n사용법이 아직 없습니다. `portal/guides/{d}.md` 를 만들면 여기에 뜹니다."


# ── HTTP ────────────────────────────────────────────────────────────────
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    @property
    def user(self):  # ponytail: 로그인 없음 → 리버스프록시 헤더(X-Forwarded-User/X-User) 있으면 그것, 아니면 IP
        return self.headers.get("X-Forwarded-User") or self.headers.get("X-User") or self.client_address[0]

    def _send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def proxy(self):
        """/t/<dir>/<rest> → http://127.0.0.1:<port>/<rest>. 첫 화면 HTML 에는 사용법 팝업을 붙이고, 나머지(SSE 포함)는 그대로 흘린다."""
        parts = self.path.split("/", 3)
        t = tool(parts[2]) if len(parts) > 2 else None
        if not t:
            return self._send({"error": "unknown tool"}, code=404)
        if len(parts) < 4:
            self.send_response(302); self.send_header("Location", f"/t/{t['dir']}/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = int(self.headers.get("Content-Length") or 0)
        req = urllib.request.Request(f"http://127.0.0.1:{t['port']}/{parts[3]}", data=self.rfile.read(n) if n else None,
                                     headers={k: v for k, v in self.headers.items() if k.lower() not in HOP}, method=self.command)
        try:
            r = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            return self._send({"error": f"{t['dir']} 응답 없음 ({type(e).__name__}) — 포털에서 '시작'을 누르세요"}, code=502)
        inject = self.command == "GET" and parts[3] in ("", "index.html") and "text/html" in (r.headers.get("Content-Type") or "")
        body = r.read() + HELP.replace("%DIR%", t["dir"]).encode() if inject else None
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
        if p == "/api/tools":
            with db() as c:
                u = usage(c)
                return self._send({"tools": [{**t, "up": alive(t["port"]), "installed": os.path.isdir(os.path.join(BASE, t["dir"])),
                                              "url": f"/t/{t['dir']}/", "url_direct": f"http://{HOST}:{t['port']}",
                                              "usage": u.get(t["dir"], {"recent": 0, "total": 0, "users": 0, "likes": 0})} for t in TOOLS],
                                   "rank": user_rank(c), "posts": posts(c, self.user), "me": self.user})
        if p.startswith("/api/guide/"):
            d = p.split("/")[-1]
            return self._send(guide(d).encode() if (tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
        if p.startswith("/api/readme/"):
            d = p.split("/")[-1]
            f = os.path.join(BASE, d, "README.md") if tool(d) else ""
            return self._send((open(f, encoding="utf-8").read() if f and os.path.exists(f) else "README 없음").encode(), "text/plain; charset=utf-8")
        self._send((HTML + HELP.replace("%DIR%", "portal")).encode(), "text/html; charset=utf-8")

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
        for t in TOOLS:
            if os.path.isdir(os.path.join(BASE, t["dir"])):
                print(sys.argv[1], t["dir"], t["port"]); (start if sys.argv[1] == "start-all" else stop)(t)
        sys.exit(0)
    # 옛 json 통계·피드 1회 이관
    with db() as c:
        for f, sql in ((os.path.join(ROOT, "_stats.json"), None), (os.path.join(ROOT, "feed.json"), None)):
            if os.path.exists(f):
                rows = json.load(open(f, encoding="utf-8"))
                if "stats" in f:
                    c.executemany("INSERT INTO events VALUES(?,?,?,?)", [("like" if r.get("like") else "open", r["dir"], r["ts"], r["ip"]) for r in rows])
                elif not c.execute("SELECT 1 FROM posts").fetchone():
                    c.executemany("INSERT INTO posts(title,body,author,dept,tags,ts) VALUES(?,?,?,?,?,?)",
                                  [(r["title"], r["body"], r["author"], r["dept"], json.dumps(r.get("tags", [])), r["date"]) for r in rows])
                os.rename(f, f + ".imported")
    print(f"portal → http://localhost:{PORT}  data={DATA}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
