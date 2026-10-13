#!/usr/bin/env python3
"""계약 시험(실제 포털 둘): 같은 요청을 지금 포털(:8700, portal.py)과 새 포털(:8720, fastportal)에 보내 응답을 비교한다.
  python3 fastportal/tests/contract_live.py --demo <id>:<pin> [--old 8700] [--new 8720] [--json out.json]
비교: 상태 코드 · 헤더(Content-Type · Cache-Control · ETag · Location · Set-Cookie 속성 · X-Author · X-Content-Type-Options) ·
     본문(JSON 은 변하는 값 가림, HTML·글은 그대로). 날짜·Server·Connection·Transfer-Encoding 같은 전송 헤더는 비교 안 함.
안전: 읽기(GET)와 되돌릴 수 있는 POST 만 — 데모 계정 로그인·로그아웃, 테마 저장 후 원래대로, onboarded 후 원래대로,
     틀린 PIN 은 포털마다 1번(잠금 5번에 안 닿음), 권한 거절(403)은 권한 확인이 먼저인 호출만. 도구 시작·중지·등록·글쓰기 없음.
사람 구분: 127.0.0.1 쿠키 없음 = 서버 주인(새 포털엔 _data/.local_owner_token 을 X-Local-Owner 로 붙여서 — 값은 출력 안 함), 이 서버의 바깥 주소(hostname -I 첫 값)로 붙으면 '밖에서 온 손님'."""
import argparse, http.client, json, re, sqlite3, subprocess, sys, time, os

ap = argparse.ArgumentParser()
ap.add_argument("--old", type=int, default=8700)
ap.add_argument("--new", type=int, default=8720)
ap.add_argument("--demo", required=True, help="id:pin (데모 계정)")
ap.add_argument("--json", help="결과 표를 JSON 으로")
ap.add_argument("--token-file", help="새 포털의 로컬 주인 토큰 파일(기본 <db 폴더>/.local_owner_token) — 'local' 요청에 X-Local-Owner 로 붙임(새 포털에만)")
ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "..", "..", "..", "_data", "portal.db"))
A = ap.parse_args()
DEMO_ID, DEMO_PIN = A.demo.split(":", 1)
TOKF = A.token_file or os.path.join(os.path.dirname(os.path.abspath(A.db)), ".local_owner_token")
LOCAL_TOK = open(TOKF).read().strip() if os.path.exists(TOKF) else ""   # 값은 출력하지 않음
EXT = subprocess.run(["hostname", "-I"], capture_output=True, text=True).stdout.split()[0]   # 밖에서 온 것처럼 보이는 주소(코드에 적지 않음)

VOLATILE = {"seen", "t", "exp", "used", "util", "procs", "recent", "total", "users", "likes", "n", "last"}   # 시각·GPU·사용 횟수(시험 중에도 바뀜)
CMP_HEADERS = ("content-type", "cache-control", "etag", "last-modified", "location", "x-author", "x-content-type-options", "content-disposition")


def req(port, method, path, host="127.0.0.1", headers=None, body=None):
    for attempt in range(30):   # 운영 포털이 잠깐 재시작 중이면 기다렸다 다시
        try:
            c = http.client.HTTPConnection(host, port, timeout=60)
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            data = r.read()
            c.close()
            return r.status, [(k.lower(), v) for k, v in r.getheaders()], data
        except (ConnectionRefusedError, ConnectionResetError):
            time.sleep(2)
    raise SystemExit(f"{port} 에 붙지 못함")


