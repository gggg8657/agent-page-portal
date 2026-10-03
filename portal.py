#!/usr/bin/env python3
"""에이전트 페이지 포털 — 도구 목록·상태·바로가기·시작/중지. stdlib만.  python3 portal.py  → http://localhost:8700
도구 목록은 tools.json. 각 도구는 자기 폴더의 setup.sh 로 PORT 를 넘겨 기동한다(setup.sh 없으면 app.py 직접)."""
import datetime, json, os, socket, subprocess, sys, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(ROOT)
PORT = int(os.environ.get("PORT", "8700"))
HOST = os.environ.get("PORTAL_HOST", "localhost")  # 링크에 쓸 호스트명 (서버 배포 시 IP/도메인)
TOOLS = json.load(open(os.path.join(ROOT, "tools.json"), encoding="utf-8"))
STATS = os.path.join(ROOT, "_stats.json")  # ponytail: json 파일에 열기 기록 [{dir, ts, ip}], 수천 건까지는 충분


def stats():
    try:
        return json.load(open(STATS, encoding="utf-8"))
    except Exception:
        return []


def usage(d, rows):
    r = [x for x in rows if x["dir"] == d and not x.get("like")]
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    return {"recent": sum(1 for x in r if x["ts"] >= cut), "total": len(r), "users": len({x["ip"] for x in r}),
            "likes": sum(1 for x in rows if x["dir"] == d and x.get("like"))}


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
    env = {**os.environ, "PORT": str(t["port"])}
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        cmd = ["bash", "setup.sh"]
    else:  # ponytail: setup.sh 없는 도구는 app.py 직접 (saju-local)
        py = os.path.join(cwd, "venv", "bin", "python")
        cmd = [py if os.path.exists(py) else sys.executable, "app.py"]
    subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(cwd, "server.log"), "ab"), stderr=subprocess.STDOUT,
                     start_new_session=True)


def stop(t):
    cwd = os.path.join(BASE, t["dir"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        subprocess.run(["bash", "setup.sh", "stop"], cwd=cwd, capture_output=True)
    subprocess.run(["pkill", "-f", f"{t['dir']}/app.py"], capture_output=True)
    subprocess.run(["bash", "-c", f"lsof -ti tcp:{t['port']} | xargs kill 2>/dev/null"], capture_output=True)


HTML = open(os.path.join(ROOT, "portal.html"), encoding="utf-8").read()
HELP = open(os.path.join(ROOT, "help.html"), encoding="utf-8").read()  # 도구 페이지에 끼워 넣는 ? 버튼 + 사용법 팝업


def guide(d):
    p = os.path.join(ROOT, "guides", f"{d}.md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else f"## {d}\n\n사용법이 아직 없습니다. `portal/guides/{d}.md` 를 만들면 여기에 뜹니다."


HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host", "content-length"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def proxy(self):
        """/t/<dir>/<rest> → http://127.0.0.1:<port>/<rest>. 도구 UI 는 상대경로만 쓰므로 그대로 통과. SSE 는 read1 로 바로 흘림."""
        parts = self.path.split("/", 3)  # ['', 't', dir, rest]
        t = tool(parts[2]) if len(parts) > 2 else None
        if not t:
            return self._send({"error": "unknown tool"}, code=404)
        if len(parts) < 4:  # /t/dir → /t/dir/ (상대경로 기준점)
            self.send_response(302); self.send_header("Location", f"/t/{t['dir']}/"); self.send_header("Content-Length", "0"); self.end_headers(); return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        req = urllib.request.Request(f"http://127.0.0.1:{t['port']}/{parts[3]}", data=body, headers=hdr, method=self.command)
        try:
            r = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            return self._send({"error": f"{t['dir']} 응답 없음 ({type(e).__name__}) — 포털에서 '시작'을 누르세요"}, code=502)
        inject = self.command == "GET" and parts[3] in ("", "index.html") and "text/html" in (r.headers.get("Content-Type") or "")
        if inject:  # 도구 첫 화면: ? 버튼 + 사용법 팝업을 끝에 붙인다 (도구 파일은 손대지 않음)
            body = r.read() + HELP.replace("%DIR%", t["dir"]).encode()
            r.close()
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        length = r.headers.get("Content-Length")
        if length:
            self.send_header("Content-Length", length)
        else:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            r.close()
            if not length:
                self.close_connection = True

    def _send(self, body, ctype="application/json", code=200):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/t/"):
            return self.proxy()
        if self.path == "/api/tools":
            rows = stats()
            return self._send([{**t, "up": alive(t["port"]), "installed": os.path.isdir(os.path.join(BASE, t["dir"])),
                                "url": f"/t/{t['dir']}/", "url_direct": f"http://{HOST}:{t['port']}", "usage": usage(t["dir"], rows),
                                "readme": os.path.exists(os.path.join(BASE, t["dir"], "README.md"))} for t in TOOLS])
        if self.path.startswith("/api/readme/"):
            d = self.path.split("/")[-1]
            p = os.path.join(BASE, d, "README.md") if tool(d) else ""
            return self._send((open(p, encoding="utf-8").read() if p and os.path.exists(p) else "README 없음").encode(), "text/plain; charset=utf-8")
        if self.path.startswith("/api/guide/"):
            d = self.path.split("/")[-1]
            return self._send(guide(d).encode() if (tool(d) or d == "portal") else b"", "text/plain; charset=utf-8")
        if self.path == "/api/feed":
            try:
                return self._send(json.load(open(os.path.join(ROOT, "feed.json"), encoding="utf-8")))
            except Exception:
                return self._send([])
        self._send((HTML + HELP.replace("%DIR%", "portal")).encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.startswith("/t/"):
            return self.proxy()
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        t = tool(req.get("dir"))
        if not t:
            return self._send({"error": "unknown tool"})
        if self.path == "/api/like":
            rows = stats() + [{"dir": t["dir"], "ts": datetime.datetime.now().isoformat(timespec="seconds"), "ip": self.client_address[0], "like": True}]
            json.dump(rows, open(STATS, "w", encoding="utf-8"))
            return self._send({"ok": True})
        if self.path == "/api/open":  # 열기 클릭 기록 → 사용 통계
            rows = stats() + [{"dir": t["dir"], "ts": datetime.datetime.now().isoformat(timespec="seconds"), "ip": self.client_address[0]}]
            json.dump(rows, open(STATS, "w", encoding="utf-8"))
            return self._send({"ok": True})
        (start if self.path == "/api/start" else stop)(t)
        self._send({"ok": True})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("start-all", "stop-all"):
        for t in TOOLS:
            if os.path.isdir(os.path.join(BASE, t["dir"])):
                print(("시작" if sys.argv[1] == "start-all" else "중지"), t["dir"], t["port"]); (start if sys.argv[1] == "start-all" else stop)(t)
        sys.exit(0)
    print(f"portal → http://localhost:{PORT}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
