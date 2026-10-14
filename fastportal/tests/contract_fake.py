#!/usr/bin/env python3
"""계약 시험(가짜 도구): 임시 폴더에 portal.py 사본과 fastportal 을 같은 임시 DB·가짜 tools.json 으로 띄우고, 이 시험이 띄운
가짜 도구(받은 헤더·본문을 그대로 돌려줌, SSE)를 거쳐 프록시 규칙·스트리밍·끊김·잠금·동시 스트림을 확인한다. 실제 데이터·도구·LLM 은 안 씀.
계정 저장소(AUTH_BACKEND)는 가짜 외부 저장소(임시 모듈 fake_ext_auth:FakeExternal)·설정 없는 external 포털을 따로 띄워 확인.
  python3 fastportal/tests/contract_fake.py [--streams 50] [--slow]      # --slow: 1분 넘는 POST → /api/events 기록까지(1분 더 걸림)
환경: FASTPORTAL_VENV(기본 ~/.local/fastportal-venv)"""
import argparse, hashlib, http.client, json, os, select, shutil, signal, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ap = argparse.ArgumentParser()
ap.add_argument("--streams", type=int, default=50)
ap.add_argument("--slow", action="store_true")
A = ap.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
VENV = os.environ.get("FASTPORTAL_VENV") or os.path.expanduser("~/.local/fastportal-venv")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


# ── 가짜 도구 ────────────────────────────────────────────────────────────
STATE = {"open": 0, "max_open": 0, "closed_by_peer": 0, "lock": threading.Lock()}


class Tool(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, o, code=200, extra=()):
        b = json.dumps(o, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def _echo(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        skip = {"host", "user-agent", "accept", "connection", "accept-encoding"}   # 전송 계층(urllib·httpx 가 다르게 채움)
        self._json({"method": self.command, "path": self.path, "headers": sorted((k.lower(), v) for k, v in self.headers.items() if k.lower() not in skip),
                    "te": self.headers.get("Transfer-Encoding"), "len": len(body), "sha": hashlib.sha256(body).hexdigest()[:16]})

    def do_POST(self):
        if self.path.startswith("/quick-slow"):   # 작업 알림 자동 기록 시험(PORTAL_JOBS_AUTO_S=1)
            n = int(self.headers.get("Content-Length") or 0); n and self.rfile.read(n)
            time.sleep(2.5); return self._json({"ok": True}, code=500 if "fail" in self.path else 200)
        if self.path.startswith("/slow"):
            n = int(self.headers.get("Content-Length") or 0); n and self.rfile.read(n)
            time.sleep(61); return self._json({"ok": True})
        self._echo()

    do_PUT = do_DELETE = do_PATCH = lambda self: self._echo()   # 모든 메서드 프록시 시험

    def do_HEAD(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", "0"); self.end_headers()

    def do_GET(self):
        p = self.path
        if p.startswith("/echo"):
            return self._echo()
        if p in ("/", "/index.html") or p.startswith("/?"):
            b = b'<!doctype html><html><head><meta charset="utf-8"><title>fake</title></head><body><h1>fake tool</h1></body></html>'
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if p == "/redir":
            self.send_response(302); self.send_header("Location", "/echo?from=redir"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if p == "/redir-ext":
            self.send_response(302); self.send_header("Location", f"http://127.0.0.1:{DOWN_PORT}/x"); self.send_header("Content-Length", "0"); self.end_headers(); return
        if p == "/cookies":
            return self._json({"ok": 1}, extra=[("Set-Cookie", "a=1; Path=/"), ("Set-Cookie", "b=2; Path=/"), ("X-Tool", "yes")])
        if p == "/err":
            return self._json({"error": "bad"}, code=418)
        if p == "/agcookie":   # 포털 쿠키를 덮으려는 도구(보안 시험)
            return self._json({"ok": 1}, extra=[("Set-Cookie", "agsid=evil; Path=/"), ("Set-Cookie", "AGTHEME=x; Path=/"), ("Set-Cookie", "c=3; Path=/")])
        if p.startswith("/api/doc"):   # API 가 주는 HTML(사용자 내용) — sandbox 대상
            b = b"<script>fetch('/api/me')</script>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if p.startswith("/sse"):   # n 개 이벤트를 iv 초 간격으로, 그 뒤 idle 초 동안 아무것도 안 보냄(포털이 끊으면 바로 알아챔)
            q = dict(x.split("=") for x in (p.split("?") + [""])[1].split("&") if "=" in x)
            n, iv, idle = int(q.get("n", 5)), float(q.get("iv", 0.2)), float(q.get("idle", 0))
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache"); self.end_headers()
            with STATE["lock"]:
                STATE["open"] += 1; STATE["max_open"] = max(STATE["max_open"], STATE["open"])
            try:
                for i in range(n):
                    self.wfile.write(f"data: {json.dumps({'i': i, 't': time.time()})}\n\n".encode()); self.wfile.flush()
                    if i < n - 1:
                        time.sleep(iv)
                end = time.time() + idle
                while time.time() < end:   # 조용히 기다리면서 상대(포털)가 연결을 닫는지 봄
                    r, _, _ = select.select([self.connection], [], [], 0.2)
                    if r and not self.connection.recv(1, socket.MSG_PEEK):
                        with STATE["lock"]:
                            STATE["closed_by_peer"] += 1
                        return
            except (BrokenPipeError, ConnectionResetError):
                with STATE["lock"]:
                    STATE["closed_by_peer"] += 1
                return
            finally:
                with STATE["lock"]:
                    STATE["open"] -= 1
                self.close_connection = True
            return
        self._json({"error": "nf"}, code=404)


TOOL_PORT, DOWN_PORT = free_port(), free_port()
tool_srv = ThreadingHTTPServer(("127.0.0.1", TOOL_PORT), Tool); tool_srv.daemon_threads = True
threading.Thread(target=tool_srv.serve_forever, daemon=True).start()
# 혼합형: '다른 컨테이너'의 도구 대신 127.0.0.2 에만 연 가짜 도구 — 포털이 host 를 안 쓰고 127.0.0.1 로 가면 연결 거부
REMOTE_PORT, BIND_PORT = free_port(), free_port()
remote_srv = ThreadingHTTPServer(("127.0.0.2", REMOTE_PORT), Tool); remote_srv.daemon_threads = True
threading.Thread(target=remote_srv.serve_forever, daemon=True).start()
BAD_HOSTS = ["127.0.0.1/evil", "http://127.0.0.1", "x@127.0.0.1", "127.0.0.1:80", "a b", "[::1]", "-bad.example"]

# ── 임시 포털 둘(같은 임시 DB) ───────────────────────────────────────────
T = tempfile.mkdtemp(prefix="fastportal-test-")
ROOT = os.path.join(T, "agent-page-portal")
os.makedirs(os.path.join(ROOT, "docs", "img"))
for f in ("portal.py", "portal.html", "help.html", "login.html", "account.html"):
    shutil.copy(os.path.join(REPO, f), ROOT)
for d in ("theme", "v2", "guides"):
    shutil.copytree(os.path.join(REPO, d), os.path.join(ROOT, d))
os.makedirs(os.path.join(ROOT, "guide"))
shutil.copy(os.path.join(REPO, "docs", "img", "landing-home.png"), os.path.join(ROOT, "docs", "img"))
json.dump([{"dir": "fake-local", "port": TOOL_PORT, "name": "가짜", "title": "가짜 도구", "group": "시험"},
           {"dir": "down-local", "port": DOWN_PORT, "name": "꺼짐", "title": "꺼진 도구", "group": "시험"},
           {"dir": "remote-local", "port": REMOTE_PORT, "host": "127.0.0.2", "name": "원격", "title": "다른 컨테이너 도구", "group": "시험"},
           {"dir": "bind-local", "port": BIND_PORT, "name": "바인드", "title": "BIND 확인", "group": "시험"},
           {"dir": "hidden-local", "port": TOOL_PORT, "name": "숨김", "title": "숨긴 도구", "group": "시험", "hidden": True}]
          + [{"dir": f"badhost{i}-local", "port": TOOL_PORT, "host": h, "name": "틀린 host", "group": "시험"} for i, h in enumerate(BAD_HOSTS)],
          open(os.path.join(ROOT, "tools.json"), "w"), ensure_ascii=False)
# bind-local: 포털이 띄우면 받은 BIND 를 파일에 적고 끝남(core.start 의 TOOL_BIND 확인용)
os.makedirs(os.path.join(T, "bind-local"))
open(os.path.join(T, "bind-local", "app.py"), "w").write("import os, json\nh = os.path.dirname(os.path.abspath(__file__))\n"
    "json.dump({k: os.environ.get(k) for k in ('PORTAL_JOBS_URL', 'PORTAL_JOBS_TOKEN', 'PORTAL_JOBS_TOOL', 'PORTAL_JOBS_SECRET', 'PORTAL_TOOL_KEY')}, open(os.path.join(h, 'jobs-env.json'), 'w'))\n"
    "open(os.path.join(h, 'bind.txt'), 'w').write(os.environ.get('BIND', ''))\n")
BIND_TXT = os.path.join(T, "bind-local", "bind.txt")
# React 빌드 대신 작은 가짜 dist(WEB_DIST 시험)
WEB = os.path.join(T, "webdist")
os.makedirs(os.path.join(WEB, "assets"))
open(os.path.join(WEB, "index.html"), "w").write('<!doctype html><html><head><script type="module" src="/app/assets/index-AbC12345.js"></script></head><body><div id="root"></div></body></html>')
open(os.path.join(WEB, "assets", "index-AbC12345.js"), "w").write("console.log(1)")
open(os.path.join(WEB, "theme-boot.js"), "w").write("/*boot*/")
DATA = os.path.join(T, "_data")
OLD, NEW, NEW2 = free_port(), free_port(), free_port()
env = {**os.environ, "AGENT_DATA": DATA, "HOST": "127.0.0.1", "BACKUP": "0", "PORTAL_ROOT": ROOT, "PORTAL_MAX_BODY": str(8 * 1024 * 1024), "AGENT_OWNERS": "boss"}
env.pop("TLS_CERT", None); env.pop("TLS_KEY", None)
procs = {}


def launch(name, port, extra=None):
    if name == "old":
        cmd, cwd = [sys.executable, os.path.join(ROOT, "portal.py")], ROOT
    else:
        cmd, cwd = [os.path.join(VENV, "bin", "python"), "-m", "fastportal"], REPO
    procs[name] = subprocess.Popen(cmd, cwd=cwd, env={**env, "PORT": str(port), **(extra or {})}, stdout=open(os.path.join(T, f"{name}.log"), "w"), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close(); return
        except OSError:
            time.sleep(0.1)
    raise SystemExit(f"{name} 안 뜸: " + open(os.path.join(T, f"{name}.log")).read())


launch("old", OLD); launch("new", NEW)
TOKF = os.path.join(DATA, ".local_owner_token")   # 새 포털(PORTAL_LOCAL_OWNER=token, 기본)이 처음 기동 때 만듦
TOKV = open(TOKF).read().strip()
TOK_PORTS = {NEW}   # 이 포트로 가는 요청엔 X-Local-Owner 를 붙임(예전 '127.0.0.1 쿠키 없음 = 주인' 시험을 그대로 쓰려고). tok=False 로 뺌


def req(port, method, path, headers=None, body=None, chunked=False, tok=True):
    if tok and port in TOK_PORTS and not any(k.lower() == "x-local-owner" for k in (headers or {})):
        headers = {**(headers or {}), "X-Local-Owner": TOKV}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    if chunked:
        c.putrequest(method, path)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        c.putheader("Transfer-Encoding", "chunked"); c.endheaders()
        for i in range(0, len(body), 65536):
            part = body[i:i + 65536]; c.send(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        c.send(b"0\r\n\r\n")
    else:
        c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse(); b = r.read(); c.close()
    return r.status, [(k.lower(), v) for k, v in r.getheaders()], b


RESULTS = []


def check(name, ok, detail="", kind="일치"):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail, "kind": kind})
    print(("  OK  " if ok else "  !!  ") + f"{name}" + (f"  — {detail}" if detail else ""))


def hdr(hd, k):
    return [v for kk, v in hd if kk == k]


NEW_REQ = {"x-agent-auth", "cookie"}   # 새 포털이 일부러 바꾼 도구 쪽 헤더: 서명(X-Agent-Auth)을 붙이고 쿠키는 안 넘김 — '보안' 절에서 따로 확인
NEW_RESP = {"x-content-type-options", "referrer-policy", "x-frame-options", "content-security-policy"}   # 새 포털이 붙이는 보안 응답 헤더


def norm(r):
    """도구가 받은 요청(_echo) — 일부러 바꾼 헤더를 빼고 비교용으로"""
    return {**r, "headers": [h for h in r["headers"] if h[0] not in NEW_REQ]} if isinstance(r, dict) and "headers" in r else r


# 계정(임시 DB): 가입 → 쿠키
st, hd, b = req(OLD, "POST", "/api/signup", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "name": "시험", "pin": "1234"}).encode())
TOK = hdr(hd, "set-cookie")[0].split(";")[0].split("=", 1)[1]
CK = {"Cookie": f"agsid={TOK}; agtheme=v2-dark; toolck=keep"}

print("\n## 헤더 규칙 (도구가 받은 헤더 비교)")
spoof = {**CK, "X-Agent-User": "boss", "X-User": "boss", "X-Forwarded-User": "boss", "Accept-Encoding": "gzip", "X-Custom": "1", "Te": "trailers"}
ra = json.loads(req(OLD, "GET", "/t/fake-local/echo?q=%2F1", spoof)[2]); rb = json.loads(req(NEW, "GET", "/t/fake-local/echo?q=%2F1", spoof)[2])
check("사용자 헤더 버림·포털 값(X-Agent-User=tester)", dict(rb["headers"]).get("x-agent-user") == "tester" and "x-user" not in dict(rb["headers"]), str(rb["headers"]))
check("쿠키는 도구로 하나도 안 감(예전 포털은 agsid·agtheme 만 빼고 넘김)", "cookie" not in dict(rb["headers"]), str(rb["headers"]), kind="의도한 차이")
check("도구가 받은 헤더·경로 같음(old=new, 서명·쿠키 빼고)", norm(ra) == norm(rb), "" if norm(ra) == norm(rb) else f"{ra} ≠ {rb}")
ra = json.loads(req(OLD, "GET", "/t/fake-local/echo")[2]); rb = json.loads(req(NEW, "GET", "/t/fake-local/echo")[2])
check("서버 안 쿠키 없음(+토큰) → X-Agent-User=owner", dict(rb["headers"]).get("x-agent-user") == "owner" and norm(ra) == norm(rb))
check("X-Local-Owner 토큰은 도구로 안 감", "x-local-owner" not in dict(rb["headers"]), str(rb["headers"]))

print("\n## 요청 본문")
body = os.urandom(3 * 1024 * 1024)
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body)[2])
rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body)[2])
check("Content-Length 본문 3MB 그대로(old=new)", norm(ra) == norm(rb) and rb["len"] == len(body), f"{rb['len']} {rb['sha']}")
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", CK, b"a=1")[2]); rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", CK, b"a=1")[2])
check("Content-Type 없을 때 기본값 같음", norm(ra) == norm(rb), f"{dict(ra['headers']).get('content-type')} / {dict(rb['headers']).get('content-type')}")
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", CK, b"")[2]); rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", CK, b"")[2])
check("빈 POST 같음", norm(ra) == norm(rb), "" if norm(ra) == norm(rb) else f"{ra} ≠ {rb}")
sa, _, ba = req(OLD, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body[:300000], chunked=True)
sb, _, bb = req(NEW, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body[:300000], chunked=True)
check("청크 본문: 새 포털은 길이를 붙여 전달(예전 포털은 본문을 버림)", json.loads(bb)["len"] == 300000, f"old len={json.loads(ba)['len']} new len={json.loads(bb)['len']}", kind="의도한 차이")
big = b"x" * (9 * 1024 * 1024)
sb, _, bb = req(NEW, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, big)
sa, _, ba = req(OLD, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, big)
check("업로드 상한 초과 → 413(본문 읽어 버린 뒤, 상한 8MB 로 시험)", sb == 413 and json.loads(bb).get("error"), f"new {sb} {bb[:80]!r} / old {sa}(상한 없음)", kind="의도한 차이")
sb, _, bb = req(NEW, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, big, chunked=True)
check("청크 업로드 상한 초과 → 413", sb == 413, f"{sb}", kind="의도한 차이")
sb, _, bb = req(NEW, "POST", "/api/account/name", {**CK, "Content-Type": "application/json"}, b"{" + b" " * (2 * 1024 * 1024) + b"}")
check("포털 API JSON 상한(1MB) 초과 → 413", sb == 413, f"{sb}", kind="의도한 차이")

