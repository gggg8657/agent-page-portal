"""도구 시작 막기(tools.claim_start) — 동시에 눌러도 한 번만, start_s 동안 다시 못 켬, 중지하면 풀림."""
import json, os, tempfile, threading, unittest
from unittest import mock

from fastportal import jobs, tools


class ClaimStartTest(unittest.TestCase):
    def setUp(self):
        tools._STARTING.clear()

    def test_once_under_race(self):
        t, got = {"dir": "x-local"}, []
        ths = [threading.Thread(target=lambda: got.append(tools.claim_start(t))) for _ in range(16)]
        [h.start() for h in ths]; [h.join() for h in ths]
        self.assertEqual(got.count(True), 1)

    def test_gap_and_stop(self):
        t = {"dir": "x-local", "port": 1, "start_s": 0}
        self.assertTrue(tools.claim_start(t)); self.assertTrue(tools.claim_start(t), "start_s=0 이면 바로 다시")
        t["start_s"] = 180
        self.assertFalse(tools.claim_start(t))
        with mock.patch.object(tools, "port_pids", lambda p: set()):
            tools.stop({**t, "dir": "x-local"})   # setup.sh 없는 폴더 — 포트 주인만 찾음
        self.assertTrue(tools.claim_start(t), "중지하면 막기가 풀림")


