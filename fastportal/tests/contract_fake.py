#!/usr/bin/env python3
"""계약 시험(가짜 도구): 임시 폴더에 portal.py 사본과 fastportal 을 같은 임시 DB·가짜 tools.json 으로 띄우고, 이 시험이 띄운
가짜 도구(받은 헤더·본문을 그대로 돌려줌, SSE)를 거쳐 프록시 규칙·스트리밍·끊김·잠금·동시 스트림을 확인한다. 실제 데이터·도구·LLM 은 안 씀.
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
        if self.path.startswith("/slow"):
            n = int(self.headers.get("Content-Length") or 0); n and self.rfile.read(n)
            time.sleep(61); return self._json({"ok": True})
        self._echo()

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
           {"dir": "down-local", "port": DOWN_PORT, "name": "꺼짐", "title": "꺼진 도구", "group": "시험"}], open(os.path.join(ROOT, "tools.json"), "w"), ensure_ascii=False)
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


# 계정(임시 DB): 가입 → 쿠키
st, hd, b = req(OLD, "POST", "/api/signup", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "name": "시험", "pin": "1234"}).encode())
TOK = hdr(hd, "set-cookie")[0].split(";")[0].split("=", 1)[1]
CK = {"Cookie": f"agsid={TOK}; agtheme=v2-dark; toolck=keep"}

print("\n## 헤더 규칙 (도구가 받은 헤더 비교)")
spoof = {**CK, "X-Agent-User": "boss", "X-User": "boss", "X-Forwarded-User": "boss", "Accept-Encoding": "gzip", "X-Custom": "1", "Te": "trailers"}
ra = json.loads(req(OLD, "GET", "/t/fake-local/echo?q=%2F1", spoof)[2]); rb = json.loads(req(NEW, "GET", "/t/fake-local/echo?q=%2F1", spoof)[2])
check("사용자 헤더 버림·포털 값(X-Agent-User=tester)", dict(rb["headers"]).get("x-agent-user") == "tester" and "x-user" not in dict(rb["headers"]), str(rb["headers"]))
check("agsid·agtheme 쿠키는 도구로 안 감, 도구 쿠키는 감", dict(rb["headers"]).get("cookie") == "toolck=keep")
check("도구가 받은 헤더·경로 같음(old=new)", ra == rb, "" if ra == rb else f"{ra} ≠ {rb}")
ra = json.loads(req(OLD, "GET", "/t/fake-local/echo")[2]); rb = json.loads(req(NEW, "GET", "/t/fake-local/echo")[2])
check("서버 안 쿠키 없음(+토큰) → X-Agent-User=owner", dict(rb["headers"]).get("x-agent-user") == "owner" and ra == rb)
check("X-Local-Owner 토큰은 도구로 안 감", "x-local-owner" not in dict(rb["headers"]), str(rb["headers"]))

print("\n## 요청 본문")
body = os.urandom(3 * 1024 * 1024)
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body)[2])
rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", {**CK, "Content-Type": "application/octet-stream"}, body)[2])
check("Content-Length 본문 3MB 그대로(old=new)", ra == rb and rb["len"] == len(body), f"{rb['len']} {rb['sha']}")
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", CK, b"a=1")[2]); rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", CK, b"a=1")[2])
check("Content-Type 없을 때 기본값 같음", ra == rb, f"{dict(ra['headers']).get('content-type')} / {dict(rb['headers']).get('content-type')}")
ra = json.loads(req(OLD, "POST", "/t/fake-local/echo", CK, b"")[2]); rb = json.loads(req(NEW, "POST", "/t/fake-local/echo", CK, b"")[2])
check("빈 POST 같음", ra == rb, "" if ra == rb else f"{ra} ≠ {rb}")
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
             "/t/fake-local", "/t/none-local/"):
    sa, ha, ba = req(OLD, "GET", path, CK); sb, hb, bb = req(NEW, "GET", path, CK)
    keep = lambda h: [(k, v) for k, v in h if k not in ("date", "server", "connection", "transfer-encoding", "keep-alive")]
    ba_, bb_ = ba, bb
    if path.endswith("redir"):   # 도구가 받은 헤더 비교(전송 헤더는 _echo 가 뺌)
        ba_, bb_ = json.loads(ba), json.loads(bb)
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
check("5번 틀린 뒤 맞는 PIN 도 잠김", s6 == 400 and "10분" in json.loads(b6)["error"], json.loads(b6)["error"])
launch("new2", NEW2)
s7, _, b7 = req(NEW2, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "pin": "1234"}).encode())
check("다른 fastportal 프로세스에서도 잠김(같은 DB)", s7 == 400 and "10분" in json.loads(b7)["error"], kind="의도한 차이")
s8, _, _ = req(OLD, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "tester", "pin": "1234"}).encode())
check("예전 포털(메모리)은 모름 → 로그인 됨(참고)", s8 == 200, kind="참고")
sa, ha, ba = req(OLD, "POST", "/api/login", {"Content-Type": "application/json"}, bad)
sb, hb, bb = req(NEW, "POST", "/api/login", {"Content-Type": "application/json"}, json.dumps({"id": "nobody", "pin": "9999"}).encode())
check("틀린 PIN 문구 같음", sa == sb == 400 and json.loads(ba) == json.loads(bb), f"{ba.decode()} / {bb.decode()}")

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

if A.slow:
    print("\n## 1분 넘는 POST → /api/events (61초)")
    ths = [threading.Thread(target=req, args=(p, "POST", "/t/fake-local/slow", {**CK, "Content-Type": "application/json"}, b"{}")) for p in (OLD, NEW)]
    [t.start() for t in ths]; [t.join() for t in ths]
    ea = json.loads(req(OLD, "GET", "/api/events", CK)[2]); eb = json.loads(req(NEW, "GET", "/api/events", CK)[2])
    strip = lambda e: [{k: v for k, v in x.items() if k not in ("id", "t", "sec")} for x in e["events"]]
    check("/api/events 기록 같음", strip(ea) == strip(eb) and len(eb["events"]) == 1, f"{strip(ea)} / {strip(eb)}")

for p in procs.values():
    p.send_signal(signal.SIGTERM)
for p in procs.values():
    try:
        p.wait(10)
    except subprocess.TimeoutExpired:
        p.kill()
tool_srv.shutdown()
errs = [l for l in open(os.path.join(T, "new.log")) if "Traceback" in l or "error:" in l]
check("새 포털 로그에 예외 없음", not errs, "".join(errs[:5]))
shutil.rmtree(T, ignore_errors=True)
n_ok = sum(r["ok"] for r in RESULTS)
print(f"\n== 가짜 도구 시험: {len(RESULTS)}건 중 통과 {n_ok}")
json.dump(RESULTS, open(os.environ.get("FAKE_JSON", os.devnull), "w"), ensure_ascii=False, indent=1)
sys.exit(0 if n_ok == len(RESULTS) else 1)