print("\n## 응답")
for path in ("/t/fake-local/", "/t/fake-local/?pet=1", "/t/fake-local/cookies", "/t/fake-local/err", "/t/fake-local/redir", "/t/fake-local/nope", "/t/down-local/", "/t/down-local/api/x",
             "/t/fake-local", "/t/none-local/", "/app/", "/app", "/web/landing.json"):   # /app·/web: WEB_DIST 없으면 예전과 같음
    sa, ha, ba = req(OLD, "GET", path, CK); sb, hb, bb = req(NEW, "GET", path, CK)
    keep = lambda h: [(k, v) for k, v in h if k not in ("date", "server", "connection", "transfer-encoding", "keep-alive") and k not in NEW_RESP]
    ba_, bb_ = ba, bb
    if path.endswith("redir"):   # 도구가 받은 헤더 비교(전송 헤더는 _echo 가 뺌)
        ba_, bb_ = norm(json.loads(ba)), norm(json.loads(bb))
        ha, hb = [x for x in ha if x[0] != "content-length"], [x for x in hb if x[0] != "content-length"]   # 본문(받은 헤더)이 서명·쿠키만큼 다름
    check(f"GET {path} 같음 ({sa}/{sb})", sa == sb and ba_ == bb_ and keep(ha) == keep(hb), "" if (sa == sb and ba_ == bb_ and keep(ha) == keep(hb)) else f"{keep(ha)} {ba[:200]!r}\n      ≠ {keep(hb)} {bb[:200]!r}")

req(NEW, "GET", "/t/fake-local/cookies", CK)
rb = json.loads(req(NEW, "GET", "/t/fake-local/echo")[2])
check("도구가 준 Set-Cookie 를 포털이 기억해 다른 요청에 붙이지 않음", "cookie" not in dict(rb["headers"]), str(rb["headers"]))
sa, ha, _ = req(OLD, "GET", "/t/fake-local/redir-ext", CK); sb, hb, _ = req(NEW, "GET", "/t/fake-local/redir-ext", CK)
check("다른 주소로의 리디렉트는 따라가지 않고 그대로 전달", sb == 302, f"old {sa}(따라가서 실패) / new {sb} {hdr(hb, 'location')}", kind="의도한 차이")

print("\n## SSE 스트리밍(버퍼링 없음)")
for name, port in (("old", OLD), ("new", NEW)):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30); t0 = time.time()
    c.request("GET", "/t/fake-local/sse?n=5&iv=0.5", headers=CK); r = c.getresponse()
    first = r.readline(); t_first = time.time() - t0
    rest = r.read(); total = time.time() - t0; c.close()
    n_ev = (first + rest).count(b"data:")
    check(f"{name}: 첫 이벤트 {t_first:.2f}s · 전체 {total:.2f}s · 이벤트 {n_ev}개", t_first < 0.4 and n_ev == 5 and total > 1.9, r.getheader("Content-Type"))

print(f"\n## 동시 스트림 {A.streams}개 — 스레드·메모리, 브라우저가 끊으면 도구 쪽도 닫히는지")


def threads_rss(pid):
    s = open(f"/proc/{pid}/status").read()
    g = lambda k: int(next(l.split()[1] for l in s.splitlines() if l.startswith(k)))
    return g("Threads:"), g("VmRSS:") // 1024


def open_streams(port, n, path):
    socks = []
    for _ in range(n):
        s = socket.create_connection(("127.0.0.1", port), 10)
        s.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\nCookie: agsid={TOK}\r\n\r\n".encode()); socks.append(s)
    got = 0
    for s in socks:   # 첫 이벤트가 오는지
        s.settimeout(10); buf = b""
        while b"data:" not in buf:
            buf += s.recv(4096)
        got += 1
    return socks, got


for name, port in (("old", OLD), ("new", NEW)):
    pid = procs[name].pid
    t0, m0 = threads_rss(pid)
    with STATE["lock"]:
        STATE.update(closed_by_peer=0, max_open=0)
    socks, got = open_streams(port, A.streams, "/t/fake-local/sse?n=1&idle=30")   # 이벤트 하나 뒤 30초 조용 — 가장 나쁜 경우(쓸 것이 없어 끊김을 못 알아챔)
    time.sleep(1)
    t1, m1 = threads_rss(pid)
    for s in socks:
        s.close()
    deadline = time.time() + 10
    while time.time() < deadline and STATE["open"] > 0:
        time.sleep(0.2)
    t2, m2 = threads_rss(pid)
    detail = f"스레드 {t0}→{t1}→{t2}, RSS {m0}→{m1}→{m2}MB, 받은 첫 이벤트 {got}/{A.streams}, 도구 동시 연결 최대 {STATE['max_open']}, 끊은 뒤 도구 쪽 남은 연결 {STATE['open']}"
    if name == "new":
        # 스레드는 스트림 수와 무관: 늘어난 몇 개는 DB 확인용 스레드 풀(anyio 기본 40개 상한, 재사용)
        check(f"new: 스트림 {A.streams}개에도 스레드가 스트림 수만큼 늘지 않음·끊으면 도구 연결도 바로 닫힘", got == A.streams and t1 - t0 <= 41 and STATE["open"] == 0, detail, kind="동작 확인")
    else:
        check(f"old(참고): 스트림마다 스레드 1개", got == A.streams, detail + " — 조용한 스트림은 도구가 다음 글자를 보낼 때까지 남음", kind="참고")
    deadline = time.time() + 35   # 예전 포털 쪽 남은 연결이 다음 시험을 흐리지 않게
    while time.time() < deadline and STATE["open"] > 0:
        time.sleep(0.5)

