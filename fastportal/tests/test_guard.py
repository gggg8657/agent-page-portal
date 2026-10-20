"""F12 코드 수집·엿보기 막기 — 빌드 자산 권한(webguard), 엿보기 탐지·차단(probe), 기존 도구 화면 주석 걷기(strip), 없는 주소 404·noindex.
통합 시험은 임시 데이터 폴더로 포털을 따로 띄움(운영 _data 를 건드리지 않음)."""
import http.client, json, os, re, shutil, socket, subprocess, sys, tempfile, time, unittest

from fastportal import probe, strip, webguard

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VENV = os.environ.get("FASTPORTAL_VENV") or os.path.expanduser("~/.local/fastportal-venv")
TS = os.environ.get("TS_PATH") or os.path.expanduser("~/agent-page/agent-page-web/node_modules/typescript")
TOOLS_BASE = os.environ.get("TOOLS_BASE") or os.path.expanduser("~/agent-page")

MAN = {
    "index.html": {"file": "assets/index-A1.js", "src": "index.html", "isEntry": True, "imports": ["_jsx-B1.js"], "css": ["assets/index-C1.css"],
                   "dynamicImports": ["src/components/Shell.tsx", "src/pages/Admin.tsx", "src/pages/Approvals.tsx", "src/pages/Create.tsx"]},
    "_jsx-B1.js": {"file": "assets/jsx-B1.js"},
    "_Manage-M1.js": {"file": "assets/Manage-M1.js", "imports": ["_jsx-B1.js"]},
    "_adminonly-X1.js": {"file": "assets/adminonly-X1.js"},
    "src/components/Shell.tsx": {"file": "assets/Shell-S1.js", "src": "src/components/Shell.tsx", "isDynamicEntry": True, "imports": ["_jsx-B1.js", "_Manage-M1.js"], "css": ["assets/Shell-S1.css"]},
    "src/pages/Admin.tsx": {"file": "assets/Admin-D1.js", "src": "src/pages/Admin.tsx", "isDynamicEntry": True, "imports": ["_Manage-M1.js", "_adminonly-X1.js"], "css": ["assets/Admin-D1.css"]},
    "src/pages/Approvals.tsx": {"file": "assets/Approvals-P1.js", "src": "src/pages/Approvals.tsx", "isDynamicEntry": True, "imports": ["_Manage-M1.js", "_adminonly-X1.js"]},
    "src/pages/Create.tsx": {"file": "assets/Create-R1.js", "src": "src/pages/Create.tsx", "isDynamicEntry": True, "dynamicImports": ["src/pages/DevTools.tsx"]},
    "src/pages/DevTools.tsx": {"file": "assets/DevTools-V1.js", "src": "src/pages/DevTools.tsx", "isDynamicEntry": True},
}