def mask(o):
    if isinstance(o, dict):
        return {k: ("<v>" if k in VOLATILE else mask(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [mask(x) for x in o]
    return o


def norm_cookie(v):
    return re.sub(r"agsid=[A-Za-z0-9_-]{20,}", "agsid=<tok>", v)


def norm_body(ctype, b):
    if "json" in ctype:
        try:
            return mask(json.loads(b))
        except ValueError:
            pass
    s = b.decode("utf-8", "replace") if ("text" in ctype or "javascript" in ctype or "json" in ctype) else b
    if isinstance(s, str):
        s = re.sub(r"agsid=[A-Za-z0-9_-]{20,}", "agsid=<tok>", s)
    return s


def view(st, hd, b):
    h = {}
    for k, v in hd:
        if k in CMP_HEADERS:
            h[k] = v
        if k == "set-cookie":
            h.setdefault("set-cookie", []).append(norm_cookie(v))
    return {"status": st, "headers": h, "body": norm_body(h.get("content-type", ""), b)}


RESULTS = []


def same(name, method, path, who="demo", headers=None, body=None, note="", expect=None):
    hd = dict(headers or {})
    host = EXT if who == "anon" else "127.0.0.1"
    if who == "demo":
        hd["Cookie"] = (hd.get("Cookie", "") + "; " if hd.get("Cookie") else "") + "agsid=" + DEMO_TOK
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode(); hd.setdefault("Content-Type", "application/json")
    hd_new = dict(hd, **{"X-Local-Owner": LOCAL_TOK}) if who == "local" and LOCAL_TOK else hd   # 새 포털(PORTAL_LOCAL_OWNER=token)은 헤더가 있어야 로컬 주인
    a = view(*req(A.old, method, path, host, hd, body))
    b = view(*req(A.new, method, path, host, hd_new, body))
    ok = a == b and (expect is None or a["status"] == expect)
    diff = "" if ok else describe(a, b, expect)
    RESULTS.append({"name": name, "req": f"{method} {path}", "who": who, "status": f"{a['status']}/{b['status']}", "ok": ok, "diff": diff, "note": note})
    print(("  OK  " if ok else "  !!  ") + f"{name:38} {method} {path[:60]:60} {who:5} {a['status']}/{b['status']}" + (f"\n        {diff}" if diff else ""))
    return a, b


def describe(a, b, expect):
    out = []
    if expect is not None and a["status"] != expect:
        out.append(f"기대 {expect}")
    if a["status"] != b["status"]:
        out.append(f"status {a['status']}≠{b['status']}")
    for k in sorted(set(a["headers"]) | set(b["headers"])):
        if a["headers"].get(k) != b["headers"].get(k):
            out.append(f"header {k}: {a['headers'].get(k)!r} ≠ {b['headers'].get(k)!r}")
    if a["body"] != b["body"]:
        x, y = a["body"], b["body"]
        if isinstance(x, str) and isinstance(y, str):
            i = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]), min(len(x), len(y)))
            out.append(f"body @{i}: {x[max(0, i - 60):i + 80]!r} ≠ {y[max(0, i - 60):i + 80]!r}")
        else:
            out.append(f"body: {str(x)[:300]} ≠ {str(y)[:300]}")
    return " | ".join(out)


def login(port, remember=True):
    st, hd, b = req(port, "POST", "/api/login", headers={"Content-Type": "application/json"},
                    body=json.dumps({"id": DEMO_ID, "pin": DEMO_PIN, "remember": remember}).encode())
    ck = next(v for k, v in hd if k == "set-cookie")
    return re.search(r"agsid=([A-Za-z0-9_-]+)", ck)[1], st, ck


def demo_row():
    con = sqlite3.connect(f"file:{os.path.abspath(A.db)}?mode=ro", uri=True)
    r = con.execute("SELECT theme, onboarded, dept, name FROM users WHERE id=?", (DEMO_ID,)).fetchone()
    con.close()
    return r


orig = demo_row()
print("demo 원래 값(theme, onboarded):", orig[:2])