print("\n## 로그인 잠금(10분에 5번) — 새 포털은 DB 에 기록: 다른 프로세스·재시작에도")
bad = json.dumps({"id": "tester", "pin": "9999"}).encode()
for i in range(5):
    req(NEW, "POST", "/api/login", {"Content-Type": "application/json"}, bad)
s6, _, b6 = req(NEW, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "pin": "1234"}).encode())
check("5번 틀린 뒤 맞는 PIN 도 잠김 → 429 locked(예전 400)", s6 == 429 and json.loads(b6).get("code") == "locked" and "10분" in json.loads(b6)["error"], json.loads(b6)["error"])
launch("new2", NEW2)
s7, _, b7 = req(NEW2, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "pin": "1234"}).encode())
check("다른 fastportal 프로세스에서도 잠김(같은 DB)", s7 == 429 and "10분" in json.loads(b7)["error"], kind="의도한 차이")
s8, _, _ = req(OLD, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "pin": "1234"}).encode())
check("예전 포털(메모리)은 모름 → 로그인 됨(참고)", s8 == 200, kind="참고")
sa, ha, ba = req(OLD, "POST", "/api/login", {"Content-Type": "application/json"}, bad)
sb, hb, bb = req(NEW, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "nobody", "pin": "9999"}).encode())
check("틀린 PIN 문구 같음(새 포털은 code 를 더 줌)", sa == sb == 400 and json.loads(ba)["error"] == json.loads(bb)["error"] and json.loads(bb).get("code") == "bad_credentials", f"{ba.decode()} / {bb.decode()}")

print("\n## 로컬 주인 판정·프록시 뒤(보안) — 새 포털은 portal.py 와 일부러 다름")


def raw_req(port, path, headers):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.putrequest("GET", path)
    for k, v in headers:
        c.putheader(k, v)
    c.endheaders(); r = c.getresponse(); b = r.read(); c.close()
    return r.status, b


def who(port, headers):
    st, b = raw_req(port, "/api/me", headers)
    return st, (json.loads(b).get("id") if st == 200 else None)


cases = [("쿠키 없음(127.0.0.1)", [], "owner", "owner"),
         ("빈 agsid=", [("Cookie", "agsid=")], "owner", None),
         ("agsid=; 형식 틀림(특수문자)", [("Cookie", "agsid=%%%")], "owner", None),
         ("Cookie 줄 둘(첫 줄엔 agsid 없음, 둘째 줄 빈 agsid)", [("Cookie", "a=1"), ("cookie", "agsid=")], "owner", None),
         ("Cookie 줄 둘(둘째 줄에 진짜 세션)", [("Cookie", "a=1"), ("cookie", f"agsid={TOK}")], "owner", "tester"),
         ("무효 agsid", [("Cookie", "agsid=" + "x" * 43)], None, None),
         ("X-Forwarded-For 위조(믿을 프록시 없음 → 무시)", [("X-Forwarded-For", "203.0.113.5")], "owner", "owner")]
for name, hd_, exp_old, exp_new in cases:
    (sa, ia), (sb, ib) = who(OLD, hd_), who(NEW, hd_ + [("X-Local-Owner", TOKV)])
    same_ = (ia, sa) == (ib, sb)
    check(f"{name}: old={ia or sa} new={ib or sb}", ia == exp_old and ib == exp_new, kind="일치" if same_ else "의도한 차이(보안)")
print("\n## 로컬 주인 토큰(PORTAL_LOCAL_OWNER=token, 기본) — SSH 포트포워딩 브라우저가 로그아웃 뒤 주인이 되던 문제")
check("토큰 파일 권한 600", (os.stat(TOKF).st_mode & 0o777) == 0o600, oct(os.stat(TOKF).st_mode & 0o777), kind="설정")
st, i = who(NEW, [])
check(f"헤더 없는 127.0.0.1 쿠키 없음 → {st}(로그인 필요)", st == 401, kind="의도한 차이(보안)")
st, i = who(NEW, [("X-Local-Owner", "x" * 43)])
check(f"틀린 토큰 → {st}", st == 401, kind="의도한 차이(보안)")
st, i = who(NEW, [("X-Local-Owner", TOKV)])
check(f"맞는 토큰 → owner", st == 200 and i == "owner", kind="설정")
st, i = who(NEW, [("X-Local-Owner", TOKV), ("Cookie", "agsid=")])
check(f"맞는 토큰 + 빈 agsid(로그아웃한 브라우저 꼴) → {st}", st == 401, kind="설정")
sb = req(NEW, "POST", "/api/signup", {"Content-Type": "application/json"}, json.dumps({"id": "boss", "name": "x", "pin": "1234"}).encode(), tok=False)[0]
check(f"헤더 없는 로컬: 관리자 아이디(boss) 가입 거절 {sb}", sb == 400, kind="설정")
sb, _, b = req(NEW, "GET", "/", {"Accept": "text/html"}, tok=False)
check(f"헤더 없는 로컬: 홈 → {sb}(로그인 화면으로)", sb == 302, kind="의도한 차이(보안)")
NOOWN, PROX, OLDMODE = free_port(), free_port(), free_port()
launch("oldmode", OLDMODE, {"PORTAL_LOCAL_OWNER": "1"})
st, i = who(OLDMODE, [])
check(f"PORTAL_LOCAL_OWNER=1(예전 방식): 127.0.0.1 쿠키 없음 → {i or st}", st == 200 and i == "owner", kind="설정")
launch("noown", NOOWN, {"PORTAL_LOCAL_OWNER": "0"})
launch("prox", PROX, {"TRUSTED_PROXIES": "127.0.0.1"})
st, i = who(NOOWN, [])
check(f"PORTAL_LOCAL_OWNER=0: 127.0.0.1 쿠키 없음 → {st}(로그인 필요)", st == 401, kind="설정")
st, i = who(NOOWN, [("Cookie", f"agsid={TOK}")])
check("PORTAL_LOCAL_OWNER=0: 계정 로그인은 그대로", st == 200 and i == "tester", kind="설정")
sb, bb = req(NOOWN, "POST", "/api/signup", {"Content-Type": "application/json"}, json.dumps({"id": "boss", "name": "x", "pin": "1234"}).encode())[0::2]
check(f"PORTAL_LOCAL_OWNER=0: 관리자 아이디(boss) 가입 거절 {sb}", sb == 400, kind="설정")
for name, hd_, exp in (("믿을 프록시 + XFF 바깥 주소", [("X-Forwarded-For", "203.0.113.5")], 401),
                       ("믿을 프록시 + XFF 없음(알 수 없음)", [], 401),
                       ("믿을 프록시 + XFF 위조 '127.0.0.1, 바깥'", [("X-Forwarded-For", "127.0.0.1, 203.0.113.5")], 401),
                       ("믿을 프록시 + XFF 에 프록시 주소뿐(구분 불가 → 로컬 아님)", [("X-Forwarded-For", "127.0.0.1")], 401)):
    st, i = who(PROX, hd_ + [("X-Local-Owner", TOKV)])   # 토큰이 맞아도 바깥 손님이면 주인 아님
    check(f"TRUSTED_PROXIES=127.0.0.1: {name} → {st}", st == exp, kind="설정")

print("\n## 혼합형: tools.json host(다른 컨테이너의 도구)")
rb = json.loads(req(NEW, "GET", "/t/remote-local/echo?q=1", spoof)[2])
h = dict(rb["headers"])
check("host 도구로 프록시(127.0.0.2:포트) + 헤더 규칙 그대로", rb["path"] == "/echo?q=1" and h.get("x-agent-user") == "tester" and "x-user" not in h and "x-forwarded-user" not in h
      and "cookie" not in h and "x-agent-auth" in h and "x-local-owner" not in h, str(rb["headers"]), kind="설정")
sb, hb, bb = req(NEW, "GET", "/t/remote-local/", CK)
check("host 도구 첫 화면에도 사용법·테마 붙임", sb == 200 and b"fake tool" in bb and b"data-sig" in bb, f"{sb}", kind="설정")
tl = {t["dir"]: t for t in json.loads(req(NEW, "GET", "/api/tools", CK)[2])["tools"]}
check("/api/tools: host 도구 up·installed", tl["remote-local"]["up"] is True and tl["remote-local"]["installed"] is True and tl["fake-local"]["up"] is True and tl["down-local"]["up"] is False,
      str({k: (v["up"], v["installed"]) for k, v in tl.items()}), kind="설정")
check("/api/tools: 형식 틀린 host 는 꺼짐(같은 포트의 로컬 도구가 켜져 있어도)", all(tl[f"badhost{i}-local"]["up"] is False for i in range(len(BAD_HOSTS))), kind="보안")
for i, hv in enumerate(BAD_HOSTS):
    sb, _, bb = req(NEW, "GET", f"/t/badhost{i}-local/echo", CK)
    check(f"형식 틀린 host {hv!r} → 502, 프록시 안 함", sb == 502 and b"fake tool" not in bb and b"headers" not in bb, f"{sb} {bb.decode('utf-8', 'replace')[:60]}", kind="보안")
for ep in ("/api/start", "/api/stop"):
    for who_, hd_ in (("주인", {}), ("일반", CK)):
        sb, _, bb = req(NEW, "POST", ep, {**hd_, "Content-Type": "application/json"}, json.dumps({"dir": "remote-local"}).encode())
        check(f"host 도구 {ep}({who_}) → 409", sb == 409 and json.loads(bb).get("error") == "다른 컨테이너에서 항상 켜져 있는 도구입니다", f"{sb} {bb.decode()}", kind="설정")
rb = json.loads(req(NEW, "GET", "/t/remote-local/echo")[2])
check("host 도구는 stop 뒤에도 그대로 켜짐(포털이 안 건드림)", rb.get("path") == "/echo", kind="설정")


def bind_of(port):
    if os.path.exists(BIND_TXT):
        os.remove(BIND_TXT)
    sb = req(port, "POST", "/api/start", {"Content-Type": "application/json", "X-Local-Owner": TOKV}, json.dumps({"dir": "bind-local"}).encode())[0]
    for _ in range(100):
        if os.path.exists(BIND_TXT) and open(BIND_TXT).read():
            return sb, open(BIND_TXT).read()
        time.sleep(0.1)
    return sb, None


