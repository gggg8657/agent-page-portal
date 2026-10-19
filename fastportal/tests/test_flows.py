"""모두의 에이전트 연결(flows.py) — 어댑터 목록 밖 거절, 매핑 검사, 계정 격리(403), 사용자당 동시 실행 1, 앞 단계 결과 넘김·실패 단계에서 멈춤,
서명 헤더(X-Agent-User = 실행자). 실제 도구·LLM 은 부르지 않음(가짜 HTTP 도구)."""
import json, os, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from fastportal import config, flows, jobs
from fastportal.db import db

SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, self.headers.get("X-Agent-User"), self.headers.get("X-Agent-Auth"), body))
        if self.path == "/api/generate":   # writer: SSE
            out = ("data: " + json.dumps({"stage": "llm", "msg": "쓰는 중"}) + "\n\n" + "data: " + json.dumps({"done": {"variants": ["초안:" + body["values"]["body"]]}}) + "\n\n").encode()
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
        elif self.path == "/api/polish_text":
            if "실패" in body["text"]:
                o = json.dumps({"error": "빈 글"}).encode(); self.send_response(400)
            else:
                o = json.dumps({"output": body["text"] + " (다듬음)", "changes": [1], "rejected": []}).encode(); self.send_response(200)
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(o))); self.end_headers(); self.wfile.write(o)
        else:
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers()


class FlowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        port = cls.srv.server_address[1]
        cls.TOOLS = [{"dir": "writer-local", "port": port, "name": "Writer"}, {"dir": "kordoc-local", "port": port, "name": "kordoc"},
                     {"dir": "persona-local", "port": port, "hidden": True}]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        T = {t["dir"]: t for t in self.TOOLS}
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(flows.tools, "tool", side_effect=lambda d: T.get(d)),
                  patch.object(flows.tools, "tools", return_value=self.TOOLS),
                  patch.object(jobs, "upsert", return_value=None)]
        for x in self.p: x.start()
        with db() as c:
            flows.init(c)
            c.execute("CREATE TABLE IF NOT EXISTS audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
        SEEN.clear()
        flows._ACTIVE.clear()

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def save(self, uid="alice", steps=None, **kw):
        steps = steps or [{"adapter": "writer.notice", "inputs": {"title": {"from": "const", "value": "안내"}}}, {"adapter": "kordoc.polish"}]
        with db() as c:
            return flows.save(c, uid, {"name": "시험", "steps": steps, **kw})

    def run_flow(self, uid, fid, inputs, agent_user=None):
        with db() as c:
            res, go = flows.start_run(c, uid, agent_user or uid, {"id": fid, "inputs": inputs})
        go()
        rid = res["run"]["id"]
        for _ in range(100):
            with db() as c:
                r = flows.get_run(c, uid, rid)
            if r["status"] != "running":
                return r
            time.sleep(0.05)
        self.fail("끝나지 않음")

    def test_only_listed_adapters_and_mapping_rules(self):
        bad = [
            [{"adapter": "persona.chat"}, {"adapter": "kordoc.polish"}],                       # 목록 밖
            [{"adapter": "kordoc.polish"}],                                                     # 단계 1개
            [{"adapter": "kordoc.polish", "inputs": {"text": {"from": "prev"}}}, {"adapter": "kordoc.polish"}],   # 첫 단계 prev
            [{"adapter": "kordoc.polish"}, {"adapter": "kordoc.polish", "inputs": {"text": {"from": "step", "step": 1}}}],   # 자기/뒤 단계
            [{"adapter": "kordoc.polish"}, {"adapter": "kordoc.polish", "inputs": {"strength": {"from": "const", "value": "rm -rf"}}}],   # 목록 밖 선택값
            [{"adapter": "kordoc.polish"}, {"adapter": "kordoc.polish", "inputs": {"text": {"from": "url", "value": "http://x"}}}],
        ]
        for st in bad:
            with self.assertRaises(ValueError, msg=st):
                self.save(steps=st)
        f = self.save()
        self.assertEqual(f["steps"][1]["inputs"]["text"], {"from": "prev"}, "기본: 앞 단계 결과")
        self.assertEqual(f["steps"][0]["inputs"]["body"], {"from": "ask"})

    def test_account_isolation(self):
        f = self.save("alice")
        with db() as c:
            for op in (lambda: flows.get_flow(c, "bob", f["id"]), lambda: flows.save(c, "bob", {"id": f["id"], "name": "x", "steps": f["steps"]}),
                       lambda: flows.delete(c, "bob", {"id": f["id"]}), lambda: flows.start_run(c, "bob", "bob", {"id": f["id"], "inputs": {}})):
                with self.assertRaises(PermissionError):
                    op()
        r = self.run_flow("alice", f["id"], {"0.body": "정전"})
        with db() as c, self.assertRaises(PermissionError):
            flows.get_run(c, "bob", r["id"])
        with db() as c:
            self.assertEqual(flows.listing(c, "bob")["flows"], [])

    def test_chain_passes_previous_result_and_signs_as_runner(self):
        f = self.save("alice")
        r = self.run_flow("alice", f["id"], {"0.body": "3동 정전"}, agent_user="alice")
        self.assertEqual(r["status"], "done", r)
        self.assertEqual(r["steps"][1]["text"], "초안:3동 정전 (다듬음)")
        self.assertEqual(SEEN[1][3]["text"], "초안:3동 정전", "앞 단계 결과가 다음 입력")
        for _, user, auth, _ in SEEN:
            self.assertEqual(user, "alice")
            if config.TOOL_AUTH:
                self.assertTrue(auth and auth.startswith("v1."), "포털 서명")

    def test_stops_at_failed_step(self):
        st = [{"adapter": "kordoc.polish"}, {"adapter": "kordoc.polish"}, {"adapter": "kordoc.polish"}]
        f = self.save(steps=st)
        r = self.run_flow("alice", f["id"], {"0.text": "실패하는 글"})
        self.assertEqual(r["status"], "failed")
        self.assertEqual([s["status"] for s in r["steps"]], ["failed", "skipped", "skipped"])
        self.assertIn("빈 글", r["steps"][0]["error"])
        self.assertEqual(len(SEEN), 1)

    def test_required_runtime_input_and_one_run_per_user(self):
        f = self.save()
        with db() as c, self.assertRaises(ValueError):
            flows.start_run(c, "alice", "alice", {"id": f["id"], "inputs": {}})
        flows._ACTIVE["alice"] = "x"
        with db() as c, self.assertRaises(ValueError):
            flows.start_run(c, "alice", "alice", {"id": f["id"], "inputs": {"0.body": "a"}})
        flows._ACTIVE.clear()

    def test_tool_down_is_reported(self):
        with patch.object(flows.tools, "alive_sync", return_value=False):
            f = self.save()
            r = self.run_flow("alice", f["id"], {"0.body": "a"})
        self.assertEqual(r["status"], "failed")
        self.assertIn("꺼져 있습니다", r["steps"][0]["error"])

    def test_community_counts_only(self):
        f = self.save("alice")
        self.run_flow("alice", f["id"], {"0.body": "a"})
        with db() as c:
            st = flows.community(c)
        self.assertEqual((st["flows"], st["flow_makers"], st["flow_runs_7d"]), (1, 1, 1))
        self.assertFalse(any(isinstance(v, (str, list, dict)) for v in st.values()), "수만 — 이름·내용 없음")

    def test_hidden_tool_not_listed(self):
        self.assertFalse(any(a["tool"] == "persona-local" for a in flows.adapter_list()))


if __name__ == "__main__":
    unittest.main()