print("\n## 로그인 전 · 손님(바깥 주소)")
same("틀린 PIN(1번씩)", "POST", "/api/login", "anon", body={"id": DEMO_ID, "pin": "000000"})
same("없는 아이디", "POST", "/api/login", "anon", body={"id": "no-such-user-x", "pin": "1234"})
same("JSON 아님", "POST", "/api/login", "anon", body=b"[1,2]", headers={"Content-Type": "application/json"})
same("깨진 JSON", "POST", "/api/login", "anon", body=b"{x", headers={"Content-Type": "application/json"})
same("가입 규칙 위반(아이디)", "POST", "/api/signup", "anon", body={"id": "A", "name": "x", "pin": "1234"})
same("가입 규칙 위반(주인 아이디)", "POST", "/api/signup", "anon", body={"id": "owner", "name": "x", "pin": "1234"})
same("가입 규칙 위반(PIN)", "POST", "/api/signup", "anon", body={"id": "zz-contract-test", "name": "x", "pin": "12"})
same("가입 규칙 위반(이미 있음)", "POST", "/api/signup", "anon", body={"id": DEMO_ID, "name": "x", "pin": "1234"})
for th in ("", "classic-light", "v2-dark", "trendy-auto", "classic-dark", "bogus"):
    same(f"랜딩 /login 테마={th or '없음'}", "GET", "/login", "anon", headers={"Cookie": f"agtheme={th}"} if th else {})
same("랜딩 /login?next=…", "GET", "/login?next=/account", "anon")
same("/api/depts", "GET", "/api/depts", "anon")
same("로그인 필요 API(401)", "GET", "/api/me", "anon")
same("로그인 필요 화면(302 next)", "GET", "/account?x=1&y=/t/a", "anon", headers={"Accept": "text/html"})
same("로그인 필요 홈(302)", "GET", "/", "anon", headers={"Accept": "text/html,application/xhtml+xml"})
same("로그인 필요 POST(401)", "POST", "/api/open", "anon", body={"dir": "kordoc-local"})
same("로그인 필요 프록시(302)", "GET", "/t/kordoc-local/", "anon", headers={"Accept": "text/html"})
same("무효 쿠키(로컬이어도 로그인)", "GET", "/api/me", "local", headers={"Cookie": "agsid=invalidtokenxxxxxxxxxxxxxxxxxxxxx"})
same("랜딩 그림", "GET", "/landing/home.png", "anon")
same("테마 CSS", "GET", "/theme/tokens.css?v=1", "anon")
same("테마 JS", "GET", "/theme/picker.js", "anon")
same("테마 CSS 304", "GET", "/theme/tokens.css", "anon", headers={"If-None-Match": view(*req(A.old, "GET", "/theme/tokens.css"))["headers"]["etag"]})
same("랜딩 그림 304", "GET", "/landing/home.png", "anon", headers={"If-None-Match": view(*req(A.old, "GET", "/landing/home.png"))["headers"]["etag"]})
same("테마 boot.js 막음", "GET", "/theme/boot.js", "anon")
same("테마 경로 탈출", "GET", "/theme/..%2fportal.py", "anon")
same("테마 도구 CSS", "GET", "/theme/tools/kordoc-local.css", "anon")
same("테마 글꼴 없는 것", "GET", "/theme/fonts/none.woff2", "anon")
same("로그아웃(쿠키 없음)", "GET", "/logout", "anon")
same("지원 안 하는 메서드 PUT", "PUT", "/api/me", "anon", body=b"")
same("지원 안 하는 메서드 DELETE", "DELETE", "/", "anon")

print("\n## 데모 계정 로그인")
tok_old, st1, ck_old = login(A.old)
tok_new, st2, ck_new = login(A.new)
same_cookie = norm_cookie(ck_old) == norm_cookie(ck_new)
RESULTS.append({"name": "로그인 성공 Set-Cookie(유지 켬)", "req": "POST /api/login", "who": "anon", "status": f"{st1}/{st2}", "ok": same_cookie and st1 == st2 == 200,
                "diff": "" if same_cookie else f"{norm_cookie(ck_old)} ≠ {norm_cookie(ck_new)}", "note": "30일 Max-Age"})