sb, b_ = bind_of(NEW)
check(f"TOOL_BIND 없음 → 도구 BIND={b_}", sb == 200 and b_ == "127.0.0.1", kind="설정")
BINDP = free_port()
launch("bind", BINDP, {"TOOL_BIND": "0.0.0.0"})
sb, b_ = bind_of(BINDP)
check(f"TOOL_BIND=0.0.0.0 → 도구 BIND={b_}", sb == 200 and b_ == "0.0.0.0", kind="설정")

print("\n## React 빌드 같은 출처(WEB_DIST·WEB_PREFIX)")
WEBP = free_port()
launch("web", WEBP, {"WEB_DIST": WEB})
st, hd, b = req(WEBP, "GET", "/app/", {"Accept": "text/html"}, tok=False)
check(f"로그인 없이 /app/ → {st} {hdr(hd, 'location')}(로그인으로)", st == 302 and hdr(hd, "location") == ["/app/login?next=%2F"] and not hdr(hd, "set-cookie"), kind="설정")
st, hd, b = req(WEBP, "GET", "/app/tool/sql-local?x=1", {"Accept": "text/html"}, tok=False)
check(f"로그인 없이 /app/tool/sql-local?x=1 → {st} {hdr(hd, 'location')}", st == 302 and hdr(hd, "location") == ["/app/login?next=%2Ftool%2Fsql-local%3Fx%3D1"], kind="설정")
st, hd, b = req(WEBP, "GET", "/app/login", {"Accept": "text/html"}, tok=False)
check(f"로그인 없이 /app/login → {st} index.html, no-cache·CSP", st == 200 and b'id="root"' in b and hdr(hd, "cache-control") == ["no-cache"] and hdr(hd, "content-security-policy")
      and not hdr(hd, "set-cookie"), str(hdr(hd, "cache-control")), kind="설정")
st, hd, b = req(WEBP, "GET", "/app/tool/sql-local?x=1", {**CK, "Accept": "text/html"}, tok=False)
check(f"로그인한 상태로 SPA 경로 /app/tool/sql-local → {st} index.html(세션 쿠키 안 실음)", st == 200 and b'id="root"' in b and not hdr(hd, "set-cookie"), kind="설정")
st, hd, b = req(WEBP, "GET", "/app/assets/index-AbC12345.js", tok=False)
check(f"해시 자산 → {st} {hdr(hd, 'content-type')} 긴 캐시", st == 200 and b == b"console.log(1)" and "immutable" in hdr(hd, "cache-control")[0] and hdr(hd, "content-type")[0].startswith("text/javascript"), kind="설정")
st, hd, b = req(WEBP, "GET", "/app/theme-boot.js", tok=False)
check(f"dist 위 파일(theme-boot.js) → {st} no-cache", st == 200 and hdr(hd, "cache-control") == ["no-cache"], kind="설정")
etag = None
st, hd, _ = req(WEBP, "GET", "/app/assets/index-AbC12345.js", tok=False); etag = hdr(hd, "etag")[0]
st, _, _ = req(WEBP, "GET", "/app/assets/index-AbC12345.js", {"If-None-Match": etag}, tok=False)
check(f"ETag 일치 → {st}", st == 304, kind="설정")
for path, exp in (("/app/assets/old-Zz999999.js", 404), ("/app/nope.png", 404), ("/app/%2e%2e/tools.json", 404), ("/app/..%2f..%2fbind-local%2fapp.py", 404), ("/app", 302)):
    st, hd, b = req(WEBP, "GET", path, tok=False)
    check(f"{path} → {st}", st == exp and b"bind.txt" not in b and b"remote-local" not in b, kind="설정")
st, _, b = req(WEBP, "GET", "/api/me", tok=False)
check(f"WEB_DIST 켜도 로그인 안 한 API 는 {st}", st == 401, kind="설정")
st, _, b = req(WEBP, "GET", "/web/landing.json", tok=False)
lj = json.loads(b) if st == 200 else {}
check(f"/web/landing.json(로그인 전 랜딩 분류) → {st} 도구 {lj.get('tools')}개", st == 200 and lj.get("tools") == len(BAD_HOSTS) + 4 and lj["groups"][0]["name"] == "시험", kind="설정")
st, hd, _ = req(WEBP, "GET", "/app/login?next=%2Ft%2Ffake-local%2F", CK, tok=False)
check(f"로그인한 상태로 /app/login → {st} {hdr(hd, 'location')}", st == 302 and hdr(hd, "location") == ["/app/tool/fake-local"], kind="설정")
for nx, loc in (("", "/app/"), ("%2Fall", "/app/all"), ("%2Fadmin", "/app/admin"), ("%2Fapprovals", "/app/approvals"), ("%2Fguide%2F", "/guide/"), ("%2Fapp%2Faccount", "/app/account"), ("%2F%2Fevil.example", "/app/")):
    st, hd, _ = req(WEBP, "GET", "/app/login" + ("?next=" + nx if nx else ""), CK, tok=False)
    check(f"로그인한 상태로 /app/login?next={nx} → {st} {hdr(hd, 'location')}", st == 302 and hdr(hd, "location") == [loc], kind="설정")
st, hd, _ = req(WEBP, "GET", "/app/login", {"Cookie": "agsid=" + "x" * 43}, tok=False)
check(f"무효 세션으로 /app/login → {st}(랜딩)", st == 200, kind="설정")
st, _, b = req(WEBP, "GET", "/api/tools", CK, tok=False)
check(f"같은 출처 API(로그인 쿠키) → {st}", st == 200 and "tools" in json.loads(b), kind="설정")
# 배포 = 심볼릭 링크 바꿔 끼우기(agent-page-web scripts/deploy.sh) — 포털 재시작 없이 새 빌드가 나감
WEB2 = os.path.join(T, "webdist2"); os.makedirs(os.path.join(WEB2, "assets"))
open(os.path.join(WEB2, "index.html"), "w").write('<!doctype html><html><head></head><body><div id="root" data-v="2"></div></body></html>')
LNK = os.path.join(T, "webcur"); os.symlink(WEB, LNK)
WEBL = free_port()
launch("weblink", WEBL, {"WEB_DIST": LNK})
st1, _, b1 = req(WEBL, "GET", "/app/login", tok=False)
os.symlink(WEB2, LNK + ".new"); os.replace(LNK + ".new", LNK)   # 원자적으로 바꿈
st2, _, b2 = req(WEBL, "GET", "/app/login", tok=False)
st3, _, _ = req(WEBL, "GET", "/app/assets/index-AbC12345.js", tok=False)
check(f"WEB_DIST 링크를 바꾸면 재시작 없이 새 빌드 → {st1}/{st2}, 옛 자산 {st3}", st1 == 200 and b'data-v="2"' not in b1 and st2 == 200 and b'data-v="2"' in b2 and st3 == 404, kind="설정")

print("\n## '/' 전환 준비(WEB_ROOT=1·WEB_ROOT_DIST — 기본 꺼짐)")
ROOTD = os.path.join(T, "webroot"); os.makedirs(os.path.join(ROOTD, "assets"))
open(os.path.join(ROOTD, "index.html"), "w").write('<!doctype html><html><head><script type="module" src="/assets/index-R00t1234.js"></script></head><body><div id="root" data-base="/"></div></body></html>')
open(os.path.join(ROOTD, "assets", "index-R00t1234.js"), "w").write("console.log(2)")
open(os.path.join(ROOTD, "theme-boot.js"), "w").write("/*boot-root*/")
bad = subprocess.run([os.path.join(VENV, "bin", "python"), "-c", "import fastportal.app"], cwd=REPO, env={**env, "WEB_ROOT": "1"}, capture_output=True, text=True, timeout=60)
check("WEB_ROOT=1 인데 WEB_ROOT_DIST 없음 → 시작 안 함", bad.returncode != 0 and "WEB_ROOT_DIST" in (bad.stderr + bad.stdout), kind="설정")
RTP = free_port()
launch("webroot", RTP, {"WEB_DIST": WEB, "WEB_ROOT": "1", "WEB_ROOT_DIST": ROOTD})
isroot = lambda b: b'data-base="/"' in b
st, hd, b = req(RTP, "GET", "/", {"Accept": "text/html"}, tok=False)
check(f"로그인 없이 / → {st} {hdr(hd, 'location')}(포털과 같은 곳)", st == 302 and hdr(hd, "location") == ["/login?next=%2F"], kind="설정")
st, hd, b = req(RTP, "GET", "/login", {"Accept": "text/html"}, tok=False)
check(f"/login → {st} React(루트 빌드), CSP", st == 200 and isroot(b) and hdr(hd, "content-security-policy"), kind="설정")
for path in ("/", "/all", "/fav", "/recent", "/account", "/approvals", "/admin", "/tool/fake-local", "/tool/fake-local/x/1"):
    st, hd, b = req(RTP, "GET", path, {**CK, "Accept": "text/html"}, tok=False)
    check(f"로그인한 상태로 {path} → {st} React(루트 빌드)", st == 200 and isroot(b), kind="설정")
for nx, loc in (("", "/"), ("%2Ft%2Ffake-local%2F", "/tool/fake-local"), ("%2Fadmin", "/admin"), ("%2Fguide%2F", "/guide/")):
    st, hd, _ = req(RTP, "GET", "/login" + ("?next=" + nx if nx else ""), CK, tok=False)
    check(f"로그인한 상태로 /login?next={nx} → {st} {hdr(hd, 'location')}", st == 302 and hdr(hd, "location") == [loc], kind="설정")
st, hd, b = req(RTP, "GET", "/assets/index-R00t1234.js", tok=False)
check(f"/assets/… → {st} 긴 캐시", st == 200 and b == b"console.log(2)" and "immutable" in hdr(hd, "cache-control")[0], kind="설정")
st, _, b = req(RTP, "GET", "/theme-boot.js", tok=False)
check(f"/theme-boot.js → {st} 루트 빌드 것", st == 200 and b == b"/*boot-root*/", kind="설정")
for path, exp in (("/assets/old-Zz999999.js", 404), ("/assets/..%2f..%2ftools.json", 404)):
    st, _, b = req(RTP, "GET", path, tok=False)
    check(f"{path} → {st}", st == exp and b"remote-local" not in b, kind="설정")
