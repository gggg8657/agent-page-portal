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
            hd = jobs.tool_headers("kim")
        self.assertEqual(hd["X-Agent-User"], "kim")
        ts = hd["X-Agent-Auth"].split(".")[1]
        self.assertEqual(hd["X-Agent-Auth"], jobs.sign("kim", int(ts)), "도구가 확인하는 서명과 같음")
        with mock.patch.object(jobs.config, "TOOL_AUTH", False):
            self.assertEqual(jobs.tool_headers("kim"), {"X-Agent-User": "kim"})


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
