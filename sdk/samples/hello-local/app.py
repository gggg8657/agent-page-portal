"""hello-local — 개발자 업로드 샘플 도구(표준 라이브러리만). 포털 형식 안내(FORMAT.md)의 최소 예.

  - 포트·주소: PORT(포털이 배정) · BIND(기본 127.0.0.1 — 격리 컨테이너에서는 포털이 0.0.0.0 을 넘김)
  - 포털 서명: portal_auth.install(H, "hello-local") — 포털을 거친 요청만 받고, /healthz 는 누구나
  - 사용자별 저장: WORKSPACE/u/<사용자>/memo.json (owner·단독 실행은 WORKSPACE 바로 아래)
  - LLM: LLM_BASE_URL(OpenAI 호환, 격리 컨테이너에서는 http://127.0.0.1:11440/v1 중계) · LLM_MODEL
  화면은 상대 주소(api/…)만 씀 — 포털이 /t/hello-local/ 아래로 붙이므로."""
import json, os, re, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import portal_auth

PORT = int(os.environ.get("PORT", "8810"))
BIND = os.environ.get("BIND", "127.0.0.1")
WS = os.environ.get("WORKSPACE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "_workspace")
PAGE = """<!doctype html><meta charset="utf-8"><title>hello-local</title>
<style>body{font:15px/1.6 system-ui,sans-serif;max-width:640px;margin:32px auto;padding:0 16px}textarea{width:100%;min-height:90px}
button{margin:8px 8px 0 0;padding:6px 14px}pre{background:#f4f4f5;padding:10px;white-space:pre-wrap}</style>
<h1>hello-local</h1><p>나: <b id="who">…</b> · 저장 횟수 <b id="n">0</b></p>
<textarea id="memo" placeholder="내 메모(내 계정 폴더에만 저장)"></textarea><br>
<button id="save">메모 저장</button><button id="ask">LLM 에게 한 줄 인사 받기</button><pre id="out"></pre>
<script>
const $ = (i) => document.getElementById(i);
async function call(path, body) {
  const r = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  const j = await r.json(); if (!r.ok) throw new Error(j.error || r.status); return j;
}
call("api/memo").then((j) => { $("who").textContent = j.user; $("n").textContent = j.count; $("memo").value = j.memo; });
$("save").onclick = () => call("api/memo", {memo: $("memo").value}).then((j) => { $("n").textContent = j.count; $("out").textContent = "저장했습니다"; }, (e) => $("out").textContent = e);
$("ask").onclick = () => { $("out").textContent = "…"; call("api/hello", {}).then((j) => $("out").textContent = j.text, (e) => $("out").textContent = e); };
</script>"""


def user_ws(h):
    """포털이 넘긴 사용자별 작업 폴더. owner(서버 주인)·헤더 없음(단독 실행) = WORKSPACE 그대로"""
    u = portal_auth.user(h)
    d = WS if u == "owner" else os.path.join(WS, "u", re.sub(r"[^A-Za-z0-9_.-]", "_", u)[:64] or "_")
    os.makedirs(d, exist_ok=True)
    return d, u


def load(d):
    try:
        with open(os.path.join(d, "memo.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"memo": "", "count": 0}


def llm(prompt):
    base = (os.environ.get("LLM_BASE_URL") or "http://127.0.0.1:11440/v1").rstrip("/")
    body = {"model": os.environ.get("LLM_MODEL", "gemma4:31b"), "max_tokens": 80, "messages": [{"role": "user", "content": prompt}]}
    rq = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj, ctype="application/json; charset=utf-8"):
        b = obj.encode() if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self.send(200, PAGE, "text/html; charset=utf-8")
        if p == "/api/memo":
            d, u = user_ws(self)
            return self.send(200, {"user": u, **load(d)})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(min(n, 65536)) or b"{}") if n else {}
        except ValueError:
            return self.send(400, {"error": "JSON 이 아닙니다"})
        if p == "/api/memo":
            d, u = user_ws(self)
            m = load(d)
            m = {"memo": str(req.get("memo") or "")[:2000], "count": int(m.get("count") or 0) + 1}
            with open(os.path.join(d, "memo.json"), "w", encoding="utf-8") as f:
                json.dump(m, f, ensure_ascii=False)
            return self.send(200, {"user": u, **m})
        if p == "/api/hello":
            try:
                return self.send(200, {"text": llm("한국어로 한 줄 인사를 해 주세요.")})
            except Exception as e:
                return self.send(502, {"error": "LLM 을 부르지 못했습니다: " + portal_auth.scrub(type(e).__name__)})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    ThreadingHTTPServer((BIND, PORT), portal_auth.install(H, "hello-local")).serve_forever()