st, _, _ = req(RTP, "GET", "/api/me", tok=False)
st2, _, _ = req(RTP, "GET", "/api/me", CK, tok=False)
check(f"/api 는 포털 그대로 → 로그인 없이 {st}, 로그인 {st2}", st == 401 and st2 == 200, kind="설정")
st, _, b = req(RTP, "GET", "/t/fake-local/echo", CK, tok=False)
check(f"/t/<도구>/ 는 프록시 그대로 → {st}", st == 200, kind="설정")
st, hd, _ = req(RTP, "GET", "/theme/tokens.css", tok=False)
check(f"/theme/ 는 포털 그대로 → {st}", st == 200 and hdr(hd, "content-type")[0].startswith("text/css"), kind="설정")
st, hd, _ = req(RTP, "GET", "/logout", tok=False)   # 쿠키 없이(시험 세션은 남김)
check(f"/logout 은 포털 그대로 → {st} {hdr(hd, 'location')}", st == 302 and hdr(hd, "location") == ["/login"], kind="설정")
st, _, b = req(RTP, "GET", "/landing/home.png", tok=False)
check(f"/landing/home.png 는 포털 그대로 → {st}", st == 200 and b[:4] == b"\x89PNG", kind="설정")
st, _, b = req(RTP, "GET", "/app/login", tok=False)
check(f"/app(WEB_DIST)도 같이 → {st}", st == 200 and b'id="root"' in b and not isroot(b), kind="설정")
st, _, b = req(NEW, "GET", "/", {**CK, "Accept": "text/html"})
check(f"WEB_ROOT 없으면 / 는 예전 포털 화면 → {st}", st == 200 and not isroot(b) and b"/theme/tour.js" in b, kind="설정")

print("\n## 작업 알림 센터(jobs 표 · 내부 경로 · SSE)")
import hmac as _hmac
SECF = os.path.join(DATA, ".portal_jobs_secret")
check("비밀값 파일 권한 600", os.path.exists(SECF) and (os.stat(SECF).st_mode & 0o777) == 0o600, kind="보안")
SEC = open(SECF).read().strip().encode()
jtok = lambda d: _hmac.new(SEC, ("portal-jobs:" + d).encode(), hashlib.sha256).hexdigest()
JE = json.load(open(os.path.join(T, "bind-local", "jobs-env.json")))
check("도구 시작 때 PORTAL_JOBS_URL·TOKEN·TOOL 을 넘김(비밀값은 안 넘김)", JE["PORTAL_JOBS_URL"] == f"http://127.0.0.1:{BINDP}/api/internal/jobs" and JE["PORTAL_JOBS_TOOL"] == "bind-local"
      and JE["PORTAL_JOBS_TOKEN"] == jtok("bind-local") and not JE["PORTAL_JOBS_SECRET"], str({k: bool(v) for k, v in JE.items()}), kind="설정")
st, hd, b = req(NEW, "POST", "/api/signup", {"Content-Type": "application/json"}, json.dumps({"id": "other", "name": "남", "pin": "1234"}).encode(), tok=False)
CK2 = {"Cookie": "agsid=" + hdr(hd, "set-cookie")[0].split(";")[0].split("=", 1)[1]}


def ijob(port, payload, token=None, tool=None, extra=None):
    hd_ = {"Content-Type": "application/json", **(extra or {})}
    if token is not False:
        hd_["X-Portal-Jobs-Token"] = token or jtok(tool or payload.get("tool", ""))
    st, _, b = req(port, "POST", "/api/internal/jobs", hd_, json.dumps(payload).encode(), tok=False)
    return st, json.loads(b or b"{}")


J1 = {"tool": "down-local", "user": "tester", "id": "j1", "status": "running", "title": "시험 작업", "link": "/?job=j1", "message": "도는 중"}
for name, kw, exp in (("토큰 없음", {"token": False}, 403), ("틀린 토큰", {"token": "x" * 64}, 403), ("다른 도구의 토큰", {"token": jtok("fake-local")}, 403),
                      ("로컬 주인 토큰(다른 비밀)", {"token": TOKV}, 403)):
    st, j = ijob(NEW, J1, **kw)
    check(f"내부 경로 {name} → {st}", st == exp, str(j), kind="보안")
st, j = ijob(NEW, {**J1, "tool": "none-local"}, token=jtok("none-local"))
check(f"내부 경로 없는 도구 → {st}(같은 403)", st == 403, kind="보안")
st, j = ijob(PROX, J1, extra={"X-Forwarded-For": "203.0.113.5"})
check(f"내부 경로 바깥 주소(믿을 프록시 뒤 XFF 203.0.113.5)·맞는 토큰 → {st}", st == 403, kind="보안")
st, j = ijob(PROX, {**J1, "tool": "remote-local", "id": "r1"}, extra={"X-Forwarded-For": "127.0.0.2"})
check(f"tools.json host(127.0.0.2) 도구는 그 주소에서 받음 → {st}", st == 200 and j.get("id") == "remote-local:r1", str(j), kind="설정")
st, j = ijob(PROX, {**J1, "tool": "down-local", "id": "r2"}, extra={"X-Forwarded-For": "127.0.0.2"})
check(f"host 주소에서 다른(로컬) 도구 이름으로 → {st}", st == 403, kind="보안")
for name, bad in (("status 틀림", {"status": "weird"}), ("user 형식", {"user": "a b<"}), ("id 형식", {"id": "../x"})):
    st, j = ijob(NEW, {**J1, **bad})
    check(f"내부 경로 {name} → {st}", st == 400, str(j), kind="설정")


def sse_open(port, cookie_hd, since=None, last_id=None):
    s = socket.create_connection(("127.0.0.1", port), 10)
    extra = "".join(f"{k}: {v}\r\n" for k, v in cookie_hd.items()) + (f"Last-Event-ID: {last_id}\r\n" if last_id else "")
    s.sendall(f"GET /api/jobs/stream{'?since=' + str(since) if since else ''} HTTP/1.1\r\nHost: x\r\n{extra}\r\n".encode())
    s.settimeout(10)
    return s


def sse_read(s, want, timeout=5):
    """이벤트(event, data) 목록 — want 개 모이거나 timeout"""
    buf, out, end = b"", [], time.time() + timeout
    s.settimeout(0.2)
    while time.time() < end and len(out) < want:
        try:
            ch = s.recv(65536)
            if not ch:
                break
            buf += ch
        except socket.timeout:
            pass
        while b"\n\n" in buf:
            blk, buf = buf.split(b"\n\n", 1)
            ev = dict(l.split(": ", 1) for l in blk.decode().splitlines() if ": " in l and not l.startswith(":") and "HTTP/" not in l)
            if "event" in ev:
                out.append((ev["event"], json.loads(ev["data"]), ev.get("id")))
    return out


s_me, s_other, s_own = sse_open(NEW, CK), sse_open(NEW, CK2), sse_open(NEW, {"X-Local-Owner": TOKV})
h = [sse_read(x, 1) for x in (s_me, s_other, s_own)]
check("SSE 연결 → hello{last, unread}", all(x and x[0][0] == "hello" and "unread" in x[0][1] for x in h), str(h), kind="동작 확인")
t0 = time.time(); st, j = ijob(NEW, J1)
got = sse_read(s_me, 1); dt = time.time() - t0
check(f"내부 경로 running → 본인 SSE 로 {dt:.2f}s 안에", st == 200 and got and got[0][0] == "job" and got[0][1]["status"] == "running" and got[0][1]["link"] == "/t/down-local/?job=j1"
      and got[0][1]["title"] == "시험 작업" and dt < 1.5, str(got), kind="동작 확인")
st, j = ijob(NEW, {**J1, "status": "done", "message": "끝", "title": ""})
got = sse_read(s_me, 1)
check("done → SSE(제목은 그대로, progress 100, unread)", got and got[0][1]["status"] == "done" and got[0][1]["title"] == "시험 작업" and got[0][1]["progress"] == 100 and got[0][1]["unread"], str(got), kind="동작 확인")
own = sse_read(s_own, 2)
check("주인(owner) SSE 는 남의 작업도 받음", [e[1]["status"] for e in own] == ["running", "done"], str(own), kind="동작 확인")
oth = sse_read(s_other, 1, timeout=1.5)
check("다른 계정 SSE 엔 안 감", oth == [], str(oth), kind="보안")
st, j = ijob(NEW, {**J1, "status": "running"})
check("끝난 작업을 다시 running 으로 → 무시", st == 200 and j.get("ignored") is True, str(j), kind="설정")
st, j = ijob(NEW, {**J1, "user": "other", "status": "failed"})
check(f"같은 id 를 다른 사용자로 → {st}", st == 403, kind="보안")
jl = json.loads(req(NEW, "GET", "/api/jobs?unread=1", CK)[2])
jo = json.loads(req(NEW, "GET", "/api/jobs", CK2)[2])
jw = json.loads(req(NEW, "GET", "/api/jobs")[2])
check("/api/jobs?unread=1: 본인 것(안 읽은 끝난 작업 + 도는 작업) + 안 읽은 개수", [x["id"] for x in jl["jobs"]] == ["down-local:j1", "remote-local:r1"] and jl["unread"] == 1, str(jl), kind="동작 확인")
check("/api/jobs: 다른 계정엔 안 보임 · 주인은 전부", jo["jobs"] == [] and jo["unread"] == 0 and {x["id"] for x in jw["jobs"]} >= {"down-local:j1", "remote-local:r1"}, kind="보안")
st, _, b = req(NEW, "GET", "/api/jobs", tok=False)
check(f"/api/jobs 로그인 없이 → {st}", st == 401, kind="보안")
st, _, b = req(NEW, "GET", "/api/jobs/stream", tok=False)
check(f"/api/jobs/stream 로그인 없이 → {st}", st == 401, kind="보안")
ev = json.loads(req(NEW, "GET", "/api/events", CK)[2])
check("/api/events(예전 모양)에도 끝난 작업", any(e["dir"] == "down-local" and e["ok"] is True and {"id", "name", "sec", "t"} <= set(e) for e in ev["events"]) and ev["last"] > 1_700_000_000,
      str(ev), kind="동작 확인")
st, _, b = req(NEW, "POST", "/api/jobs/read", {**CK2, "Content-Type": "application/json"}, json.dumps({"ids": ["down-local:j1"]}).encode(), tok=False)
check("남의 작업 읽음 표시 → 0건", st == 200 and json.loads(b)["read"] == 0, b.decode(), kind="보안")
st, _, b = req(NEW, "POST", "/api/jobs/read", {**CK, "Content-Type": "application/json"}, json.dumps({"all": True}).encode(), tok=False)
got = sse_read(s_me, 1)
check("모두 읽음 → unread 0 + 본인 SSE 에 read", st == 200 and json.loads(b)["unread"] == 0 and got and got[0][0] == "read" and got[0][1]["ids"] == ["down-local:j1"], f"{b} {got}", kind="동작 확인")
check("읽음 표시는 주인 SSE 로 안 감", sse_read(s_own, 1, timeout=1) == [], kind="동작 확인")
jw2 = json.loads(req(NEW, "GET", "/api/jobs?unread=1")[2])
check("주인의 읽음은 따로(본인이 읽어도 주인에겐 안 읽음)", any(x["id"] == "down-local:j1" and x["unread"] for x in jw2["jobs"]), kind="동작 확인")
last_seq = got and json.loads(req(NEW, "GET", "/api/jobs", CK)[2])["jobs"][0]["seq"]
for x in (s_me, s_other, s_own):
    x.close()