class WebGuardTest(unittest.TestCase):
    def test_analyze(self):
        pub, roles = webguard.analyze(MAN)
        self.assertEqual(pub, {"assets/index-A1.js", "assets/jsx-B1.js", "assets/index-C1.css"})
        self.assertEqual(roles["assets/Admin-D1.js"], {"admin"})
        self.assertEqual(roles["assets/Admin-D1.css"], {"admin"})
        self.assertEqual(roles["assets/Approvals-P1.js"], {"admin", "head"})
        self.assertEqual(roles["assets/adminonly-X1.js"], {"admin", "head"}, "관리·승인 화면만 부르는 조각은 두 역할의 합")
        self.assertEqual(roles["assets/DevTools-V1.js"], {"admin", "developer"})
        for f in ("assets/Shell-S1.js", "assets/Manage-M1.js", "assets/Create-R1.js"):
            self.assertNotIn(f, roles, "셸이 같이 쓰는 조각·일반 화면은 로그인만")

    def test_policy_old_manifest_and_unknown(self):
        d = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(d, ".vite"))
            open(os.path.join(d, "index.html"), "w").write("<script src='/assets/index-A1.js'></script>")
            json.dump(MAN, open(os.path.join(d, ".vite", "manifest.json"), "w"))
            json.dump({"src/pages/Admin.tsx": {"file": "assets/Admin-OLD.js", "src": "src/pages/Admin.tsx", "isDynamicEntry": True},
                       "index.html": {"file": "assets/index-OLD.js", "isEntry": True}}, open(os.path.join(d, ".vite", "manifest.20261001-000000.json"), "w"))
            p = webguard.policy(d)
            self.assertEqual(p.need("assets/index-A1.js"), "public")
            self.assertEqual(p.need("assets/index-OLD.js"), "login", "옛 빌드의 공개 번들은 로그인만(공개는 지금 빌드 것만)")
            self.assertEqual(p.need("assets/Admin-OLD.js"), {"admin"}, "옛 빌드 관리 번들도 계속 가림")
            self.assertEqual(p.need("assets/unknown-Z.js"), "login", "모르는 파일은 로그인한 계정에 엶(fail open)")
            os.remove(os.path.join(d, ".vite", "manifest.json")); os.remove(os.path.join(d, ".vite", "manifest.20261001-000000.json"))
            p = webguard.policy(d)
            self.assertFalse(p.has_manifest)
            self.assertEqual(p.need("assets/index-A1.js"), "public", "manifest 없는 빌드: index.html 이 부르는 것만 공개")
            self.assertEqual(p.need("assets/Shell-S1.js"), "login")
        finally:
            shutil.rmtree(d)


class ProbeTest(unittest.TestCase):
    def setUp(self):
        probe.clear()
        self.saved = (probe.ENABLED, probe.MAX, probe.WINDOW, probe.BLOCK_S, probe.BLOCK_MAX_S)
        probe.ENABLED, probe.MAX, probe.WINDOW, probe.BLOCK_S, probe.BLOCK_MAX_S = True, 5, 60, 10, 25

    def tearDown(self):
        probe.ENABLED, probe.MAX, probe.WINDOW, probe.BLOCK_S, probe.BLOCK_MAX_S = self.saved
        probe.clear()

    def test_distinct_threshold_and_expiry(self):
        t = 1000.0
        for i in range(4):
            self.assertIsNone(probe.record("192.0.2.9", "", "GET", f"/x{i}", now=t + i))
        hit = probe.record("192.0.2.9", "", "GET", "/x4", now=t + 4)
        self.assertIsNotNone(hit)
        sec, total, distinct, sample = hit
        self.assertEqual((sec, total, distinct), (10, 5, 5))
        self.assertIn("GET /x0", sample)
        self.assertTrue(probe.ip_has_block("192.0.2.9", now=t + 5))
        self.assertEqual(probe.blocked("192.0.2.9", "", now=t + 5), 9)
        self.assertEqual(probe.blocked("192.0.2.9", "alice", now=t + 5), 0, "같은 IP 의 다른 계정은 따로")
        self.assertEqual(probe.blocked("192.0.2.8", "", now=t + 5), 0)
        self.assertEqual(probe.blocked("192.0.2.9", "", now=t + 15), 0, "시간이 지나면 풀림")
        self.assertFalse(probe.ip_has_block("192.0.2.9", now=t + 15))

    def test_repeat_same_path_needs_more(self):
        t = 1000.0
        for i in range(probe.MAX * probe.TOTAL_FACTOR - 1):   # 같은 주소 폴링 404 — 다른 주소 1개
            self.assertIsNone(probe.record("192.0.2.7", "bob", "GET", "/t/x-local/api/job/1", now=t + i * 0.1))
        self.assertIsNotNone(probe.record("192.0.2.7", "bob", "GET", "/t/x-local/api/job/1", now=t + 30), "총 횟수 상한(MAX×5)")

    def test_window_slides(self):
        t = 1000.0
        for i in range(4):
            probe.record("192.0.2.6", "", "GET", f"/y{i}", now=t + i)
        self.assertIsNone(probe.record("192.0.2.6", "", "GET", "/y4", now=t + 100), "창 밖 기록은 빠짐")

    def test_escalation(self):
        t = 1000.0
        secs = []
        for round_ in range(3):
            base = t + round_ * 100
            for i in range(5):
                hit = probe.record("192.0.2.5", "", "GET", f"/z{i}", now=base + i * 0.01)
            secs.append(hit[0])
        self.assertEqual(secs, [10, 20, 25], "다시 걸리면 2배, 상한까지")

    def test_exempt_paths(self):
        for p in ("/favicon.ico", "/robots.txt", "/api/version", "/healthz", "/t/chart-local/healthz", "/.well-known/security.txt"):
            self.assertTrue(probe.exempt_path(p), p)
        for p in ("/.env", "/api/admin", "/t/chart-local/api/meta", "/assets/x.js"):
            self.assertFalse(probe.exempt_path(p), p)

    def test_disabled(self):
        probe.ENABLED = False
        for i in range(20):
            self.assertIsNone(probe.record("192.0.2.4", "", "GET", f"/w{i}"))
        self.assertEqual(probe.blocked("192.0.2.4", ""), 0)