print(f"  {'OK' if same_cookie else '!!'}  로그인 성공 {st1}/{st2} {norm_cookie(ck_new)}")
t2o, s1, c1 = login(A.old, remember=False)
t2n, s2, c2 = login(A.new, remember=False)
ok = norm_cookie(c1) == norm_cookie(c2) and "Max-Age" not in c2
RESULTS.append({"name": "로그인 성공 Set-Cookie(유지 끔)", "req": "POST /api/login remember=false", "who": "anon", "status": f"{s1}/{s2}", "ok": ok, "diff": "" if ok else f"{c1} ≠ {c2}", "note": "세션 쿠키"})
print(f"  {'OK' if ok else '!!'}  로그인(유지 끔) {norm_cookie(c2)}")
con = sqlite3.connect(f"file:{os.path.abspath(A.db)}?mode=ro", uri=True)
import hashlib
shorts = [bool(con.execute("SELECT 1 FROM sess_short WHERE token=?", (hashlib.sha256(t.encode()).hexdigest(),)).fetchone()) for t in (t2o, t2n, tok_old, tok_new)]
con.close()
ok = shorts == [True, True, False, False]
RESULTS.append({"name": "sess_short 기록(같은 DB)", "req": "-", "who": "-", "status": "-", "ok": ok, "diff": "" if ok else str(shorts), "note": "유지 끔 세션만"})
print(f"  {'OK' if ok else '!!'}  sess_short {shorts}")
DEMO_TOK = tok_old   # 같은 DB 를 쓰므로 한 쿠키로 두 포털 모두 — 같은 상태에서 비교

print("\n## 로그인 뒤(데모 계정)")
same("/api/me", "GET", "/api/me")
same("/api/account", "GET", "/api/account")
same("/api/account/theme", "GET", "/api/account/theme")
same("/api/account/notices", "GET", "/api/account/notices", note="읽으면 notified 표시(데모 계정)")
same("/api/approvals 거절(403)", "GET", "/api/approvals", expect=403)
same("/api/admin 거절(403)", "GET", "/api/admin", expect=403)
same("/api/account/없음", "GET", "/api/account/nothing")
same("/api/tools", "GET", "/api/tools")
same("/api/gpu", "GET", "/api/gpu")
same("/api/events", "GET", "/api/events")
same("/api/events?since=", "GET", "/api/events?since=99999999999")
same("/api/guide/portal", "GET", "/api/guide/portal")
same("/api/guide/kordoc-local", "GET", "/api/guide/kordoc-local")
same("/api/guide/없는 도구", "GET", "/api/guide/nope")
same("/api/readme/kordoc-local", "GET", "/api/readme/kordoc-local")
same("/api/readme/없는 도구", "GET", "/api/readme/nope")
same("없는 주소 404", "GET", "/api/nothing")
same("/guide → /guide/", "GET", "/guide")
same("/guide/ 설명서", "GET", "/guide/")
same("/guide 그림 없는 것", "GET", "/guide/img/none.png")
same("/guide 경로 탈출", "GET", "/guide/..%2f..%2fportal.py")
same("/guide/fonts/OFL.txt", "GET", "/guide/fonts/OFL.txt")
same("홈 /", "GET", "/")
same("/account", "GET", "/account")
same("/approvals → /account(권한 없음)", "GET", "/approvals")
same("/admin → /account(권한 없음)", "GET", "/admin")
same("/login 이미 로그인 → /", "GET", "/login")
for nx in ("/account", "/t/kordoc-local/?x=1&y=2", "//evil.example", "/\\evil.example", "http://evil.example", "/%0d%0aSet-Cookie:%20x=1",
           "/%09/evil.example", "%2F%2Fevil.example", "/ok%20path", "javascript:alert(1)", "/%ED%95%9C%EA%B8%80", "/%5Cevil.example", "/a%00b"):
    same(f"next 리디렉트 {nx[:20]}", "GET", "/login?next=" + nx)