class ToolHeadersTest(unittest.TestCase):
    def test_sign_only_with_tool_auth(self):
        with mock.patch.object(jobs.config, "TOOL_AUTH", True):
            hd = jobs.tool_headers("kim", "a-local")
        self.assertEqual(hd["X-Agent-User"], "kim")
        ts = hd["X-Agent-Auth"].split(".")[1]
        self.assertEqual(hd["X-Agent-Auth"], jobs.sign("kim", "a-local", int(ts)), "도구가 확인하는 서명과 같음")
        with mock.patch.object(jobs.config, "TOOL_AUTH", False):
            self.assertEqual(jobs.tool_headers("kim", "a-local"), {"X-Agent-User": "kim"})
            self.assertEqual(jobs.tool_headers("kim", "a-local", "admin"), {"X-Agent-User": "kim"}, "서명을 안 쓰면 역할도 안 보냄")

    def test_role_signed_like_sdk(self):
        """관리자 계정 역할: 포털 서명(jobs.sign_role)을 도구 관문(sdk/portal_auth)이 그대로 인정 — 다른 사용자·다른 도구로는 안 통함"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("pa_sdk", os.path.join(os.path.dirname(jobs.__file__), "..", "sdk", "portal_auth.py"))
        pa = importlib.util.module_from_spec(spec); spec.loader.exec_module(pa)
        with mock.patch.object(jobs.config, "TOOL_AUTH", True), mock.patch.dict(os.environ, {"PORTAL_TOOL_KEY": jobs.TOOL_KEY}):
            hd = jobs.tool_headers("kim", "a-local", "admin")
            self.assertEqual(hd["X-Agent-Role"], "admin")
            self.assertTrue(pa.verify(hd["X-Agent-User"], hd["X-Agent-Auth"], "a-local"))
            self.assertTrue(pa.verify_role("kim", "admin", hd["X-Agent-Role-Auth"], "a-local"))
            self.assertFalse(pa.verify_role("lee", "admin", hd["X-Agent-Role-Auth"], "a-local"), "다른 사용자에 옮겨 붙일 수 없음")
            self.assertFalse(pa.verify_role("kim", "admin", hd["X-Agent-Role-Auth"], "b-local"), "다른 도구에 다시 쓸 수 없음")
            self.assertNotIn("X-Agent-Role", jobs.tool_headers("kim", "a-local"), "일반 계정은 역할 없음")


class AgentUserTest(unittest.TestCase):
    """계정마다 자기 작업 공간: X-Agent-User = 계정 아이디(관리자 포함), owner 는 로컬 주인 토큰뿐. 관리자는 서명된 역할로"""
    def _ctx(self, uid):
        from fastportal import ctx as C
        c = C.Ctx.__new__(C.Ctx); c.uid = uid
        return c

    def test_admin_accounts_split(self):
        from fastportal import ctx as C
        with mock.patch.object(C.accounts, "is_admin", lambda u: u == "owner" or u in {"adm1", "adm2"}):
            a, b, u, o = (self._ctx(x) for x in ("adm1", "adm2", "kim", "owner"))
            self.assertEqual((a.agent_user, b.agent_user), ("adm1", "adm2"), "관리자 계정끼리 작업 공간을 나누지 않음")
            self.assertEqual((a.agent_role, b.agent_role, u.agent_role, o.agent_role), ("admin", "admin", "", ""), "owner 는 이름만으로 관리자(역할 안 붙임)")
            self.assertEqual((u.agent_user, o.agent_user), ("kim", "owner"))

    def test_proxy_headers(self):
        from fastportal import proxy
        from types import SimpleNamespace as NS
        req = NS(headers=NS(raw=[(b"x-agent-role", b"admin"), (b"x-agent-role-auth", b"r2.1.x"), (b"x-agent-user", b"owner"), (b"accept", b"*/*")]))
        with mock.patch.object(jobs.config, "TOOL_AUTH", True):
            hd = dict(proxy.upstream_headers(NS(request=req, agent_user="kim", agent_role=""), None, "a-local"))
            self.assertEqual(hd["X-Agent-User"], "kim")
            self.assertNotIn("X-Agent-Role", hd, "밖에서 붙인 역할 헤더는 버림")
            self.assertNotIn("x-agent-role", hd)
            hd = dict(proxy.upstream_headers(NS(request=req, agent_user="adm1", agent_role="admin"), None, "a-local"))
            self.assertEqual((hd["X-Agent-User"], hd["X-Agent-Role"]), ("adm1", "admin"))
            self.assertTrue(hd["X-Agent-Role-Auth"].startswith("r2."))


class AdminInboxTest(unittest.TestCase):
    """작업 알림: 관리자 계정 = 본인 + 관리자 알림함('owner'), 일반 = 본인, 로컬 주인 = 전부"""
    def test_vis(self):
        with mock.patch.object(jobs.config, "OWNERS", {"adm1"}):
            self.assertEqual(jobs._vis("owner"), ("1=1", ()))
            self.assertEqual(jobs._vis("adm1"), ("user IN (?, 'owner')", ("adm1",)))
            self.assertEqual(jobs._vis("kim"), ("user=?", ("kim",)))

    def test_hub(self):
        with mock.patch.object(jobs.config, "OWNERS", {"adm1"}):
            h = jobs.Hub()
            qs = {v: h.subscribe(v) for v in ("owner", "adm1", "kim")}
            h._dispatch("job", 1, "owner")
            h._dispatch("job", 2, "kim")
            h._dispatch("read", 3, "owner", exact=True)
            got = {v: [q.get_nowait()[1] for _ in range(q.qsize())] for v, q in qs.items()}
            self.assertEqual(got, {"owner": [1, 2, 3], "adm1": [1], "kim": [2]})


class AddToolTest(unittest.TestCase):
    def test_writes_whole_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "tools.json")
            json.dump([{"dir": "a-local", "port": 9001}], open(f, "w"))
            with mock.patch.object(tools.config, "TOOLS_FILE", f), mock.patch.dict(tools._TOOLS, {"m": None, "v": []}):
                tools.add_tool({"dir": "b-local", "port": "9002", "title": "<b>"})
                self.assertEqual([t["dir"] for t in json.load(open(f))], ["a-local", "b-local"])
                self.assertEqual(os.listdir(d), ["tools.json"], "임시 파일이 남지 않음")
                with self.assertRaises(ValueError):
                    tools.add_tool({"dir": "c-local", "port": "9001"})   # 포트 겹침


if __name__ == "__main__":
    unittest.main()
