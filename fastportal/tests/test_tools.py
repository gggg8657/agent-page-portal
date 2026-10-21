"""도구 시작 막기(tools.claim_start) — 동시에 눌러도 한 번만, start_s 동안 다시 못 켬, 중지하면 풀림."""
import threading, unittest
from unittest import mock

from fastportal import tools


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


if __name__ == "__main__":
    unittest.main()