ijob(NEW, {**J1, "id": "j2", "status": "queued", "title": "두 번째"})
s_re = sse_open(NEW, CK, last_id=last_seq)
got = sse_read(s_re, 2)
check("다시 붙을 때 Last-Event-ID 뒤 바뀐 것만 다시 받음", [e[0] for e in got] == ["hello", "job"] and got[1][1]["id"] == "down-local:j2", str(got), kind="동작 확인")
s_re.close()
st, _, b = req(NEW2, "GET", "/api/jobs", CK, tok=False)
check("다른 포털 프로세스(재시작과 같음)에도 남음(DB)", st == 200 and {x["id"] for x in json.loads(b)["jobs"]} == {"down-local:j1", "down-local:j2", "remote-local:r1"}, b.decode()[:200], kind="동작 확인")
print(f"  … SSE {A.streams * 4}개 동시 연결")
pid = procs["new"].pid; t_a, m_a = threads_rss(pid)
many = [sse_open(NEW, CK) for _ in range(A.streams * 4)]
okh = sum(1 for x in many if sse_read(x, 1))
t_b, m_b = threads_rss(pid)
t0 = time.time(); ijob(NEW, {**J1, "id": "j3", "status": "running", "title": "많이"})
okj = sum(1 for x in many if (lambda g: g and g[0][1]["id"] == "down-local:j3")(sse_read(x, 1, timeout=3)))
dt = time.time() - t0
for x in many:
    x.close()
check(f"SSE {len(many)}개: hello {okh}, 새 작업 {okj}개에 {dt:.2f}s, 스레드 {t_a}→{t_b}, RSS {m_a}→{m_b}MB", okh == okj == len(many) and t_b - t_a <= 41, kind="동작 확인")
JA = free_port()
launch("jobsauto", JA, {"PORTAL_JOBS_AUTO_S": "1"})
s_a = sse_open(JA, CK); sse_read(s_a, 1)
st, _, _ = req(JA, "POST", "/t/fake-local/quick-slow", {**CK, "Content-Type": "application/json"}, b"{}", tok=False)
got = sse_read(s_a, 2, timeout=4)
check("자동 기록: 기준(1초) 넘는 프록시 POST → running 뒤 done", [e[1]["status"] for e in got] == ["running", "done"] and got[0][1]["id"].startswith("auto:fake-local:")
      and got[1][1]["link"] == "/t/fake-local/" and got[1][1]["message"].endswith("초 걸림"), str(got), kind="동작 확인")
