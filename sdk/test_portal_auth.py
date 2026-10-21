"""portal_auth 자가검증(서버 하나를 임시 포트에 띄워 실제 HTTP 로).  python3 sdk/test_portal_auth.py"""
import io, json, os, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portal_auth as pa


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"user": self.headers.get("X-Agent-User") or "owner"} | ({"role": pa.role(self), "admin": pa.is_admin(self)} if self.path == "/r" else {})).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    do_POST = do_GET


srv = ThreadingHTTPServer(("127.0.0.1", 0), pa.install(H, "test-local"))
pa.install(H, "test-local")   # 두 번 붙여도 한 번
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"


def get(path, headers=None, method="GET"):
    req = urllib.request.Request(URL + path, headers=headers or {}, method=method, data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# 1) 단독 모드: 127.0.0.1 은 그대로(헤더 없으면 owner)
os.environ.pop("PORTAL_TOOL_KEY", None)
assert get("/x") == (200, {"user": "owner"})
assert get("/x", {"X-Agent-User": "kim"}) == (200, {"user": "kim"})
c, j = get("/healthz")
assert c == 200 and j["ok"] and j["tool"] == "test-local" and j["auth"] == "standalone", j
assert get("/r") == (200, {"user": "owner", "role": "", "admin": True})                       # 단독 실행 = 주인
assert get("/r", {"X-Agent-User": "kim", "X-Agent-Role": "admin"}) == (200, {"user": "kim", "role": "", "admin": False})   # 확인할 키 없음 → 역할 지움

# 2) 서명 모드
os.environ["PORTAL_TOOL_KEY"] = "k" * 43
assert get("/x")[0] == 401                                            # 헤더 없음
assert get("/x", {"X-Agent-User": "owner"})[0] == 401                 # 사용자만(위장)
assert get("/x", pa.sign("kim", "test-local")) == (200, {"user": "kim"})            # 서명 맞음
assert get("/x", pa.sign("kim", "test-local"), method="POST") == (200, {"user": "kim"})
bad = pa.sign("kim", "test-local"); bad["X-Agent-User"] = "owner"
assert get("/x", bad)[0] == 401                                       # 서명은 kim, 사용자 바꿔치기
old = pa.sign("kim", "test-local", now=time.time() - 600)
assert get("/x", old)[0] == 401                                       # 10분 전 서명
assert get("/x", {"X-Agent-User": "kim", "X-Agent-Auth": "v1.x.y"})[0] == 401
assert get("/x", {"X-Agent-Auth": pa.sign("kim", "test-local")["X-Agent-Auth"]})[0] == 401   # 사용자 헤더 없이
assert pa.sign("", "test-local") == {}
assert get("/x", pa.sign("kim", "other-local"))[0] == 401   # 다른 도구 앞으로 만든 서명은 이 도구에서 안 통함(로그에 남은 서명 재사용 막기)
v1 = pa.sign("kim", "test-local"); ts = v1["X-Agent-Auth"].split(".")[1]   # 예전 v1 형식은 받지 않음
v1["X-Agent-Auth"] = "v1.%s.%s" % (ts, __import__("hmac").new(("k" * 43).encode(), f"v1|{ts}|kim".encode(), "sha256").hexdigest())
assert pa.verify("kim", v1["X-Agent-Auth"], "test-local", now=int(ts)) == (time.strftime("%Y-%m-%d", time.gmtime(int(ts))) < pa.V1_UNTIL)   # 옮기는 동안만
_u = pa.V1_UNTIL; pa.V1_UNTIL = "2000-01-01"
assert get("/x", v1)[0] == 401                                        # 날짜가 지나면 v1 거부
pa.V1_UNTIL = _u                                              # 사용자 없으면 서명 안 함
# 역할(관리자 계정) 서명 — 사용자·역할·받는 도구를 함께 서명(r2)
T = "test-local"
assert pa.sign("kim", T, role="admin")["X-Agent-Role"] == "admin" and pa.sign("kim", T, role="admin")["X-Agent-Role-Auth"].startswith("r2.")
assert get("/r", pa.sign("kim", T, role="admin")) == (200, {"user": "kim", "role": "admin", "admin": True})
assert get("/r", pa.sign("kim", T)) == (200, {"user": "kim", "role": "", "admin": False})
assert get("/r", {**pa.sign("kim", T), "X-Agent-Role": "admin"}) == (200, {"user": "kim", "role": "", "admin": False})   # 역할 서명 없이 붙임 → 지움
lee = pa.sign("lee", T, role="admin")
assert get("/r", {**pa.sign("kim", T), "X-Agent-Role": "admin", "X-Agent-Role-Auth": lee["X-Agent-Role-Auth"]})[1]["admin"] is False   # 남의 역할 서명
oth = pa.sign("kim", "other-local", role="admin")
assert get("/r", {**pa.sign("kim", T), "X-Agent-Role": "admin", "X-Agent-Role-Auth": oth["X-Agent-Role-Auth"]})[1]["admin"] is False   # 다른 도구 앞 역할 서명
up = pa.sign("kim", T, role="user"); up["X-Agent-Role"] = "admin"
assert get("/r", up)[1]["admin"] is False                                                         # 역할 바꿔치기
stale = pa.sign("kim", T, now=time.time() - 600, role="admin"); stale["X-Agent-Auth"] = pa.sign("kim", T)["X-Agent-Auth"]
assert get("/r", stale)[1]["admin"] is False                                                      # 오래된 역할 서명
v1r = {**v1, **{k: v for k, v in pa.sign("kim", T, now=int(ts), role="admin").items() if k.startswith("X-Agent-Role")}}
assert get("/r", v1r)[1]["admin"] is (time.strftime("%Y-%m-%d") < pa.V1_UNTIL and abs(time.time() - int(ts)) < pa.SKEW)   # 옮기는 동안: v1 사용자 서명 + r2 역할도 됨
assert get("/r", pa.sign("owner", T)) == (200, {"user": "owner", "role": "", "admin": True})     # 로컬 주인 토큰
assert not pa.verify_role("kim", "admin", None, T) and not pa.verify_role("kim", "admin", "v2.1.x", T)
c, j = get("/healthz")
assert c == 200 and j["auth"] == "signed", j                           # 헬스는 서명 없이
os.environ["PORTAL_TOOL_KEY"] = "other-key-xxxxxxxxxxxxxxxxx"
assert get("/x", {"X-Agent-User": "kim", "X-Agent-Auth": old["X-Agent-Auth"]})[0] == 401   # 다른 키

# 3) 바깥 주소 판정(소켓 없이 check 직접)
class Fake:
    def __init__(self, ip, headers=None):
        self.client_address, self.path, self.command = (ip, 1), "/x", "GET"
        self.headers, self.wfile, self.code = headers or {}, io.BytesIO(), None
    def send_response(self, c): self.code = c
    def send_header(self, *a): pass
    def end_headers(self): pass

os.environ.pop("PORTAL_TOOL_KEY", None)
f = Fake("10.0.0.5"); assert not pa.check(f, "t") and f.code == 403   # 단독 모드: 바깥은 막음
f = Fake("::1"); assert pa.check(f, "t")
os.environ["PORTAL_AUTH_ALLOW_UNSIGNED"] = "172.17.0.0/16"
f = Fake("172.17.0.9"); assert pa.check(f, "t")                       # 옮기는 동안 허용한 대역
f = Fake("172.18.0.9"); assert not pa.check(f, "t") and f.code == 403
os.environ["PORTAL_TOOL_KEY"] = "k" * 43
f = Fake("172.17.0.9"); assert pa.check(f, "t")                       # 서명 모드에서도 허용 대역은 서명 없이
f = Fake("172.17.0.9", {"X-Agent-User": "kim", "X-Agent-Role": "admin"}); assert pa.check(f, "t") and not pa.is_admin(f)   # 서명 없는 역할은 지움
f = Fake("172.17.0.9", {"X-Agent-Auth": "v2.1.bad", "X-Agent-User": "kim"}); assert not pa.check(f, "t") and f.code == 401   # 틀린 서명은 허용 대역이어도 거부
f = Fake("10.0.0.5", pa.sign("kim", "t")); assert pa.check(f, "t")         # 서명 맞으면 어디서든
os.environ.pop("PORTAL_AUTH_ALLOW_UNSIGNED")
# 4) 오류 문장 정리
assert pa.scrub("No such file: '/home/u/agent-page/x.json'") == "No such file: '<경로>'"
assert pa.scrub("refused http://127.0.0.1:8771/v1") == "refused <내부주소>/v1"
assert pa.scrub("값은 1/2 (a/b)") == "값은 1/2 (a/b)"   # 경로가 아닌 빗금은 그대로
srv.shutdown()
print("portal_auth selftest OK")
