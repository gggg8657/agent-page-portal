"""첫 로그인 둘러보기 기록 — onboarded(새 가입자 자동 시작)·tour_seen(기존 계정에 '새 기능 둘러보기' 안내를 판마다 한 번).
마이그레이션이 기존 계정을 반복해서 귀찮게 하지 않는지, done=false 로 다음 로그인 때 다시 뜨게 할 수 있는지."""
import os, sqlite3, tempfile, unittest
from unittest.mock import patch

from fastportal import accounts, config, db


class Ctx:
    def __init__(self, uid):
        self.uid, self.uname, self.is_owner = uid, uid, False
    def send(self, body, ctype="application/json", code=200, headers=None):
        return code, body


class TourTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.f = os.path.join(self.tmp.name, "portal.db")
        self.p = patch.object(config, "DB_FILE", self.f)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def me(self, uid):
        with db.db() as c:
            return accounts.me(c, uid, uid)

    def post(self, uid, body):
        with db.db() as c:
            return accounts.manage_post(Ctx(uid), c, "/api/account/onboarded", body)

    def old_db(self):
        """onboarded 는 있고 tour_seen 은 없던 때의 DB(기존 계정 2명 — 둘러보기 마친 사람·아직 안 한 사람)"""
        c = sqlite3.connect(self.f)
        c.execute("CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, salt BLOB, hash BLOB, ts TEXT, iters INTEGER DEFAULT 200000, "
                  "disabled INTEGER DEFAULT 0, seen TEXT, theme TEXT, onboarded INTEGER DEFAULT 0)")
        c.execute("INSERT INTO users(id,name,dept,ts,onboarded) VALUES('old','Old','','2026-01-01',1)")
        c.execute("INSERT INTO users(id,name,dept,ts,onboarded) VALUES('fresh','Fresh','','2026-01-01',0)")
        c.commit(); c.close()

    def test_migration_prompts_existing_once(self):
        self.old_db()
        db.init_db()
        m = self.me("old")
        self.assertTrue(m["onboarded"], "기존 계정의 onboarded 는 그대로(둘러보기가 자동으로 다시 뜨지 않음)")
        self.assertLess(m["tour_seen"], m["tour_version"], "기존 계정은 이 판의 '새 기능 둘러보기' 안내를 받음")
        self.assertEqual(self.post("old", {"done": True})[0], 200)
        m = self.me("old")
        self.assertEqual(m["tour_seen"], config.TOUR_VERSION, "닫으면 이 판은 본 것으로")
        db.init_db()   # 재시작(마이그레이션 다시) — 다시 0 으로 돌리면 안 됨
        self.assertEqual(self.me("old")["tour_seen"], config.TOUR_VERSION)
        self.assertFalse(self.me("fresh")["onboarded"], "아직 안 한 계정은 그대로 자동 시작")

    def test_new_user_and_replay(self):
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('new','New','','2026-10-20')")   # auth/local.py create_user 와 같은 열(기본값 사용)
        m = self.me("new")
        self.assertFalse(m["onboarded"], "새 가입자는 둘러보기 자동 시작")
        self.post("new", {"done": True})
        m = self.me("new")
        self.assertTrue(m["onboarded"])
        self.assertEqual(m["tour_seen"], m["tour_version"], "둘러보기를 마치면 '새 기능' 안내는 안 뜸")
        self.post("new", {"done": False})   # 계정 설정 '다음 로그인 때 둘러보기 다시 보이기'
        m = self.me("new")
        self.assertFalse(m["onboarded"])
        self.assertEqual(m["tour_seen"], config.TOUR_VERSION, "다시 보이기는 판 기록을 지우지 않음")

    def test_version_bump_prompts_again_once(self):
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts,onboarded,tour_seen) VALUES('u','U','','2026-10-20',1,?)", (config.TOUR_VERSION,))
        self.assertEqual(self.me("u")["tour_seen"], self.me("u")["tour_version"])
        with patch.object(config, "TOUR_VERSION", config.TOUR_VERSION + 1):
            m = self.me("u")
            self.assertLess(m["tour_seen"], m["tour_version"])
            self.post("u", {"done": True})
            m = self.me("u")
            self.assertEqual(m["tour_seen"], m["tour_version"])

    def test_local_no_account(self):
        db.init_db()
        m = self.me("owner")
        self.assertFalse(m["account"])
        self.assertTrue(m["onboarded"])
        self.assertEqual(m["tour_seen"], m["tour_version"], "계정 없는 로컬 접속엔 안내를 안 띄움")

    def tut(self, uid, body):
        with db.db() as c:
            return accounts.manage_post(Ctx(uid), c, "/api/account/tutorial", body)

    def test_tutorial_seen_per_account(self):
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('a','A','','2026-10-20')")
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('b','B','','2026-10-20')")
        self.assertEqual(self.me("a")["tutorials"], {}, "처음엔 본 따라 하기 없음 → 도구 첫 방문에 자동 시작")
        code, r = self.tut("a", {"id": "chart", "version": 1})
        self.assertEqual((code, r["tutorials"]), (200, {"chart": 1}))
        self.tut("a", {"id": "chart", "version": 1})   # 같은 것 다시 — 그대로
        self.tut("a", {"id": "mail"})                  # version 생략 = 1
        self.assertEqual(self.me("a")["tutorials"], {"chart": 1, "mail": 1})
        self.assertEqual(self.me("b")["tutorials"], {}, "다른 계정과 섞이지 않음")
        self.tut("a", {"id": "chart", "version": 2})
        self.tut("a", {"id": "chart", "version": 1})   # 낮은 판으로 되돌리지 않음
        self.assertEqual(self.me("a")["tutorials"]["chart"], 2)
        self.tut("a", {"reset": True})                 # 계정 설정 '사용법 진행 기록 초기화'
        self.assertEqual(self.me("a")["tutorials"], {})
        for bad in ({"id": "../x"}, {"id": ""}, {"id": "chart", "version": 0}, {"id": "chart", "version": "1"}, {"id": "chart", "version": True}, {"id": "x" * 41}):
            with self.assertRaises(ValueError):
                self.tut("a", bad)
        self.assertIsNone(self.me("owner")["tutorials"], "계정 없는 로컬 접속은 기록 없음(자동 시작 안 함)")

    def test_used_tools(self):
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('a','A','','2026-10-20')")
            c.executemany("INSERT INTO events VALUES(?,?,?,?)", [("open", "chart-local", "2026-10-20", "a"), ("open", "chart-local", "2026-10-20", "a"),
                                                                  ("like", "mail-local", "2026-10-20", "a"), ("open", "kordoc-local", "2026-10-20", "b")])
        self.assertEqual(self.me("a")["used"], ["chart-local"], "연 적 있는 도구만(좋아요·남의 기록 제외)")

    def test_tutorial_seen_concurrent(self):
        import threading
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts) VALUES('a','A','','2026-10-20')")
        ids = [f"t{i}" for i in range(30)]
        ths = [threading.Thread(target=self.tut, args=("a", {"id": i})) for i in ids]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.assertEqual(sorted(self.me("a")["tutorials"]), sorted(ids), "동시에 보낸 기록이 하나도 사라지지 않음")

    def test_tutorial_seen_corrupt(self):
        db.init_db()
        with db.db() as c:
            c.execute("INSERT INTO users(id,name,dept,ts,tut_seen) VALUES('a','A','','2026-10-20','not json')")
        self.assertEqual(self.me("a")["tutorials"], {})
        self.tut("a", {"id": "chart"})
        self.assertEqual(self.me("a")["tutorials"], {"chart": 1})


if __name__ == "__main__":
    unittest.main()