same("POST JSON 아님", "POST", "/api/open", body=b"[]", headers={"Content-Type": "application/json"})
same("POST 없는 도구", "POST", "/api/open", body={"dir": "nope"})
same("POST 없는 주소(도구 있음)", "POST", "/api/nothing", body={"dir": "kordoc-local"})
same("중지 거절(403)", "POST", "/api/stop", body={"dir": "kordoc-local"}, expect=403)
same("앱 등록 거절(403)", "POST", "/api/tool_add", body={"dir": "zz-x", "port": 65000}, expect=403)
same("관리 거절(403)", "POST", "/api/admin/dept", body={"op": "add", "name": "x"}, expect=403)
same("관리 없는 주소(403 먼저)", "POST", "/api/admin/none", body={}, expect=403)
same("승인 없는 요청", "POST", "/api/approvals/decide", body={"id": 0, "ok": True})
same("요청 취소 없는 것", "POST", "/api/account/cancel", body={"id": 0})
same("없는 테마 저장", "POST", "/api/account/theme", body={"theme": "bogus"})
same("이름 빈 값", "POST", "/api/account/name", body={"name": " "})
same("PIN 바꾸기 새 PIN 규칙", "POST", "/api/pin", body={"pin": DEMO_PIN, "new": "12"}, note="지금 PIN 맞고 새 PIN 틀림 → 바뀌지 않음")
same("/api/account/없음 POST", "POST", "/api/account/nothing", body={})

print("\n## 테마 저장·되돌리기(데모 계정)")
for th in ("v2-dark", "trendy-light", "classic-light"):
    same(f"테마 저장 {th}", "POST", "/api/account/theme", body={"theme": th})
    same(f"  홈 / ({th})", "GET", "/")
    same(f"  /account ({th})", "GET", "/account")
    same(f"  /guide/ ({th})", "GET", "/guide/")
    same(f"  도구 첫 화면 ({th})", "GET", "/t/kordoc-local/")
same("onboarded 끔", "POST", "/api/account/onboarded", body={"done": False})
same("  /api/me (onboarded 끔)", "GET", "/api/me")
same("onboarded 켬", "POST", "/api/account/onboarded", body={"done": True})

print("\n## 서버 주인(127.0.0.1, 쿠키 없음)")
same("/api/me 주인", "GET", "/api/me", "local")
same("/api/admin 주인", "GET", "/api/admin", "local")
same("/api/approvals 주인", "GET", "/api/approvals", "local")
same("/admin 주인", "GET", "/admin", "local")
same("/login 주인(접속 중 표시)", "GET", "/login", "local")
for th in ("classic-light", "v2-dark", "v2-light", "trendy-dark"):
    same(f"홈 주인 agtheme={th}", "GET", "/", "local", headers={"Cookie": f"agtheme={th}"})
    same(f"도구 첫 화면 주인 {th}", "GET", "/t/saju-local/", "local", headers={"Cookie": f"agtheme={th}"})
same("주인 테마 저장(계정 없음 → 쿠키만)", "POST", "/api/account/theme", "local", body={"theme": "v2-dark"})
same("주인 계정 API 거절", "POST", "/api/account/name", "local", body={"name": "x"})
same("주인 PIN 거절", "POST", "/api/pin", "local", body={"pin": "1", "new": "1234"})

print("\n## 로컬 주인 토큰(새 포털만 — 의도한 차이: 예전 portal.py 는 127.0.0.1 쿠키 없음 = 주인)")


def new_only(name, method, path, headers, expect):
    st, _, _ = req(A.new, method, path, "127.0.0.1", headers)
    ok = st == expect
    RESULTS.append({"name": name, "req": f"{method} {path}", "who": "local", "status": f"-/{st}", "ok": ok, "diff": "" if ok else f"기대 {expect}", "note": "의도한 차이"})
    print(("  OK  " if ok else "  !!  ") + f"{name:38} {method} {path[:60]:60} local -/{st}")