req(JA, "POST", "/t/fake-local/quick-slow?fail=1", {**CK, "Content-Type": "application/json"}, b"{}", tok=False)
got = sse_read(s_a, 2, timeout=4)
check("자동 기록: 도구가 500 → failed", [e[1]["status"] for e in got] == ["running", "failed"], str(got), kind="동작 확인")
req(JA, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/json"}, b"{}", tok=False)
check("짧은 POST 는 기록 안 함", sse_read(s_a, 1, timeout=1.5) == [], kind="동작 확인")
ijob(JA, {**J1, "tool": "fake-local", "id": "self1", "status": "done"})
sse_read(s_a, 1)
req(JA, "POST", "/t/fake-local/quick-slow", {**CK, "Content-Type": "application/json"}, b"{}", tok=False)
check("자기 알림을 쓰는 도구는 자동 기록 건너뜀(두 번 안 뜸)", sse_read(s_a, 1, timeout=2) == [], kind="동작 확인")
s_a.close()
logs = "".join(open(os.path.join(T, f)).read() for f in os.listdir(T) if f.endswith(".log"))
check("로그에 도구 토큰·비밀값 없음", SEC.decode() not in logs and jtok("bind-local") not in logs and jtok("down-local") not in logs, kind="보안")

print("\n## 삭제 대신 — 비활성화·익명화·탈퇴 요청·부서 보관 (새 포털)")
import sqlite3 as _sq
DBF = os.path.join(DATA, "portal.db")


def jpost(port, path, body, hd=None, tok=True):
    st, h_, b_ = req(port, "POST", path, {**(hd or {}), "Content-Type": "application/json"}, json.dumps(body).encode(), tok=tok)
    try:
        return st, json.loads(b_), h_
    except ValueError:
        return st, {}, h_


def jget(port, path, hd=None, tok=True):
    st, _, b_ = req(port, "GET", path, hd or {}, tok=tok)
    return st, (json.loads(b_) if b_[:1] in (b"{", b"[") else {}), b_


def ck_of(h_):
    return {"Cookie": "agsid=" + hdr(h_, "set-cookie")[0].split(";")[0].split("=", 1)[1]}


def urow(uid):
    con = _sq.connect(DBF); con.row_factory = _sq.Row
    r = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone(); con.close()
    return r


DD = "보관시험부"
st, _, _ = jpost(NEW, "/api/admin/dept", {"op": "add", "name": DD})
check(f"부서 추가 {DD} → {st}", st == 200, kind="새 기능")
st, j, h_ = jpost(NEW, "/api/signup", {"id": "wd-a", "name": "탈퇴시험", "pin": "4321", "dept": DD}, tok=False)
CKA = ck_of(h_)
jpost(NEW, "/api/signup", {"id": "wd-b", "name": "정리시험", "pin": "4321"}, tok=False)
st, j, _ = jpost(NEW, "/api/admin/dept", {"op": "archive", "name": DD})
check(f"대기 요청이 있으면 보관 막음 → {st} {j.get('error', '')[:40]}", st == 400 and "대기 요청 1건" in j.get("error", ""), kind="새 기능")
rid = next(r["id"] for r in jget(NEW, "/api/admin")[1]["pending"] if r["uid"] == "wd-a")
jpost(NEW, "/api/approvals/decide", {"id": rid, "ok": True})
st, j, _ = jpost(NEW, "/api/admin/dept", {"op": "archive", "name": DD})
check(f"소속 인원이 있으면 보관 막음(이름 표시) → {st}", st == 400 and "소속 인원 1명(탈퇴시험)" in j.get("error", ""), j.get("error", ""), kind="새 기능")
st, j, _ = jpost(NEW, "/api/account/withdraw", {"reason": "퇴사"}, CKA, tok=False)
check(f"본인 탈퇴 요청 → {st}", st == 200, kind="새 기능")
st, j, _ = jpost(NEW, "/api/account/withdraw", {}, CKA, tok=False)
check(f"탈퇴 요청 두 번 → {st}", st == 400 and "이미 대기 중" in j.get("error", ""), kind="새 기능")
acc = jget(NEW, "/api/account", CKA, tok=False)[1]
wr = [r for r in acc["requests"] if r["kind"] == "withdraw" and r["status"] == "pending"]
check("내 요청에 탈퇴 요청(관리자에게, 지우지 않음)", len(wr) == 1 and wr[0]["label"] == "탈퇴 요청" and wr[0]["route"] == "관리자" and urow("wd-a")["disabled"] == 0, str(wr), kind="새 기능")
check("탈퇴 요청은 부서장이 아니라 관리자 목록에", any(r["kind"] == "withdraw" for r in jget(NEW, "/api/admin")[1]["pending"]), kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/account", {"op": "deactivate", "uid": "wd-b"}, CK, tok=False)
check(f"일반 계정이 비활성화 시도 → {st}", st == 403, kind="보안")
st, j, _ = jpost(NEW, "/api/approvals/decide", {"id": wr[0]["id"], "ok": True, "note": ""})
r = urow("wd-a")
check(f"탈퇴 승인 → 비활성화(로그인 불가·지우지 않음) {st}", st == 200 and r and r["disabled"] == 1 and r["deactivated"] and "탈퇴 요청" in r["deact_reason"] and r["name"] == "탈퇴시험", kind="새 기능")
check("비활성화 → 세션 끊김(401)", jget(NEW, "/api/me", CKA, tok=False)[0] == 401, kind="새 기능")
st, j, _ = jpost(NEW, "/api/login", {"id": "wd-a", "pin": "4321"}, tok=False)
sto, jo, _ = jpost(OLD, "/api/login", {"id": "wd-a", "pin": "4321"})
check(f"비활성 계정 로그인 거절 — 새 {st}·예전 portal.py {sto}", st == 400 and sto == 400, kind="새 기능")
ad = jget(NEW, "/api/admin")[1]
ua = next(u for u in ad["users"] if u["id"] == "wd-a")
check("관리 목록: 비활성 표시(deactivated·사유)", ua["deactivated"] and ua["deact_reason"].startswith("본인 탈퇴") and not ua["anonymized"], str(ua), kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/disable", {"uid": "wd-a", "disabled": False})
check(f"비활성 계정은 '정지 해제'로 못 풂 → {st}", st == 400, kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/account", {"op": "reactivate", "uid": "wd-a"})
st2, _, h_ = jpost(NEW, "/api/login", {"id": "wd-a", "pin": "4321"}, tok=False)
check(f"다시 활성화 → 로그인 됨 {st}/{st2}", st == 200 and st2 == 200 and urow("wd-a")["deactivated"] is None, kind="새 기능")
CKA = ck_of(h_)
st, _, _ = jpost(NEW, "/api/admin/account", {"op": "anonymize", "uid": "wd-a", "reason": "시험", "confirm": "wd-a"})
check(f"활성 계정은 바로 익명화 못 함 → {st}", st == 400 and urow("wd-a")["anonymized"] is None, kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/account", {"op": "deactivate", "uid": "wd-a", "reason": "시험 정리"})
check(f"관리자 비활성화 → {st}, 세션 끊김", st == 200 and jget(NEW, "/api/me", CKA, tok=False)[0] == 401, kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/account", {"op": "anonymize", "uid": "wd-a", "reason": "시험"})
check(f"익명화 확인값 없음 → {st}", st == 400 and "아이디를 그대로" in j.get("error", ""), kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/account", {"op": "anonymize", "uid": "wd-a", "reason": " ", "confirm": "wd-a"})
check(f"익명화 이유 없음 → {st}", st == 400 and "이유" in j.get("error", ""), kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/account", {"op": "anonymize", "uid": "wd-a", "reason": "시험 익명화", "confirm": "wd-a"})
r = urow("wd-a")
check(f"익명화 → {st}: 이름·부서·PIN 해시 지움, 아이디는 내부 키로 남음", st == 200 and r["name"] == "탈퇴한 사용자" and r["dept"] == "" and r["anonymized"] and r["anon_id"].startswith("deleted-") and r["theme"] is None, kind="새 기능")
st, _, b_ = jget(NEW, "/api/admin")
ad = json.loads(b_)
check("관리 화면 응답에 원래 아이디가 안 나옴(계정·감사 기록 모두 deleted-…)", b"wd-a" not in b_ and "탈퇴시험".encode() not in b_
      and any(u["id"] == r["anon_id"] and u["anonymized"] for u in ad["users"]) and any(a["action"] == "anonymize" and a["target"] == r["anon_id"] for a in ad["audit"]), kind="새 기능")
con = _sq.connect(DBF)
n_aud = con.execute("SELECT COUNT(*) FROM audit WHERE target='wd-a' OR actor='wd-a'").fetchone()[0]; n_req = con.execute("SELECT COUNT(*) FROM requests WHERE uid='wd-a'").fetchone()[0]; con.close()
check(f"감사 기록·요청 연결은 그대로(DB 안 {n_aud}건·{n_req}건)", n_aud >= 5 and n_req == 2, kind="새 기능")
st, _, _ = jpost(NEW, "/api/login", {"id": "wd-a", "pin": "4321"}, tok=False)
sto, _, _ = jpost(OLD, "/api/login", {"id": "wd-a", "pin": "4321"})
st2, j2, _ = jpost(NEW, "/api/signup", {"id": "wd-a", "name": "새사람", "pin": "1111"}, tok=False)
check(f"익명화 계정: 로그인 {st}·예전 포털 {sto}·같은 아이디 재가입 {st2}(남이 데이터를 물려받지 못함)", st == 400 and sto == 400 and st2 == 400, kind="보안")
for op in ("reactivate", "deactivate"):
    st, _, _ = jpost(NEW, "/api/admin/account", {"op": op, "uid": "wd-a"})
    check(f"익명화 계정 {op} → {st}(되돌릴 수 없음)", st == 400, kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/reset", {"uid": "wd-a"})
check(f"익명화 계정 PIN 초기화 → {st}", st == 400, kind="새 기능")
st, j, _ = jpost(NEW, "/api/admin/dept", {"op": "archive", "name": DD})
dl = jget(NEW, "/api/depts", tok=False)[1]["depts"]; dlo = jget(OLD, "/api/depts")[1]["depts"]
check(f"소속·대기 없으면 보관 {st} — 가입 목록에서 빠짐(새·예전 포털)", st == 200 and DD not in dl and DD not in dlo, kind="새 기능")
ad = jget(NEW, "/api/admin")[1]
check("관리 목록: 보관 표시(archived)", any(d["name"] == DD and d["archived"] and d["hidden"] for d in ad["depts"]), kind="새 기능")
st, j, _ = jpost(NEW, "/api/signup", {"id": "wd-c", "name": "x", "pin": "1234", "dept": DD}, tok=False)
check(f"보관 부서로 가입 → {st}", st == 400, kind="새 기능")
for op in ("add", "show", "archive"):
    st, j, _ = jpost(NEW, "/api/admin/dept", {"op": op, "name": DD})
    check(f"보관 부서 {op} → {st} {j.get('error', '')[:30]}", st == 400, kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/dept", {"op": "restore", "name": DD})
check(f"되살리기 → {st}, 가입 목록에 다시", st == 200 and DD in jget(NEW, "/api/depts", tok=False)[1]["depts"], kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/dept", {"op": "restore", "name": DD})
check(f"보관 안 된 부서 되살리기 → {st}", st == 400, kind="새 기능")
st, _, _ = jpost(NEW, "/api/admin/dept", {"op": "archive", "name": DD}, CK, tok=False)
check(f"일반 계정 부서 보관 → {st}", st == 403, kind="보안")
jpost(NEW, "/api/admin/account", {"op": "deactivate", "uid": "wd-b", "reason": "정리 시험"})
time.sleep(0.5)
check("정리 작업은 기본 꺼짐(비활성 계정 그대로)", urow("wd-b")["anonymized"] is None, kind="새 기능")
ANP = free_port()
launch("anon", ANP, {"PORTAL_ANON_AUTO": "1", "PORTAL_ANON_AFTER_DAYS": "0"})
for _ in range(50):
    if urow("wd-b")["anonymized"]:
        break
    time.sleep(0.1)
con = _sq.connect(DBF); sysa = con.execute("SELECT detail FROM audit WHERE actor='system' AND action='anonymize' AND target='wd-b'").fetchone(); con.close()
check("PORTAL_ANON_AUTO=1(기한 0일로 시험) → 비활성 계정 익명화, 감사 기록 actor=system", urow("wd-b")["anonymized"] and sysa and "자동 정리" in sysa[0], str(sysa), kind="새 기능")
check("정리 작업은 활성 계정을 건드리지 않음", urow("tester")["anonymized"] is None and urow("tester")["name"] == "시험", kind="새 기능")

print("\n## 계정 저장소 바꿔 끼우기(AUTH_BACKEND) — 가짜 외부 저장소·설정 없는 external (새 포털)")
check("/api/auth(local): 가입·PIN 변경 받음", jget(NEW, "/api/auth", tok=False)[1] == {"signup": True, "password_change": True, "guide": ""}, kind="새 기능")
ab = jget(NEW, "/api/admin")[1].get("auth_backend") or {}
check("/api/admin.auth_backend(local) = 임시(포털 내부 DB)", ab.get("name") == "local" and ab.get("label") == "임시(포털 내부 DB)", str(ab), kind="새 기능")
check("local 이면 화면에 AG_AUTH 안 끼움(화면 그대로)", b"AG_AUTH=" not in req(NEW, "GET", "/login", tok=False)[2] and b"AG_AUTH=" not in req(NEW, "GET", "/account", CK, tok=False)[2], kind="새 기능")
FAKEMOD = os.path.join(T, "fakeauth")
os.makedirs(FAKEMOD)
open(os.path.join(FAKEMOD, "fake_ext_auth.py"), "w").write(
    "from fastportal.auth.external import ExternalBackend\n"
    "USERS = {'chat.kim': {'id': 'chat.kim', 'name': '챗봇김', 'pw': 'Chat-pw-1'}, 'chat.off': {'id': 'chat.off', 'name': '꺼짐', 'pw': 'x', 'disabled': True},\n"
    "         'boss': {'id': 'boss', 'name': '외부보스', 'pw': 'b'}, 'owner': {'id': 'owner', 'name': 'x', 'pw': 'o'}}\n"
    "class FakeExternal(ExternalBackend):\n"
    "    label = '외부(시험용 가짜)'\n"
    "    def ready(self): return True\n"
    "    def _lookup(self, uid): return USERS.get(uid)\n"
    "    def _check(self, row, secret): return secret == row['pw']\n")
EXT, EXT0 = free_port(), free_port()
launch("ext", EXT, {"AUTH_BACKEND": "fake_ext_auth:FakeExternal", "PYTHONPATH": FAKEMOD})
launch("ext0", EXT0, {"AUTH_BACKEND": "external"})
TOK_PORTS.add(EXT)
au = jget(EXT, "/api/auth", tok=False)[1]
check("/api/auth(external): 가입·PIN 변경 끔 + 안내", au.get("signup") is False and au.get("password_change") is False and "챗봇" in au.get("guide", ""), str(au), kind="새 기능")
st, j, _ = jpost(EXT, "/api/signup", {"id": "ext-new", "name": "x", "pin": "1234"}, tok=False)
check(f"external: 가입 막힘 → {st} + 안내", st == 403 and "챗봇" in j.get("error", "") and urow("ext-new") is None, str(j), kind="새 기능")
st, j, _ = jpost(EXT, "/api/login", {"id": "chat.kim", "pin": "wrong"}, tok=False)
check(f"external: 틀린 비밀번호 → {st}", st == 400 and "틀립니다" in j.get("error", ""), kind="새 기능")
st, j, h_ = jpost(EXT, "/api/login", {"id": " Chat.Kim ", "pin": "Chat-pw-1"}, tok=False)
r = urow("chat.kim")
check(f"external: 가짜 저장소로 로그인 성공 → {st}, 포털 쪽 행 생성(PIN 열 비움)", st == 200 and j.get("id") == "chat.kim" and r and r["name"] == "챗봇김" and r["hash"] is None, f"{st} {j}", kind="새 기능")
CKE = ck_of(h_) if st == 200 else {}
me = jget(EXT, "/api/me", CKE, tok=False)[1]
check("external: 세션·/api/me 는 포털 그대로(아이디 키, 일반 역할)", me.get("id") == "chat.kim" and me.get("role") == "일반" and me.get("agent_user") == "chat.kim", str(me), kind="새 기능")
pr = json.loads(req(EXT, "GET", "/t/fake-local/echo", CKE, tok=False)[2])
check("external: 도구로 X-Agent-User=chat.kim", dict(pr["headers"]).get("x-agent-user") == "chat.kim", kind="새 기능")
st, j, _ = jpost(EXT, "/api/pin", {"pin": "Chat-pw-1", "new": "5678"}, CKE, tok=False)
check(f"external: PIN 변경 막힘 → {st} + 안내", st == 403 and "챗봇" in j.get("error", ""), str(j), kind="새 기능")
st, j, _ = jpost(EXT, "/api/admin/reset", {"uid": "chat.kim"})
check(f"external: 관리자 PIN 초기화 막힘 → {st}", st == 403, str(j), kind="새 기능")
ab = jget(EXT, "/api/admin")[1].get("auth_backend") or {}
check("external: /api/admin.auth_backend 표시(관리자)", ab.get("name") == "external" and ab.get("label") == "외부(시험용 가짜)" and ab.get("password_change") is False, str(ab), kind="새 기능")
lg, ac = req(EXT, "GET", "/login", tok=False)[2], req(EXT, "GET", "/account", CKE, tok=False)[2]
check("external: 로그인·계정 화면에 AG_AUTH(가입·PIN 변경 숨김) — doctype 앞이 아님", b"window.AG_AUTH=" in lg and b"window.AG_AUTH=" in ac and lg.lstrip()[:9].lower() == b"<!doctype", kind="새 기능")
st, _, _ = jpost(EXT, "/api/login", {"id": "chat.off", "pin": "x"}, tok=False)
check(f"external: 저장소에서 막힌 계정 → {st}", st == 400, kind="새 기능")
st, _, _ = jpost(EXT, "/api/login", {"id": "owner", "pin": "o"}, tok=False)
check(f"external: 아이디 owner(로컬 주인 자리) 거절 → {st}", st == 400, kind="보안")
jpost(EXT, "/api/admin/account", {"op": "deactivate", "uid": "chat.kim", "reason": "시험"})
st, _, _ = jpost(EXT, "/api/login", {"id": "chat.kim", "pin": "Chat-pw-1"}, tok=False)
check(f"external: 포털에서 비활성화하면 비밀번호가 맞아도 거절 → {st}", st == 400 and jget(EXT, "/api/me", CKE, tok=False)[0] == 401, kind="새 기능")
st, j, _ = jpost(EXT0, "/api/login", {"id": "someone", "pin": "x"}, tok=False)
check(f"external(설정 없음): 로그인 → {st} 연결 안 됨 안내(내부 정보 없음)", st == 503 and "아직 연결되지" in j.get("error", ""), str(j), kind="새 기능")
st, j, _ = jpost(EXT0, "/api/signup", {"id": "someone", "name": "x", "pin": "1234"}, tok=False)
check(f"external(설정 없음): 가입 → {st}", st == 403, kind="새 기능")
bad = subprocess.run([os.path.join(VENV, "bin", "python"), "-c", "import fastportal.app"], cwd=REPO, env={**env, "AUTH_BACKEND": "nope"}, capture_output=True, text=True, timeout=60)
check("AUTH_BACKEND 틀리면 기동 안 함", bad.returncode != 0 and "AUTH_BACKEND" in bad.stderr, bad.stderr[-200:], kind="새 기능")

print("\n## 보안 — 외부 화면을 붙일 때(새 포털, 예전 포털과 일부러 다름)")
import hmac as _hm
KEY = _hm.new(open(SECF).read().strip().encode(), b"portal-proxy", hashlib.sha256).hexdigest()
rb = json.loads(req(NEW, "GET", "/t/fake-local/echo", CK)[2]); h = dict(rb["headers"])
ok_sig = bool(h.get("x-agent-auth")) and _hm.compare_digest(h["x-agent-auth"].split(".")[2], _hm.new(KEY.encode(), f"v1|{h['x-agent-auth'].split('.')[1]}|tester".encode(), hashlib.sha256).hexdigest())
check("도구로 가는 요청에 포털 서명(X-Agent-Auth = HMAC(키, v1|초|사용자))", ok_sig and abs(time.time() - int(h["x-agent-auth"].split(".")[1])) < 60, h.get("x-agent-auth", "")[:20], kind="보안")
rb = json.loads(req(NEW, "GET", "/t/fake-local/echo", {**CK, "X-Agent-Auth": "v1.1.forged"})[2])
check("밖에서 보낸 X-Agent-Auth 는 버리고 포털 값으로", dict(rb["headers"]).get("x-agent-auth", "").split(".")[2] != "forged", kind="보안")
check("도구를 띄울 때 PORTAL_TOOL_KEY 를 넘김(포털 비밀값은 안 넘김)", JE.get("PORTAL_TOOL_KEY") == KEY and not JE.get("PORTAL_JOBS_SECRET"), kind="보안")
st, hd_, b_ = req(NEW, "GET", "/t/fake-local/agcookie", CK)
sc = hdr(hd_, "set-cookie")
check("도구가 포털 쿠키(agsid·AGTHEME)를 덮는 Set-Cookie 는 버림, 다른 쿠키는 그대로", st == 200 and sc == ["c=3; Path=/"], str(sc), kind="보안")
st, hd_, b_ = req(NEW, "GET", "/t/fake-local/", CK)
csp = (hdr(hd_, "content-security-policy") or [""])[0]
check("도구 화면: nosniff·X-Frame-Options·CSP frame-ancestors", hdr(hd_, "x-content-type-options") == ["nosniff"] and hdr(hd_, "x-frame-options") == ["SAMEORIGIN"] and "frame-ancestors 'self'" in csp, csp, kind="보안")
st, hd_, b_ = req(NEW, "GET", "/t/fake-local/api/doc", CK)
check("API 가 주는 HTML(사용자 내용)은 CSP sandbox(포털 쿠키·API 에 못 닿음)", (hdr(hd_, "content-security-policy") or [""])[0].startswith("sandbox"), str(hdr(hd_, "content-security-policy")), kind="보안")
st, hd_, b_ = req(NEW, "GET", "/login", tok=False)
check("고전 로그인 화면: CSP frame-ancestors·X-Frame-Options", "frame-ancestors" in (hdr(hd_, "content-security-policy") or [""])[0] and hdr(hd_, "x-frame-options") == ["SAMEORIGIN"], kind="보안")
for m in ("PUT", "DELETE", "PATCH"):
    st, _, b_ = req(NEW, m, "/t/fake-local/echo?x=1", {**CK, "Content-Type": "application/json"}, b'{"a":1}')
    j = json.loads(b_) if st == 200 else {}
    check(f"{m} 도 도구로 프록시(예전 501)", st == 200 and j.get("method") == m and j.get("len") == 7, f"{st}", kind="보안")
st, hd_, _ = req(NEW, "HEAD", "/t/fake-local/echo", CK)
check("HEAD 도 프록시", st == 200, f"{st}", kind="보안")
st, hd_, b_ = req(NEW, "DELETE", "/api/me", CK)
check("포털 API 에 DELETE → 405 + Allow", st == 405 and hdr(hd_, "allow") == ["GET, POST"], f"{st}", kind="보안")
st, hd_, b_ = req(NEW, "OPTIONS", "/api/me", {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}, tok=False)
check("OPTIONS: CORS 허용 목록이 비면 CORS 머리 없음(다른 출처 막힘)", st == 204 and not hdr(hd_, "access-control-allow-origin"), f"{st}", kind="보안")
for name, extra, exp in (("Origin 다른 사이트", {"Origin": "http://evil.example"}, 403), ("Origin null(sandbox)", {"Origin": "null"}, 403),
                         ("Referer 다른 사이트", {"Referer": "http://evil.example/x"}, 403), ("Sec-Fetch-Site cross-site", {"Sec-Fetch-Site": "cross-site"}, 403),
                         ("Origin 같은 출처", {"Origin": f"http://127.0.0.1:{NEW}"}, 200), ("헤더 없음(curl·스크립트)", {}, 200)):
    st, j, _ = jpost(NEW, "/api/account/theme", {"theme": "v2-dark"}, {**CK, **extra}, tok=False)
    check(f"요청 위조 검사(CSRF) — {name} → {st}", st == exp and (exp != 403 or j.get("code") == "csrf"), str(j)[:80], kind="보안")
st, j, _ = jpost(NEW, "/api/login", {"id": "tester", "pin": "1234"}, {"Origin": "http://evil.example"}, tok=False)
check("다른 사이트에서 로그인 POST(로그인 위조) → 403", st == 403, f"{st}", kind="보안")
st, j, _ = jget(NEW, "/api/version", tok=False) if "jget" in globals() else (0, {}, None)
check("GET /api/version 로그인 없이(api 계약 버전)", st == 200 and j.get("api") and "auth" in j, str(j)[:120], kind="새 기능")
st, j, _ = jget(NEW, "/api/landing", tok=False)
check("GET /api/landing 로그인 없이(WEB_DIST 없어도)", st == 200 and "groups" in j, str(j)[:80], kind="새 기능")
st, j, hd_ = jpost(NEW, "/api/signup", {"id": "sec1", "name": "보안", "pin": "123456"})
ck2 = {"Cookie": hdr(hd_, "set-cookie")[0].split(";")[0]}
st, _, _ = req(NEW, "GET", "/logout", {**ck2, "Sec-Fetch-Site": "cross-site"}, tok=False)
st2, _, _ = req(NEW, "GET", "/api/me", ck2, tok=False)
check("다른 사이트 링크로 GET /logout → 로그아웃 안 됨", st == 302 and st2 == 200, f"{st} {st2}", kind="보안")
st, j, hd_ = jpost(NEW, "/api/logout", {}, ck2, tok=False)
st2, _, _ = req(NEW, "GET", "/api/me", ck2, tok=False)
check("POST /api/logout → 200 + 쿠키 지움 + 세션 끝", st == 200 and j.get("ok") and "Max-Age=0" in hdr(hd_, "set-cookie")[0] and st2 == 401, f"{st} {st2}", kind="새 기능")
st, j, _ = jpost(NEW, "/api/logout", {}, tok=False)
check("POST /api/logout 로그인 안 돼 있어도 200", st == 200, f"{st}", kind="새 기능")
st, j, _ = jget(NEW, "/api/account/notices?peek=1", CK, tok=False)
st2, j2, _ = jpost(NEW, "/api/account/notices", {}, CK, tok=False)
check("알림: GET ?peek=1(읽음 처리 안 함) · POST(읽음 처리)", st == 200 and st2 == 200 and "notices" in j and "notices" in j2, kind="새 기능")
st, j, _ = jget(NEW, "/t/hidden-local/echo", CK, tok=False)
st2, j2, _ = jpost(NEW, "/api/start", {"dir": "hidden-local"}, CK, tok=False)
st3, _, _ = req(NEW, "GET", "/t/hidden-local/echo")   # 로컬 주인(토큰)
check("숨긴 도구: 일반 사용자 프록시·시작 → 404, 관리자는 됨", st == 404 and st2 == 404 and st3 == 200, f"{st} {st2} {st3}", kind="보안")
s_ = socket.create_connection(("127.0.0.1", NEW), 5); t0 = time.time()
s_.sendall(b"POST /api/post HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n1000\r\n" + b"x" * 4096 + b"\r\n")
s_.settimeout(5); got = b""
try:
    while True:
        d_ = s_.recv(4096)
        if not d_:
            break
        got += d_
except socket.timeout:
    pass
s_.close()
check("로그인 안 된 큰(청크) POST → 본문을 기다리지 않고 401 + 연결 닫음", got.startswith(b"HTTP/1.1 401") and b"connection: close" in got.lower() and time.time() - t0 < 4, got[:40].decode(errors="replace"), kind="보안")
# 접속지 잠금(마지막 — 이 뒤로는 127.0.0.1 로그인이 10분 막힘)
for i in range(31):
    st, j, _ = jpost(NEW, "/api/login", {"id": f"nobody{i}", "pin": "000000"}, tok=False)
check("한 접속지가 아이디를 바꿔 가며 30번 넘게 틀림 → 429 ip_locked", st == 429 and j.get("code") == "ip_locked", str(j)[:80], kind="보안")

if A.slow:
    print("\n## 1분 넘는 POST → /api/events (61초)")
    since0 = json.loads(req(NEW, "GET", "/api/events", CK)[2])["last"]   # 앞 시험(작업 알림 센터)이 같은 DB 에 남긴 것은 빼고 비교
    ths = [threading.Thread(target=req, args=(p, "POST", "/t/fake-local/slow", {**CK, "Content-Type": "application/json"}, b"{}")) for p in (OLD, NEW)]
    [t.start() for t in ths]; [t.join() for t in ths]
    ea = json.loads(req(OLD, "GET", "/api/events", CK)[2]); eb = json.loads(req(NEW, "GET", f"/api/events?since={since0}", CK)[2])
    strip = lambda e: [{k: v for k, v in x.items() if k in ("dir", "name", "user", "ok")} for x in e["events"]]
    check("/api/events 기록 같음(예전 키 — 새 포털은 title·link 를 더 줌)", strip(ea) == strip(eb) and len(strip(eb)) == 1, f"{strip(ea)} / {strip(eb)}")

for p in procs.values():
    p.send_signal(signal.SIGTERM)
for p in procs.values():
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
tool_srv.shutdown(); remote_srv.shutdown()
errs = [l for f in ("new.log", "ext.log") for l in open(os.path.join(T, f)) if "Traceback" in l or "error:" in l]
check("새 포털 로그에 예외 없음", not errs, "".join(errs[:5]))
shutil.rmtree(T, ignore_errors=True)
n_ok = sum(r["ok"] for r in RESULTS)
print(f"\n== 가짜 도구 시험: {len(RESULTS)}건 중 통과 {n_ok}")
json.dump(RESULTS, open(os.environ.get("FAKE_JSON", os.devnull), "w"), ensure_ascii=False, indent=1)
sys.exit(0 if n_ok == len(RESULTS) else 1)
