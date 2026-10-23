"""사용 통계: 이벤트 화이트리스트·중복 제거·속도 제한, 관리자 집계(이탈·깔때기), 숨김 도구 제외, CSV 가명·BOM."""
import datetime, os, tempfile, unittest
from unittest.mock import patch

from fastportal import analytics, config, db, dwell, jobs, timeutil

TOOLS = [{"dir": "sql-local", "name": "SQL", "port": 9999}, {"dir": "persona-local", "name": "페르소나", "port": 9998, "hidden": True}]
V1 = "2a1ed19e-1a2b-4b20-8b54-56c1f94db9e2"
V2 = "3b2fe2af-2b3c-4c31-9c65-67d2a05ec0f3"


class Ctx:
    def __init__(self, uid, owner=False, raw="/api/admin/analytics?days=7"):
        self.uid, self.is_owner, self.raw = uid, owner, raw
    def send(self, body, ctype="application/json", code=200, headers=None):
        return code, body, ctype, dict(headers or [])


def tool(d):
    return next((t for t in TOOLS if t["dir"] == d), None)


class AnalyticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                         patch.object(config, "SECRETS_DIR", self.tmp.name), patch.object(config, "DATA", self.tmp.name),
                         patch.object(analytics.tools, "tool", side_effect=tool),
                         patch.object(analytics.tools, "tools", return_value=[dict(t) for t in TOOLS]),
                         patch.object(analytics.toolstate, "admin_hidden", return_value=set())]
        for p in self.patchers: p.start()
        analytics._RATE.clear()
        db.init_db()
        with db.db() as c:
            jobs.init(c)
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('alice','Alice','연구','2026-01-01')")

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.tmp.cleanup()

    def ev(self, uid, visit, *items):
        with db.db() as c:
            return analytics.record(Ctx(uid), c, {"visit": visit, "events": list(items)})

    def seg(self, c, uid, visit, seq, screen, tool_dir, seconds, ts):
        c.execute("INSERT INTO dwell_segments VALUES(?,?,?,?,?,?,?,?)", (uid, visit, seq, screen, tool_dir, seconds, ts, ts[:10]))

    def test_whitelist_dedupe_and_rate_limit(self):
        e = {"seq": 0, "name": "card_open", "screen": "home", "tool": "sql-local"}
        self.assertEqual(self.ev("alice", V1, e)[0], 200)
        self.ev("alice", V1, e)
        with db.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ui_events").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT uid FROM ui_events").fetchone()[0], "alice")
        for bad in ({"seq": 1, "name": "hack"}, {"seq": 1, "name": "like", "screen": "nope"},
                    {"seq": 1, "name": "like", "tool": "x"}, {"seq": 1, "name": "tut_step", "target": "A B"},
                    {"seq": -1, "name": "like"}):
            with self.assertRaises(ValueError): self.ev("alice", V1, bad)
        with self.assertRaises(ValueError): self.ev("alice", "bad", e)
        with self.assertRaises(ValueError): self.ev("alice", V1, *[dict(e, seq=i) for i in range(51)])
        for i in range(7):
            r = self.ev("bob", V2, *[{"seq": i * 50 + j, "name": "like", "screen": "community"} for j in range(50)])
        self.assertEqual(r[0], 429)

    def test_report_exits_funnels_and_hidden(self):
        now = timeutil.utcnow()
        t = lambda m: (now - datetime.timedelta(minutes=m)).isoformat(timespec="seconds")
        with db.db() as c:
            # 세션1: home → tool:sql → community(끝). 세션2(2시간 뒤 아님 — 120분 전): home 만
            self.seg(c, "alice", V1, 0, "home", "", 20, t(200))
            self.seg(c, "alice", V2, 0, "tool", "sql-local", 30, t(199))
            self.seg(c, "alice", V2, 1, "tool", "sql-local", 10, t(198))
            self.seg(c, "alice", V1.replace("2a", "4c", 1), 0, "community", "", 15, t(197))
            self.seg(c, "alice", V1.replace("2a", "5d", 1), 0, "home", "", 5, t(60))
            self.seg(c, "alice", V2, 2, "tool", "persona-local", 30, t(59))
            c.execute("INSERT INTO events VALUES('like','sql-local',?,'alice')", (t(198),))
            c.execute("INSERT INTO events VALUES('like','persona-local',?,'alice')", (t(198),))
            c.execute("INSERT INTO jobs(id,user,tool,status,started,finished) VALUES('j1','alice','sql-local','done',?,?)", (now.timestamp() - 60, now.timestamp()))
        self.ev("alice", V2, {"seq": 0, "name": "tool_run", "screen": "tool", "tool": "sql-local"},
                {"seq": 1, "name": "tut_start", "screen": "tool", "tool": "sql-local", "target": "sql"},
                {"seq": 2, "name": "tut_step", "screen": "tool", "tool": "sql-local", "target": "sql", "step": 0},
                {"seq": 3, "name": "tut_step", "screen": "tool", "tool": "sql-local", "target": "sql", "step": 1},
                {"seq": 4, "name": "tut_abort", "screen": "tool", "tool": "sql-local", "target": "sql", "step": 1},
                {"seq": 5, "name": "like", "screen": "community", "target": "post"})
        self.assertEqual(analytics.report(Ctx("alice"))[0], 403)
        self.assertEqual(analytics.report(Ctx("o", True, "/api/admin/analytics?days=3"))[0], 400)
        code, d, *_ = analytics.report(Ctx("o", True))
        self.assertEqual(code, 200)
        items = {i["key"]: i for i in d["items"]}
        self.assertNotIn("tool:persona-local", items)
        self.assertNotIn("persona", str(d))
        self.assertEqual(items["tool:sql-local"]["seconds"], 40)
        self.assertEqual(items["tool:sql-local"]["median"], 40)
        self.assertEqual(items["tool:sql-local"]["likes"], 1)
        self.assertEqual(items["community"]["likes"], 1)
        self.assertEqual(items["home"]["median"], 12)
        self.assertEqual(d["totals"]["sessions"], 2)
        ex = {e["key"]: e for e in d["exits"]}
        self.assertEqual(ex["community"]["rate"], 1.0)
        self.assertEqual(ex["home"]["sessions"], 2)
        self.assertEqual(ex["home"]["exits"], 1)
        self.assertIn({"from": "home", "from_name": "홈", "to": "tool:sql-local", "to_name": "SQL", "n": 1}, d["transitions"])
        self.assertEqual(d["tutorials"][0], {"id": "sql", "start": 1, "steps": [1, 1], "done": 0, "abort": 1})
        self.assertEqual([f["users"] for f in d["funnel"]], [1, 1, 1, 1])
        self.assertEqual(sum(x["seconds"] for x in d["daily"]), 80)

    def test_csv_bom_pseudonym_stable(self):
        with db.db() as c:
            self.seg(c, "alice", V1, 0, "home", "", 20, timeutil.now_iso())
        self.ev("alice", V1, {"seq": 0, "name": "card_open", "screen": "home", "tool": "sql-local"})
        code, body, ctype, hd = analytics.export(Ctx("o", True, "/api/admin/analytics.csv?kind=raw&days=7"))
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/csv"))
        self.assertIn("attachment", hd["Content-Disposition"])
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        text = body.decode("utf-8-sig")
        self.assertNotIn("alice", text); self.assertNotIn("Alice", text)
        self.assertIn(analytics.pseudo("alice"), text)
        self.assertEqual(analytics.export(Ctx("o", True, "/api/admin/analytics.csv?kind=raw&days=7"))[1], body)
        self.assertEqual(oct(os.stat(os.path.join(self.tmp.name, ".analytics_hmac")).st_mode & 0o777), "0o600")
        code, body, *_ = analytics.export(Ctx("o", True, "/api/admin/analytics.csv?kind=daily&days=7"))
        self.assertIn("홈", body.decode("utf-8-sig"))
        self.assertEqual(analytics.export(Ctx("alice", False, "/api/admin/analytics.csv?kind=raw&days=7"))[0], 403)
        self.assertEqual(analytics._cell("=cmd"), "'=cmd")

    def test_new_screens_accepted_by_dwell(self):
        with db.db() as c:
            for i, s in enumerate(("plaza", "community", "create", "ask", "board", "post", "write")):
                self.assertEqual(dwell.record(Ctx("alice"), c, {"visit": V1, "seq": i, "screen": s, "seconds": 5})[0], 200)


if __name__ == "__main__": unittest.main()
