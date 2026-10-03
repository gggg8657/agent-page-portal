#!/usr/bin/env python3
"""에이전트 페이지 포털 — 도구 목록·상태·바로가기·시작/중지. stdlib만.  python3 portal.py  → http://localhost:8700
도구 목록은 tools.json. 각 도구는 자기 폴더의 setup.sh 로 PORT 를 넘겨 기동한다(setup.sh 없으면 app.py 직접)."""
import datetime, json, os, socket, subprocess, sys
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


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/api/tools":
            rows = stats()
            return self._send([{**t, "up": alive(t["port"]), "installed": os.path.isdir(os.path.join(BASE, t["dir"])),
                                "url": f"http://{HOST}:{t['port']}", "usage": usage(t["dir"], rows),
                                "readme": os.path.exists(os.path.join(BASE, t["dir"], "README.md"))} for t in TOOLS])
        if self.path.startswith("/api/readme/"):
            d = self.path.split("/")[-1]
            p = os.path.join(BASE, d, "README.md") if tool(d) else ""
            return self._send((open(p, encoding="utf-8").read() if p and os.path.exists(p) else "README 없음").encode(), "text/plain; charset=utf-8")
        if self.path == "/api/feed":
            try:
                return self._send(json.load(open(os.path.join(ROOT, "feed.json"), encoding="utf-8")))
            except Exception:
                return self._send([])
        self._send(HTML.encode(), "text/html; charset=utf-8")

    def do_POST(self):
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