class StripTest(unittest.TestCase):
    def js(self, src):
        return strip.strip_js(src)

    def test_line_comments(self):
        self.assertEqual(self.js("a();\n  // 설명\nb();\n"), "a();\nb();\n")
        self.assertEqual(self.js("a(); // 끝 주석은 남김\n"), "a(); // 끝 주석은 남김\n")
        self.assertEqual(self.js("// © 2026 저작권\nx;\n"), "// © 2026 저작권\nx;\n")

    def test_block_comments(self):
        self.assertEqual(self.js("a;\n/* 여러\n 줄 */\nb;\n"), "a;\nb;\n")
        self.assertEqual(self.js("/** doc */ function f(){}\n"), "function f(){}\n")
        self.assertEqual(self.js("x(/* 가운데 */ 1);\n"), "x(/* 가운데 */ 1);\n")
        out = self.js("return_ = function(){\n  return\n  /* 줄바꿈이 있는\n주석 */ 1;\n}\n")
        self.assertIn("return\n", out)
        self.assertNotIn("주석", out)

    def test_strings_templates_regex_untouched(self):
        cases = [
            "const s = '// not a comment';\n",
            'const s = "/* nope */";\n',
            "const t = `\n// 템플릿 안 줄\n/* 이것도 */\n${a /* 식 안 */ + `안쪽 ${b}`}\n`;\n",
            "const r = /\\/\\/ x/g; const q = /[/*]/;\n",
            "if (a) x = b / c / d;\n",
            "i++ / 2;\n",
            "const re = /`/; const s2 = '`';\n",
            "html = '<!-- 글자 -->';\n",
        ]
        for c in cases:
            self.assertEqual(self.js(c), c, c)

    def test_unsure_returns_original(self):
        bad = "<script>const t = `열린 템플릿\n</script>"
        self.assertEqual(strip.stripped("<html>" + bad + "</html>"), "<html>" + bad + "</html>")
        with self.assertRaises(strip.Unsure):
            strip.strip_js("const s = 'unterminated\n';")

    def test_html(self):
        h = ("<!doctype html>\n<html><head>\n  <!-- 설명 주석 -->\n<!--[if IE]><p>ie</p><![endif]-->\n"
             "<style>\n/* css 주석 */\nbody{content:\"/* 글자 */\"}\n</style>\n"
             "<script>\n// js 주석\nvar a = 1;\n</script>\n<script type=\"application/json\">{\"k\":\"// 그대로\"}</script>\n"
             "<textarea><!-- 글자 그대로 --></textarea>\n<div title=\"a<!--b-->\">x</div><!-- 줄 중간 --><b>y</b>\n</head></html>")
        out = strip.strip_html(h)
        for gone in ("설명 주석", "css 주석", "js 주석", "줄 중간"):
            self.assertNotIn(gone, out)
        for kept in ("<!--[if IE]>", '"/* 글자 */"', '{"k":"// 그대로"}', "<textarea><!-- 글자 그대로 --></textarea>", 'title="a<!--b-->"', "var a = 1;", "<b>y</b>"):
            self.assertIn(kept, out)
        self.assertNotIn("\n  \n", out)

    def test_real_tool_pages_equivalent(self):
        """실제 도구 ui.html 전부: 걷은 뒤 각 <script> 의 AST(TypeScript 파서)가 원본과 같음 — node·typescript 가 있을 때만"""
        node = shutil.which("node")
        tj = os.path.join(TOOLS_BASE, "agent-page-portal", "tools.json")
        if not node or not os.path.isdir(TS) or not os.path.exists(tj):
            self.skipTest("node·typescript·도구 폴더 없음")
        eq = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js_equiv.cjs")
        scr = re.compile(r"<script([^>]*)>(.*?)</script\s*>", re.S | re.I)
        tmp = tempfile.mkdtemp()
        n = 0
        try:
            for t in json.load(open(tj)):
                f = os.path.join(TOOLS_BASE, t["dir"], "ui.html")
                if not os.path.exists(f):
                    continue
                h = open(f, encoding="utf-8").read()
                s = strip.strip_html(h)
                a, b = scr.findall(h), scr.findall(s)
                self.assertEqual(len(a), len(b), t["dir"])
                for k, ((attrs, x), (_, y)) in enumerate(zip(a, b)):
                    if not x.strip() or ("type=" in attrs and not re.search(r"type=[\"']?(module|text/javascript)", attrs)):
                        continue
                    ext = "mjs" if "module" in attrs else "js"
                    pa, pb = os.path.join(tmp, f"{t['dir']}-{k}-a.{ext}"), os.path.join(tmp, f"{t['dir']}-{k}-b.{ext}")
                    open(pa, "w").write(x); open(pb, "w").write(y)
                    r = subprocess.run([node, eq, TS, pa, pb], capture_output=True, text=True, timeout=60)
                    self.assertEqual(r.returncode, 0, f"{t['dir']} script {k}: {r.stdout}{r.stderr}")
                    n += 1
        finally:
            shutil.rmtree(tmp)
        self.assertGreater(n, 10)


