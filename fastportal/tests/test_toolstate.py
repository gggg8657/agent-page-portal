"""관리자 도구 켜기·끄기(toolstate·admintools) — 꺼진 도구는 일반·관리자 모두의 사용자 목록에서 빠지고(/api/tools·즐겨찾기·최근·흐름 어댑터·
아이디어 후보·리뷰 대상·내 작업), /t/<dir>/ 는 일반 계정 403 tool_disabled·관리자는 그대로. 바꿀 때마다 감사 기록. 다시 켜면 그대로 돌아옴.
통합 시험은 임시 데이터·임시 tools.json(가짜 도구 = 이 시험이 띄운 작은 HTTP 서버)으로 포털을 따로 띄움 — 운영 도구·데이터를 건드리지 않음."""
import http.server, json, os, shutil, sqlite3, tempfile, threading, time, unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import config, idea, toolstate
from fastportal.db import db
from fastportal.tests.test_guard import VENV, Portal, _free


class Unit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.p = patch.object(config, "DB_FILE", os.path.join(self.tmp, "portal.db"))
        self.p.start()
        toolstate.reset()
        with db() as c:
            c.execute("CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
            toolstate.init(c)

    def tearDown(self):
        self.p.stop()
        toolstate.reset()
        shutil.rmtree(self.tmp)

    def test_set_state_audit_and_file(self):
        self.assertEqual(toolstate.off(), frozenset())
        with db() as c:
            self.assertTrue(toolstate.set_state(c, "boss", "a-local", False, "점검 중"))
            self.assertFalse(toolstate.set_state(c, "boss", "a-local", False, "또"), "같은 상태면 바뀐 것 없음(감사도 안 남김)")
        toolstate.reset()
        self.assertTrue(toolstate.is_off("a-local"))
        self.assertTrue(toolstate.user_hidden({"dir": "a-local"}))
        self.assertFalse(toolstate.user_hidden({"dir": "b-local"}))
        toolstate.write_file()
        self.assertEqual(json.load(open(os.path.join(self.tmp, "tools-disabled.json")))["disabled"], ["a-local"])
        with db() as c:
            toolstate.set_state(c, "boss", "a-local", True)
            rows = [tuple(r) for r in c.execute("SELECT actor, action, target, detail FROM audit ORDER BY rowid")]
        self.assertEqual(rows, [("boss", "tool_disable", "a-local", "점검 중"), ("boss", "tool_enable", "a-local", "")])
        toolstate.reset()
        self.assertFalse(toolstate.is_off("a-local"))

    def test_hide_independent_of_off(self):
        with db() as c:
            self.assertTrue(toolstate.set_visible(c, "boss", "a-local", False, "정리 중"))
            self.assertFalse(toolstate.set_visible(c, "boss", "a-local", False), "같은 상태면 바뀐 것 없음")
        toolstate.reset()
        self.assertEqual((toolstate.admin_hidden(), toolstate.off()), (frozenset({"a-local"}), frozenset()), "숨김은 꺼짐과 따로")
        self.assertTrue(toolstate.is_hidden({"dir": "a-local"}))
        self.assertTrue(toolstate.unlisted({"dir": "a-local"}))
        self.assertFalse(toolstate.user_hidden({"dir": "a-local"}), "숨김은 링크로 열림")
        self.assertTrue(toolstate.is_hidden({"dir": "b-local", "hidden": True}), "tools.json hidden 도 숨김")
        with db() as c:
            toolstate.set_state(c, "boss", "a-local", False)
        toolstate.reset()
        self.assertTrue(toolstate.is_off("a-local") and toolstate.is_hidden({"dir": "a-local"}))
        toolstate.write_file()
        f = json.load(open(os.path.join(self.tmp, "tools-disabled.json")))
        self.assertEqual((f["disabled"], f["hidden"]), (["a-local"], ["a-local"]))
        with db() as c:
            toolstate.set_visible(c, "boss", "a-local", True)
            toolstate.set_state(c, "boss", "a-local", True)
            rows = [tuple(r) for r in c.execute("SELECT action, detail FROM audit ORDER BY rowid")]
        self.assertEqual(rows, [("tool_hide", "정리 중"), ("tool_disable", ""), ("tool_show", ""), ("tool_enable", "")])

    def test_migrate_old_table(self):
        with db() as c:
            c.execute("DROP TABLE tool_state")
            c.execute("CREATE TABLE tool_state(dir TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, by TEXT, ts TEXT, reason TEXT)")
            c.execute("INSERT INTO tool_state(dir, enabled) VALUES('a-local', 0)")
            toolstate.init(c)
            toolstate.init(c)
            self.assertEqual(tuple(c.execute("SELECT enabled, visible FROM tool_state").fetchone()), (0, 1))
        toolstate.reset()
        self.assertEqual((toolstate.off(), toolstate.admin_hidden()), (frozenset({"a-local"}), frozenset()))

    def test_idea_candidates_skip_disabled(self):
        ts = [{"dir": "sql-local", "name": "SQL", "desc": "데이터 질의"}, {"dir": "chart-local", "name": "그래프", "desc": "데이터 그래프"}]
        with db() as c:
            toolstate.set_state(c, "boss", "chart-local", False)
        toolstate.reset()
        with patch.object(idea.tools, "tools", return_value=ts), patch.object(idea.community, "_builder", return_value=(502, None)):
            got = [x["id"] for x in idea.candidates(NS(agent_user="u"), "데이터 그래프")]
        self.assertEqual(got, ["tool:sql-local"])


class _Fake(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = b"fake tool"
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


@unittest.skipUnless(os.path.exists(os.path.join(VENV, "bin", "python")), "fastportal venv 없음")
class HTTP(unittest.TestCase):
    D = "diagram-local"   # 흐름 어댑터(diagram.*)가 있는 이름 — 실제 도구가 아니라 이 시험의 가짜 서버로 감

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for x in os.listdir(src):   # 화면 파일(portal.html 등)·guides·theme — tools.json 만 가짜
            f = os.path.join(src, x)
            if x.endswith(".html"):
                shutil.copy(f, cls.root)
            elif x in ("guides", "theme", "guide", "v2") and os.path.isdir(f):
                shutil.copytree(f, os.path.join(cls.root, x))
        json.dump([{"dir": cls.D, "port": cls.srv.server_address[1], "name": "다이어그램", "title": "다이어그램", "group": "그림", "desc": "도식"},
                   {"dir": "other-local", "port": _free(), "name": "다른 도구", "title": "다른", "group": "문서", "desc": ""}],
                  open(os.path.join(cls.root, "tools.json"), "w"), ensure_ascii=False)
        cls.P = Portal({"PORTAL_PROBE": "0", "PORTAL_ROOT": cls.root, "PORTAL_TOOL_AUTH": "0", "PORTAL_TOOLS_CACHE_S": "0"})
        cls.alice = cls.P.login("alice", signup=True)
        cls.boss = cls.P.login("boss", signup=True, owner=True)

    @classmethod
    def tearDownClass(cls):
        cls.P.close()
        cls.srv.shutdown()
        shutil.rmtree(cls.root, ignore_errors=True)

    def j(self, method, path, cookie, body=None):
        st, _, b = self.P.req(method, path, {"Content-Type": "application/json"} if body is not None else {}, body, cookie=cookie)
        try:
            return st, json.loads(b or b"null")
        except ValueError:
            return st, b

    def test_like_once_under_double_click(self):
        res = []
        ths = [threading.Thread(target=lambda: res.append(self.j("POST", "/api/like", self.alice, {"dir": "other-local"}))) for _ in range(8)]
        [h.start() for h in ths]; [h.join() for h in ths]
        self.assertTrue(all(st == 200 for st, _ in res), res)
        self.assertEqual(sum(1 for _, b in res if not b.get("already")), 1, "좋아요는 한 번만 들어감")

    def test_admin_reset_needs_my_pin(self):
        self.P.login("carol", signup=True)
        self.assertEqual(self.j("POST", "/api/admin/reset", self.boss, {"uid": "carol"})[0], 400, "내 PIN 없이는 거절")
        self.assertEqual(self.j("POST", "/api/admin/reset", self.boss, {"uid": "carol", "my_pin": "0000"})[0], 400)
        st, b = self.j("POST", "/api/admin/reset", self.boss, {"uid": "carol", "my_pin": "4821"})
        self.assertEqual(st, 200); self.assertRegex(b["pin"], r"^[0-9]{6}$")

    def dirs(self, cookie):
        return [t["dir"] for t in self.j("GET", "/api/tools", cookie)[1]["tools"]]

    def toggle(self, enabled, reason="", cookie=None, **kw):
        return self.j("POST", "/api/admin/tool", cookie or self.boss, {"dir": self.D, "enabled": enabled, "reason": reason, **kw})

    def test_flow(self):
        D, A, B = self.D, self.alice, self.boss
        self.assertEqual(self.j("POST", "/api/lists", A, {"kind": "fav", "items": [D, "other-local"]})[0], 200)
        self.assertIn(D, self.dirs(A))
        self.assertIn(D, [a["tool"] for a in self.j("GET", "/api/flows/adapters", A)[1]["adapters"]])
        self.assertEqual(self.P.req("GET", f"/t/{D}/", cookie=A)[0], 200)

        # 일반 계정은 못 바꿈 · 형식
        self.assertEqual(self.toggle(False, cookie=A)[0], 403)
        self.assertEqual(self.j("GET", "/api/admin/tools", A)[0], 403)
        self.assertEqual(self.j("POST", "/api/admin/tool", B, {"dir": "nope-local", "enabled": False})[0], 404)
        self.assertEqual(self.j("POST", "/api/admin/tool", B, {"dir": D, "enabled": "no"})[0], 400)

        # 진행 중 작업이 있으면 멈추기(stop)는 확인 없이는 409 — 상태도 안 바뀜
        db_file = os.path.join(self.P.data, "portal.db")
        with sqlite3.connect(db_file) as c:
            c.execute("INSERT INTO jobs(id,user,tool,title,status,started,updated) VALUES('j1','alice',?,'t','running',?,?)", (D, time.time(), time.time()))
        st, r = self.toggle(False, "점검", stop=True)
        self.assertEqual((st, r.get("code"), r.get("active_jobs")), (409, "jobs_running", 1), r)
        self.assertIn(D, self.dirs(A))
        with sqlite3.connect(db_file) as c:
            c.execute("DELETE FROM jobs WHERE id='j1'")

        # 끄기
        st, r = self.toggle(False, "점검")
        self.assertEqual((st, r["enabled"], r["changed"], r["stopped"]), (200, False, True, False), r)
        self.assertNotIn(D, self.dirs(A))
        self.assertNotIn(D, self.dirs(B), "관리자의 사용자 화면에서도 빠짐(관리 표에만)")
        adm = {t["dir"]: t for t in self.j("GET", "/api/admin/tools", B)[1]["tools"]}
        self.assertEqual((adm[D]["enabled"], adm[D]["reason"], adm[D]["by"]), (False, "점검", "boss"))
        self.assertTrue(adm["other-local"]["enabled"])
        self.assertEqual(self.j("GET", "/api/lists", A)[1]["fav"], ["other-local"], "즐겨찾기에서 빠짐")
        self.assertNotIn(D, [a["tool"] for a in self.j("GET", "/api/flows/adapters", A)[1]["adapters"]])
        st, _, b = self.P.req("GET", f"/t/{D}/", cookie=A)
        self.assertEqual((st, json.loads(b)["code"]), (403, "tool_disabled"))
        self.assertIn("관리자가 이 에이전트를 잠시 껐습니다", json.loads(b)["error"])
        st, _, b = self.P.req("POST", f"/t/{D}/api/x", {"Content-Type": "application/json"}, {"a": 1}, cookie=A)
        self.assertEqual(st, 403)
        st, _, b = self.P.req("GET", f"/t/{D}/", cookie=B)
        self.assertEqual((st, b), (200, b"fake tool"), "관리자는 열어서 시험")
        self.assertEqual(self.j("POST", "/api/open", A, {"dir": D})[1].get("code"), "tool_disabled")
        self.assertEqual(self.j("GET", f"/api/community/reviews?kind=tool&id={D}", A)[0], 403, "리뷰·요청 대상에서도 빠짐")
        self.assertEqual(self.j("GET", f"/api/community/reviews?kind=tool&id={D}", B)[0], 200)
        self.assertEqual(self.P.req("GET", f"/api/guide/{D}", cookie=A)[2], b"")
        self.assertEqual(json.load(open(os.path.join(self.P.data, "tools-disabled.json")))["disabled"], [D])
        aud = self.j("GET", "/api/admin", B)[1]["audit"]
        self.assertTrue(any(a["action"] == "tool_disable" and a["target"] == D and a["detail"] == "점검" for a in aud), aud[:3])

        # 숨겨진 동안 화면이 목록을 다시 써도(꺼진 도구를 모름) 저장된 항목은 남음
        self.assertEqual(self.j("POST", "/api/lists", A, {"kind": "fav", "items": []})[1], {"fav": []})

        # 다시 켜기 → 그대로 돌아옴
        st, r = self.toggle(True)
        self.assertEqual((st, r["changed"]), (200, True))
        self.assertIn(D, self.dirs(A))
        self.assertEqual(self.j("GET", "/api/lists", A)[1]["fav"], [D])
        self.assertEqual(self.P.req("GET", f"/t/{D}/", cookie=A)[0], 200)
        self.assertIn(D, [a["tool"] for a in self.j("GET", "/api/flows/adapters", A)[1]["adapters"]])
        self.assertEqual(json.load(open(os.path.join(self.P.data, "tools-disabled.json")))["disabled"], [])
        aud = self.j("GET", "/api/admin", B)[1]["audit"]
        self.assertEqual(aud[0]["action"], "tool_enable")

    def hide(self, visible, reason="", cookie=None, **kw):
        return self.j("POST", "/api/admin/tool", cookie or self.boss, {"dir": self.D, "visible": visible, "reason": reason, **kw})

    def test_hide(self):
        D, A, B = self.D, self.alice, self.boss
        self.assertEqual(self.j("POST", "/api/lists", A, {"kind": "recent", "items": [D]})[0], 200)
        self.assertEqual(self.hide(False, cookie=A)[0], 403)
        self.assertEqual(self.j("POST", "/api/admin/tool", B, {"dir": D})[0], 400, "enabled·visible 둘 다 없음")
        self.assertEqual(self.j("POST", "/api/admin/tool", B, {"dir": D, "visible": "no"})[0], 400)

        st, r = self.hide(False, "정리 중")
        self.assertEqual((st, r["visible"], r["enabled"], r["changed"]), (200, False, True, True), r)
        ts = {t["dir"]: t for t in self.j("GET", "/api/tools", A)[1]["tools"]}
        self.assertTrue(ts[D]["hidden"], "/api/tools 에는 hidden=true 로 남음(화면이 목록에서 거르고 즐겨찾기·최근·링크에서 찾음)")
        self.assertNotIn(D, [a["tool"] for a in self.j("GET", "/api/flows/adapters", A)[1]["adapters"]])
        self.assertEqual(self.j("GET", "/api/lists", A)[1]["recent"], [D], "최근 사용에는 남음")
        self.assertEqual(self.P.req("GET", f"/t/{D}/healthz", cookie=A)[0], 200, "링크로는 일반 계정도 열림")
        self.assertNotEqual(self.j("POST", "/api/open", A, {"dir": D})[1].get("code"), "tool_disabled")
        self.assertNotEqual(self.P.req("GET", f"/api/guide/{D}", cookie=A)[2], b"")
        adm = {t["dir"]: t for t in self.j("GET", "/api/admin/tools", B)[1]["tools"]}
        self.assertEqual((adm[D]["visible"], adm[D]["hidden_src"], adm[D]["enabled"], adm[D]["hide_reason"], adm[D]["hide_by"]), (False, "admin", True, "정리 중", "boss"))
        self.assertEqual((adm["other-local"]["visible"], adm["other-local"]["hidden_src"]), (True, None))
        self.assertEqual(json.load(open(os.path.join(self.P.data, "tools-disabled.json")))["hidden"], [D])

        # 숨김 + 꺼짐 — 꺼짐이 이김(403), 켜면 숨김만 남음
        self.assertEqual(self.toggle(False)[0], 200)
        self.assertNotIn(D, self.dirs(A))
        self.assertEqual(self.P.req("GET", f"/t/{D}/healthz", cookie=A)[0], 403)
        self.assertEqual(self.toggle(True)[1]["visible"], False)
        self.assertEqual(self.P.req("GET", f"/t/{D}/healthz", cookie=A)[0], 200)

        st, r = self.hide(True)
        self.assertEqual((st, r["visible"], r["changed"]), (200, True, True))
        self.assertFalse({t["dir"]: t for t in self.j("GET", "/api/tools", A)[1]["tools"]}[D].get("hidden"))
        self.assertIn(D, [a["tool"] for a in self.j("GET", "/api/flows/adapters", A)[1]["adapters"]])
        self.assertEqual(json.load(open(os.path.join(self.P.data, "tools-disabled.json")))["hidden"], [])
        acts = [(a["action"], a["target"], a["detail"]) for a in self.j("GET", "/api/admin", B)[1]["audit"][:4]]
        self.assertEqual(acts, [("tool_show", D, ""), ("tool_enable", D, ""), ("tool_disable", D, ""), ("tool_hide", D, "정리 중")])

    def test_builder_agents_admin_only(self):
        self.assertEqual(self.j("GET", "/api/admin/agents", self.alice)[0], 403)
        st, r = self.j("GET", "/api/admin/agents", self.boss)
        self.assertEqual((st, r["builder"], r["agents"]), (200, False, []), "빌더가 없으면 빈 목록 + builder=false")
        self.assertEqual(self.j("POST", "/api/admin/agent", self.alice, {"id": "abc123", "enabled": False})[0], 403)
        self.assertEqual(self.j("POST", "/api/admin/agent", self.boss, {"id": "../x", "enabled": False})[0], 400)
        self.assertEqual(self.j("POST", "/api/admin/agent", self.boss, {"id": "abc123", "enabled": False})[0], 503)


if __name__ == "__main__":
    unittest.main()
