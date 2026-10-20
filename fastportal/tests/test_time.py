"""시각 규칙(timeutil): 저장 UTC(타임존 표시 없음 = UTC) · 보여 주기·달력 KST"""
import datetime, os, tempfile, unittest

from fastportal import timeutil as tu

UTC = datetime.timezone.utc


class TimeUtilTest(unittest.TestCase):
    def test_naive_is_utc_and_formats_in_kst(self):
        self.assertEqual(tu.fmt_kst("2026-10-20T12:55:00"), "2026-10-20 21:55")
        self.assertEqual(tu.fmt_kst("2026-10-20T12:55:00Z"), "2026-10-20 21:55")
        self.assertEqual(tu.fmt_kst("2026-10-20T21:55:00+09:00"), "2026-10-20 21:55")
        self.assertEqual(tu.fmt_kst("2026-10-20 12:55:00", label=True), "2026-10-20 21:55 KST")
        self.assertEqual(tu.fmt_kst(1792500900), tu.fmt_kst(1792500900000))   # epoch 초·ms 같은 값
        self.assertEqual(tu.fmt_kst(""), "")
        self.assertEqual(tu.fmt_kst("어제"), "어제")                            # 못 읽으면 그대로

    def test_kst_date_crosses_midnight(self):
        self.assertEqual(tu.kst_date("2026-10-20T15:00:00"), datetime.date(2026, 10, 21))
        self.assertEqual(tu.kst_date("2026-10-20T14:59:59"), datetime.date(2026, 10, 20))

    def test_week_key_at_2330_utc_is_next_kst_day(self):
        # 2026-10-25(일) 23:30 UTC = 2026-10-26(월) 08:30 KST → 다음 ISO 주
        now = datetime.datetime(2026, 10, 25, 23, 30, tzinfo=UTC)
        self.assertEqual(now.date().isocalendar()[1], 43)
        self.assertEqual(tu.today_kst(now), datetime.date(2026, 10, 26))
        self.assertEqual(tu.iso_week(now), "2026-W44")
        self.assertEqual(tu.iso_week(datetime.datetime(2026, 10, 25, 14, 59, tzinfo=UTC)), "2026-W43")

    def test_daily_cut_boundary(self):
        # '오늘'·'최근 N일' 한도의 시작 = KST 0시 = 전날 15:00 UTC (저장 형식으로)
        now = datetime.datetime(2026, 10, 20, 0, 30, tzinfo=UTC)          # 09:30 KST
        self.assertEqual(tu.kst_day_start_utc(0, now), "2026-10-19T15:00:00")
        self.assertEqual(tu.kst_day_start_utc(30, now), "2026-09-19T15:00:00")
        early = datetime.datetime(2026, 10, 19, 15, 30, tzinfo=UTC)       # 00:30 KST(10-20) — UTC 로는 아직 19일
        cut = tu.kst_day_start_utc(0, early)
        self.assertEqual(cut, "2026-10-19T15:00:00")
        self.assertTrue("2026-10-19T14:59:59" < cut <= "2026-10-19T15:00:00")   # 어제(KST) 마지막 초는 빠지고 0시부터 들어감
        self.assertEqual(datetime.datetime.fromtimestamp(tu.kst_day_start_epoch(datetime.date(2026, 10, 20)), UTC),
                         datetime.datetime(2026, 10, 19, 15, 0, tzinfo=UTC))

    def test_now_iso_is_naive_utc(self):
        s = tu.now_iso()
        self.assertEqual(len(s), 19)
        d = datetime.datetime.fromisoformat(s).replace(tzinfo=UTC)
        self.assertLess(abs((datetime.datetime.now(UTC) - d).total_seconds()), 5)


class DwellDailyKstTest(unittest.TestCase):
    """체류 날짜별 집계는 저장된 UTC ts 를 KST 날짜로 묶고, 기간 시작도 KST 0시(저장값은 그대로)"""

    def setUp(self):
        from unittest.mock import patch
        from fastportal import config, db, dwell, jobs
        self.tmp = tempfile.TemporaryDirectory()
        self.patchers = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                         patch.object(dwell.tools, "tool", return_value=None), patch.object(dwell.tools, "tools", return_value=[])]
        for p in self.patchers: p.start()
        db.init_db()
        with db.db() as c:
            jobs.init(c)
        self.db, self.dwell = db, dwell

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.tmp.cleanup()

    def test_report_daily_uses_kst_dates(self):
        today = tu.today_kst()
        start0 = tu.kst_day_start_utc(0)                       # 오늘(KST) 0시 = UTC 전날 15시
        t_in = (datetime.datetime.fromisoformat(start0) + datetime.timedelta(seconds=30)).isoformat()
        t_prev = (datetime.datetime.fromisoformat(start0) - datetime.timedelta(seconds=30)).isoformat()
        t_old = (datetime.datetime.fromisoformat(tu.kst_day_start_utc(6)) - datetime.timedelta(seconds=30)).isoformat()   # 7일 창 밖
        with self.db.db() as c:
            for seq, (ts, sec) in enumerate([(t_in, 20), (t_prev, 10), (t_old, 5)], 1):
                c.execute("INSERT INTO dwell_segments(uid,visit,seq,screen,tool,seconds,ts,day) VALUES('owner','v',?,'home','',?,?,?)",
                          (seq, sec, ts, ts[:10]))   # day 는 예전처럼 UTC 날짜

        class Ctx:
            uid, is_owner, raw = "owner", True, "/api/admin/dwell?days=7"
            def send(self, body, ctype="application/json", code=200, headers=None):
                return code, body
        code, body = self.dwell.report(Ctx())
        self.assertEqual(code, 200)
        self.assertEqual(body["to"], today.isoformat())
        daily = {r["day"]: r["seconds"] for r in body["daily"]}
        self.assertEqual(daily, {today.isoformat(): 20, (today - datetime.timedelta(days=1)).isoformat(): 10})
        self.assertEqual(body["active_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