# ── 통합: 임시 데이터로 포털을 띄워 HTTP 로 ─────────────────────────────────
def _free():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _dist(root):
    os.makedirs(os.path.join(root, "assets")); os.makedirs(os.path.join(root, ".vite"))
    open(os.path.join(root, "index.html"), "w").write('<!doctype html><html><head><script type="module" src="/assets/index-A1.js"></script></head><body><div id="root"></div></body></html>')
    for e in MAN.values():
        for f in [e["file"]] + e.get("css", []):
            open(os.path.join(root, f), "w").write(f"/* {f} */")
    open(os.path.join(root, "assets", "index-A1.js.map"), "w").write("{}")
    open(os.path.join(root, "theme-boot.js"), "w").write("/*boot*/")
    json.dump(MAN, open(os.path.join(root, ".vite", "manifest.json"), "w"))


class Portal:
    def __init__(self, extra):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "_data")
        dist = os.path.join(self.tmp, "root")
        _dist(dist)
        self.port = _free()
        env = {**os.environ, "AGENT_DATA": self.data, "HOST": "127.0.0.1", "BACKUP": "0", "PORT": str(self.port), "AGENT_OWNERS": "boss",
               "WEB_ROOT": "1", "WEB_ROOT_DIST": dist, "WEB_DIST": "", **extra}
        for k in ("TLS_CERT", "TLS_KEY", "PORTAL_SECRETS_DIR"):
            env.pop(k, None)
        self.log = open(os.path.join(self.tmp, "portal.log"), "w")
        self.proc = subprocess.Popen([os.path.join(VENV, "bin", "python"), "-m", "fastportal"], cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        for _ in range(150):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close(); break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("포털 안 뜸: " + open(os.path.join(self.tmp, "portal.log")).read())
        self.tok = open(os.path.join(self.data, ".local_owner_token")).read().strip()

    def req(self, method, path, headers=None, body=None, owner=False, cookie=None):
        h = dict(headers or {})
        if owner:
            h["X-Local-Owner"] = self.tok
        if cookie:
            h["Cookie"] = cookie
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request(method, path, body=json.dumps(body).encode() if isinstance(body, dict) else body, headers=h)
        r = c.getresponse(); b = r.read(); c.close()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, b

    def login(self, uid, signup=False, owner=False):
        st, hd, b = self.req("POST", "/api/signup" if signup else "/api/login", {"Content-Type": "application/json"},
                             {"id": uid, "pin": "4821", "name": uid}, owner=owner)
        assert st == 200, (st, b)
        return re.search(r"agsid=[^;]+", hd["set-cookie"])[0]

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


@unittest.skipUnless(os.path.exists(os.path.join(VENV, "bin", "python")), "fastportal venv 없음")
class GatedAssetsHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P = Portal({"PORTAL_PROBE": "0"})
        cls.alice = cls.P.login("alice", signup=True)
        cls.boss = cls.P.login("boss", signup=True, owner=True)
        cls.dev = cls.P.login("dave", signup=True)
        st, _, b = cls.P.req("POST", "/api/admin/developer", {"Content-Type": "application/json"}, {"op": "grant", "uid": "dave"}, owner=True)
        assert st == 200, b

    @classmethod
    def tearDownClass(cls):
        cls.P.close()

    def test_matrix(self):
        P = self.P
        rows = [  # 경로, 익명, 일반, 관리자, 개발자
            ("/assets/index-A1.js", 200, 200, 200, 200),
            ("/assets/jsx-B1.js", 200, 200, 200, 200),
            ("/assets/index-C1.css", 200, 200, 200, 200),
            ("/assets/Shell-S1.js", 401, 200, 200, 200),
            ("/assets/Manage-M1.js", 401, 200, 200, 200),
            ("/assets/Create-R1.js", 401, 200, 200, 200),
            ("/assets/Admin-D1.js", 401, 404, 200, 404),
            ("/assets/Admin-D1.css", 401, 404, 200, 404),
            ("/assets/Approvals-P1.js", 401, 404, 200, 404),
            ("/assets/DevTools-V1.js", 401, 404, 200, 200),
            ("/assets/nope-Q9.js", 401, 404, 404, 404),
            ("/assets/index-A1.js.map", 404, 404, 404, 404),
        ]
        for path, *want in rows:
            got = [P.req("GET", path)[0], P.req("GET", path, cookie=self.alice)[0], P.req("GET", path, cookie=self.boss)[0], P.req("GET", path, cookie=self.dev)[0]]
            self.assertEqual(got, want, path)

    def test_cache_headers(self):
        _, h, _ = self.P.req("GET", "/assets/index-A1.js")
        self.assertIn("public", h["cache-control"])
        _, h, _ = self.P.req("GET", "/assets/Shell-S1.js", cookie=self.alice)
        self.assertTrue(h["cache-control"].startswith("private"), h["cache-control"])
        self.assertNotIn("set-cookie", h)
        st, h, _ = self.P.req("GET", "/assets/Shell-S1.js")
        self.assertEqual(h.get("cache-control"), "no-store")

    def test_login_page_and_spa(self):
        st, h, b = self.P.req("GET", "/login", {"Accept": "text/html"})
        self.assertEqual(st, 200)
        self.assertIn(b"/assets/index-A1.js", b)
        st, h, _ = self.P.req("GET", "/", {"Accept": "text/html"})
        self.assertEqual(st, 302)
        self.assertEqual(self.P.req("GET", "/theme-boot.js")[0], 200)

    def test_scan_paths_404_no_leak(self):
        paths = ["/.env", "/.git/HEAD", "/.git/config", "/config.py", "/_data/", "/_data/portal.db", "/fastportal/", "/fastportal/app.py",
                 "/package.json", "/vite.config.ts", "/src/", "/src/main.tsx", "/.vite/manifest.json", "/app/.vite/manifest.json",
                 "/assets/../.vite/manifest.json", "/portal.py", "/tools.json", "/backup.sh", "/index.html.bak", "/.DS_Store", "/server-status",
                 "/assets/", "/theme/", "/node_modules/", "/README.md"]
        for who in (None, self.alice):
            for p in paths:
                st, h, b = self.P.req("GET", p, cookie=who)
                self.assertEqual(st, 404, f"{p} {who and 'user'}: {st}")
                self.assertNotIn(b"Traceback", b)
                self.assertNotIn(self.P.data.encode(), b)
                self.assertLess(len(b), 200, p)
        self.assertEqual(self.P.req("GET", "/guide/../fastportal/app.py")[0], 401, "사용 설명서는 로그인 뒤")
        self.assertEqual(self.P.req("GET", "/guide/../fastportal/app.py", cookie=self.alice)[0], 404)

    def test_headers(self):
        for p in ("/login", "/api/version", "/assets/index-A1.js", "/nope"):
            st, h, _ = self.P.req("GET", p)
            self.assertEqual(h.get("x-robots-tag"), "noindex, nofollow", p)
            self.assertNotIn("server", h, p)


@unittest.skipUnless(os.path.exists(os.path.join(VENV, "bin", "python")), "fastportal venv 없음")
class ProbeHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P = Portal({"PORTAL_PROBE_MAX": "6", "PORTAL_PROBE_BLOCK_S": "2"})

    @classmethod
    def tearDownClass(cls):
        cls.P.close()

    def test_block_retry_after_and_expiry(self):
        P = self.P
        alice = P.login("alice2", signup=True)
        for i in range(5):
            self.assertEqual(P.req("GET", f"/api/nope{i}", cookie=alice)[0], 404)
        self.assertEqual(P.req("GET", "/api/me", cookie=alice)[0], 200, "문턱 아래면 그대로")
        self.assertEqual(P.req("GET", "/api/nope5", cookie=alice)[0], 404)   # 여섯 번째 → 차단 걸림
        st, h, b = P.req("GET", "/api/me", cookie=alice)
        self.assertEqual(st, 429)
        self.assertEqual(json.loads(b)["code"], "probe_blocked")
        self.assertTrue(1 <= int(h["retry-after"]) <= 2)
        self.assertEqual(P.req("GET", "/api/version")[0], 200, "상태 확인은 막지 않음")
        self.assertEqual(P.req("GET", "/api/me", owner=True)[0], 200, "로컬 주인 토큰은 막지 않음")
        self.assertEqual(P.req("GET", "/assets/index-A1.js")[0], 200, "같은 IP 의 다른(익명) 키는 따로")
        time.sleep(2.2)
        self.assertEqual(P.req("GET", "/api/me", cookie=alice)[0], 200, "시간이 지나면 풀림")
        st, _, b = P.req("GET", "/api/admin", owner=True)
        aud = [a for a in json.loads(b)["audit"] if a["action"] == "probe_block"]
        self.assertTrue(aud and aud[0]["actor"] == "alice2" and "/api/nope" in aud[0]["detail"], aud)
        st, _, b = P.req("GET", "/api/jobs?limit=5", owner=True)
        self.assertTrue(any(j["tool"] == "security" and j["link"] == "/admin" for j in json.loads(b)["jobs"]), b[:300])

    def test_owner_token_exempt(self):
        for i in range(20):
            self.P.req("GET", f"/api/owner-nope{i}", owner=True)
        self.assertEqual(self.P.req("GET", "/api/me", owner=True)[0], 200)

    def test_anon_scan_blocks_then_escalates(self):
        P = self.P
        hd = {"User-Agent": "scanner"}
        # 익명 스캔 — 시험 서버의 다른 시험과 섞이지 않게 고유 경로
        for i in range(6):
            P.req("GET", f"/scan-{i}.php", hd)
        st, h, _ = P.req("GET", "/login", hd)
        self.assertEqual(st, 429)
        self.assertEqual(h["retry-after"], "2")
        time.sleep(2.2)
        for i in range(6):
            P.req("GET", f"/scan2-{i}.php", hd)
        st, h, _ = P.req("GET", "/login", hd)
        self.assertEqual(st, 429)
        self.assertIn(h["retry-after"], ("3", "4"), "두 번째는 2배")
        time.sleep(4.2)
        self.assertEqual(P.req("GET", "/login", hd)[0], 200)


if __name__ == "__main__":
    unittest.main()
