"""활성 체류 구간의 계정 분리·중복 제거·관리자 지표 분리 검증."""
import datetime, os, tempfile, unittest
from unittest.mock import patch

from fastportal import config, db, dwell, jobs

TOOL = {"dir": "sql-local", "name": "SQL", "port": 9999}
VISIT = "2a1ed19e-1a2b-4b20-8b54-56c1f94db9e2"


class Ctx:
    def __init__(self, uid, owner=False, raw="/api/admin/dwell?days=7"):
        self.uid, self.is_owner, self.raw = uid, owner, raw
    def send(self, body, ctype="application/json", code=200, headers=None):
        return code, body


class DwellTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                         patch.object(dwell.tools, "tool", side_effect=lambda d: TOOL if d == TOOL["dir"] else None),
                         patch.object(dwell.tools, "tools", return_value=[TOOL])]
        for p in self.patchers: p.start()
        db.init_db()
        with db.db() as c:
            jobs.init(c)
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('alice','Alice','연구','2026-01-01')")
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('bob','Bob','연구','2026-01-01')")

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.tmp.cleanup()

    def test_duplicate_and_account_scoped_segments(self):
        with db.db() as c:
            self.assertEqual(dwell.record(Ctx("alice"), c, {"visit": VISIT, "seq": 0, "screen": "tool", "tool": "sql-local", "seconds": 15})[0], 200)
            dwell.record(Ctx("alice"), c, {"visit": VISIT, "seq": 0, "screen": "tool", "tool": "sql-local", "seconds": 15})
            dwell.record(Ctx("bob"), c, {"visit": VISIT, "seq": 0, "screen": "tool", "tool": "sql-local", "seconds": 12})
        with db.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dwell_segments").fetchone()[0], 2)

    def test_admin_report_separates_dwell_open_and_runtime(self):
        now = datetime.datetime.now()
        with db.db() as c:
            dwell.record(Ctx("alice"), c, {"visit": VISIT, "seq": 1, "screen": "tool", "tool": "sql-local", "seconds": 20})
            c.execute("INSERT INTO events VALUES('open','sql-local',?,'alice')", (now.isoformat(timespec="seconds"),))
            c.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      ("job-1", "alice", "sql-local", "작업", "done", 100, now.timestamp()-42, now.timestamp(), "/", "완료", 1, now.timestamp()))
        response = dwell.report(Ctx("owner", owner=True))
        self.assertEqual(response[0], 200)
        data = response[1]
        self.assertEqual(data["active_seconds"], 20)
        self.assertEqual(data["visits"], 1)
        self.assertEqual(data["tool_opens"], 1)
        self.assertEqual(data["finished_jobs"], 1)
        self.assertEqual(data["job_runtime_seconds"], 42)
        self.assertEqual(data["rows"][0]["uid"], "alice")
        self.assertEqual(data["tool_rows"][0]["opens"], 1)
        self.assertEqual(data["tool_rows"][0]["jobs"], 1)
        self.assertEqual(data["tool_rows"][0]["active_seconds"], 20)
        self.assertEqual(dwell.report(Ctx("alice"))[0], 403)

    def test_bad_values_rejected(self):
        for payload in ({"visit": VISIT, "seq": 0, "screen": "tool", "tool": "unknown", "seconds": 10},
                        {"visit": VISIT, "seq": 0, "screen": "admin", "seconds": 31},
                        {"visit": "bad", "seq": 0, "screen": "home", "seconds": 10}):
            with db.db() as c, self.assertRaises(ValueError): dwell.record(Ctx("alice"), c, payload)


if __name__ == "__main__": unittest.main()