new_only("헤더 없는 로컬 /api/me = 401", "GET", "/api/me", {}, 401)
new_only("헤더 없는 로컬 /api/admin = 401", "GET", "/api/admin", {}, 401)
new_only("틀린 토큰 로컬 /api/me = 401", "GET", "/api/me", {"X-Local-Owner": "wrong-token-xxxxxxxxxxxxxxxx"}, 401)
new_only("헤더 없는 로컬 / → 로그인으로", "GET", "/", {"Accept": "text/html"}, 302)
if LOCAL_TOK:
    new_only("토큰 맞는 로컬 /api/me = 200", "GET", "/api/me", {"X-Local-Owner": LOCAL_TOK}, 200)

print("\n## 프록시 /t/<도구>/ (GET 만)")
same("/t/kordoc-local → 302", "GET", "/t/kordoc-local")
same("/t/없는 도구/", "GET", "/t/nope/")
for path in ("/t/kordoc-local/", "/t/kordoc-local/index.html", "/t/kordoc-local/?job=abc", "/t/saju-local/", "/t/padong-local/?pet=1",
             "/t/writer-local/api/polish_ok", "/t/notebook-local/api/notebooks", "/t/award-local/api/health", "/t/award-local/api/meta",
             "/t/protein-local/api/health", "/t/battery-local/api/health", "/t/agent-cad-local/static/OrbitControls.js", "/t/agent-cad-local/api/library",
             "/t/kordoc-local/no/such/file", "/t/meeting-local/api/runs", "/t/tts-local/", "/t/weekly-local/"):
    same(f"프록시 {path[3:30]}", "GET", path)
same("프록시 사용자 헤더 위조 무시", "GET", "/t/award-local/api/meta", headers={"X-Agent-User": "owner", "X-User": "owner", "X-Forwarded-User": "owner"})
same("프록시 agtheme 쿠키 + 도구 쿠키", "GET", "/t/kordoc-local/", headers={"Cookie": "agtheme=v2-dark; toolck=1"})

print("\n## 로그아웃")
st_a = view(*req(A.old, "GET", "/logout", headers={"Cookie": f"agsid={t2o}"}))
st_b = view(*req(A.new, "GET", "/logout", headers={"Cookie": f"agsid={t2n}"}))
ok = st_a == st_b
RESULTS.append({"name": "로그아웃(세션 삭제·쿠키 지움)", "req": "GET /logout", "who": "demo", "status": f"{st_a['status']}/{st_b['status']}", "ok": ok, "diff": "" if ok else describe(st_a, st_b, None), "note": ""})
print(f"  {'OK' if ok else '!!'}  로그아웃 {st_a['headers'].get('set-cookie')}")
for t in (tok_new,):
    req(A.new, "GET", "/logout", headers={"Cookie": f"agsid={t}"})
same("로그아웃 뒤 그 쿠키 = 401(로컬이어도)", "GET", "/api/me", "local", headers={"Cookie": f"agsid={t2o}"})
req(A.old, "GET", "/logout", headers={"Cookie": f"agsid={tok_old}"})

# 원래대로
now_row = demo_row()
if now_row[:2] != orig[:2]:
    con = sqlite3.connect(os.path.abspath(A.db), timeout=10)
    with con:
        con.execute("UPDATE users SET theme=?, onboarded=? WHERE id=?", (orig[0], orig[1], DEMO_ID))
    con.close()
print("demo 되돌림:", demo_row()[:2], "(원래", orig[:2], ")")

n_ok = sum(r["ok"] for r in RESULTS)
print(f"\n== 계약 시험: {len(RESULTS)}건 중 일치 {n_ok}, 불일치 {len(RESULTS) - n_ok}")
if A.json:
    json.dump(RESULTS, open(A.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
sys.exit(0 if n_ok == len(RESULTS) else 1)
