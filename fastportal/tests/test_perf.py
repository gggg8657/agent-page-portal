"""빠른 성능 개선(2026-10-20 부하 시험 후속) — SQLite 연결 재사용, 세션 확인 캐시와 무효화(로그아웃·PIN 변경·초기화·비활성화·이름),
/api/tools 집계 캐시(공통 집계는 캐시, 계정별 liked 는 계정마다), 스레드 풀 상한 env, 미리 압축(.gz) 자산.
통합 시험은 임시 데이터 폴더로 포털을 따로 띄움(운영 _data 를 건드리지 않음)."""
import gzip, json, os, sqlite3, tempfile, threading, time, unittest
from unittest.mock import patch

from fastportal import config, db as dbm, pages, sessions, tools
from fastportal.tests.test_guard import VENV, Portal


class DbReuseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db"))
        self.p.start()
        dbm._TL.idle = []
        with dbm.db() as c:
            c.execute("CREATE TABLE t(x)")

    def tearDown(self):
        for c, _ in getattr(dbm._TL, "idle", []):
            c.close()
        dbm._TL.idle = []
        self.p.stop()
        self.tmp.cleanup()

    def test_reuse_same_thread(self):
        with dbm.db() as a:
            pass
        with dbm.db() as b:
            pass
        self.assertIs(a, b, "같은 스레드는 쉬는 연결을 다시 씀")

    def test_nested_is_separate_transaction(self):
        """요청 트랜잭션 안에서 따로 commit 하는 일(sessions.add_fail — 바깥은 그때 쓰기 잠금이 없음)은 다른 연결 — 바깥이 rollback 돼도 안쪽은 남음"""
        with self.assertRaises(ValueError):
            with dbm.db() as outer:
                outer.execute("SELECT COUNT(*) FROM t").fetchone()
                with dbm.db() as inner:
                    self.assertIsNot(inner, outer)
                    inner.execute("INSERT INTO t VALUES('inner')")
                raise ValueError
        with dbm.db() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT x FROM t")], ["inner"])

    def test_rollback_and_row_factory(self):
        with self.assertRaises(KeyError):
            with dbm.db() as c:
                c.execute("INSERT INTO t VALUES(1)")
                raise KeyError
        with dbm.db() as c:
            self.assertFalse(c.in_transaction)
            self.assertEqual(c.execute("SELECT COUNT(*) n FROM t").fetchone()["n"], 0)

    def test_other_thread_other_connection(self):
        with dbm.db() as a:
            pass
        got = []

        def run():
            with dbm.db() as c:
                got.append(id(c))
            for x, _ in dbm._TL.idle:
                x.close()
        th = threading.Thread(target=run)
        th.start(); th.join()
        self.assertNotEqual(got[0], id(a))

    def test_db_file_replaced(self):
        with dbm.db() as a:
            pass
        os.remove(config.DB_FILE)
        sqlite3.connect(config.DB_FILE).execute("CREATE TABLE z(y)").connection.close()
        with dbm.db() as b:
            self.assertIsNot(a, b, "파일이 바뀌면 새 연결")
            self.assertEqual(b.execute("SELECT name FROM sqlite_master").fetchone()[0], "z")


class SessionCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(sessions, "CACHE_S", 30.0)]
        for p in self.p:
            p.start()
        dbm._TL.idle = []
        sessions.cache_clear()
        dbm.init_db()
        with dbm.db() as c:
            c.execute("INSERT INTO users(id, name) VALUES('alice', '앨리스')")
            self.tok = sessions.new_session(c, "alice", True)

    def tearDown(self):
        sessions.cache_clear()
        for c, _ in getattr(dbm._TL, "idle", []):
            c.close()
        dbm._TL.idle = []
        for p in reversed(self.p):
            p.stop()
        self.tmp.cleanup()

    def _q(self):
        return sessions.session_user(self.tok)[0]

    def test_cache_hit_skips_db(self):
        self.assertEqual(self._q(), "alice")
        with patch.object(sessions, "db", side_effect=AssertionError("DB 조회하면 안 됨")):
            self.assertEqual(self._q(), "alice")

    def test_logout_invalidates(self):
        self.assertEqual(self._q(), "alice")
        sessions.end_session("agsid=" + self.tok)
        self.assertIsNone(self._q())

    def test_drop_user_sessions_invalidates(self):   # PIN 변경·PIN 초기화·비활성화·익명화가 모두 이 길
        self.assertEqual(self._q(), "alice")
        with dbm.db() as c:
            sessions.drop_user_sessions(c, "alice")
        self.assertIsNone(self._q())

    def test_disable_via_accounts(self):
        from fastportal import accounts
        self.assertEqual(self._q(), "alice")
        with dbm.db() as c:
            accounts.deactivate(c, "boss", "alice", "시험")
        self.assertIsNone(self._q())

    def test_no_recache_during_uncommitted_drop(self):
        """drop 이 커밋되기 전 다른 연결이 옛 세션을 읽어도 캐시에는 넣지 않음 → 커밋 뒤 바로 None"""
        self.assertEqual(self._q(), "alice")
        c = dbm._connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            sessions.drop_user_sessions(c, "alice")
            self.assertEqual(self._q(), "alice", "커밋 전에는 아직 유효(WAL 읽기)")
            c.commit()
        finally:
            c.close()
        self.assertIsNone(self._q())

    def test_external_change_within_ttl(self):
        """다른 프로세스가 지운 세션: TTL 이 지나면 반영"""
        with patch.object(sessions, "CACHE_S", 0.2):
            self.assertEqual(self._q(), "alice")
            c = sqlite3.connect(config.DB_FILE)
            c.execute("DELETE FROM sessions"); c.commit(); c.close()
            time.sleep(0.25)
            self.assertIsNone(self._q())

    def test_disabled_off(self):
        with patch.object(sessions, "CACHE_S", 0):
            self.assertEqual(self._q(), "alice")
            with patch.object(sessions, "db", side_effect=AssertionError):
                with self.assertRaises(AssertionError):
                    self._q()


class ToolsStatsCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(tools, "STATS_TTL", 60.0),
                  patch.object(tools, "tools", return_value=[{"dir": "a-local", "port": 1}])]
        for p in self.p:
            p.start()
        dbm._TL.idle = []
        tools.stats_reset()
        dbm.init_db()
        with dbm.db() as c:
            c.execute("INSERT INTO users(id, name) VALUES('u1','하나'),('u2','둘')")
            c.execute("INSERT INTO posts(title, body, author, dept, tags, ts) VALUES('t','b','하나','', '[\"#x\"]', '2026-10-20')")
            c.execute("INSERT INTO post_likes VALUES(1, 'u1')")
            c.execute("INSERT INTO events VALUES('open','a-local','2026-10-20T00:00:00','u1')")

    def tearDown(self):
        tools.stats_reset()
        for c, _ in getattr(dbm._TL, "idle", []):
            c.close()
        dbm._TL.idle = []
        for p in reversed(self.p):
            p.stop()
        self.tmp.cleanup()

    def _home(self, uid):
        with dbm.db() as c:
            return tools.home_data(c, {}, uid)

    def test_liked_per_user_and_same_shape(self):
        a, b = self._home("u1"), self._home("u2")
        self.assertTrue(a["posts"][0]["liked"])
        self.assertFalse(b["posts"][0]["liked"], "liked 는 계정마다(캐시된 공통 부분과 섞이지 않음)")
        self.assertEqual(a["posts"][0]["likes"], 1)
        self.assertEqual(a["posts"][0]["tags"], ["#x"])
        with dbm.db() as c:   # 예전 계산과 같은 모양·값
            old = [dict(r) | {"tags": json.loads(r["tags"] or "[]"), "liked": bool(r["liked"])} for r in
                   c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes, "
                             "(SELECT COUNT(*) FROM post_likes WHERE post=p.id AND user=?) liked FROM posts p ORDER BY id DESC LIMIT 50", ("u2",))]
        self.assertEqual(b["posts"], old)
        self.assertEqual(list(b["posts"][0]), list(old[0]), "키 순서도 같음")
        self.assertEqual(a["tools"][0]["usage"]["total"], 1)

    def test_cache_and_reset(self):
        self._home("u1")
        with dbm.db() as c:
            c.execute("INSERT INTO events VALUES('open','a-local','2026-10-20T00:00:01','u2')")
            c.execute("INSERT INTO post_likes VALUES(1, 'u2')")
        h = self._home("u2")
        self.assertEqual(h["tools"][0]["usage"]["total"], 1, "TTL 안에서는 캐시")
        self.assertTrue(h["posts"][0]["liked"], "내 liked 는 캐시와 상관없이 바로")
        tools.stats_reset()
        h = self._home("u2")
        self.assertEqual(h["tools"][0]["usage"]["total"], 2)
        self.assertEqual(h["posts"][0]["likes"], 2)

    def test_mutation_safe(self):
        h = self._home("u1")
        h["posts"][0]["tags"].append("#y"); h["rank"].append({"x": 1}) if h["rank"] else None
        self.assertEqual(self._home("u1")["posts"][0]["tags"], ["#x"], "응답을 고쳐도 캐시는 그대로")


class AssetCacheTest(unittest.TestCase):
    def test_refresh_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.js")
            open(f, "w").write("one")
            self.assertEqual(pages._asset_bytes(f), b"one")
            self.assertEqual(pages._asset_bytes(f), b"one")
            open(f, "w").write("two!")
            os.utime(f, ns=(time.time_ns() + 10**9,) * 2)
            self.assertEqual(pages._asset_bytes(f), b"two!", "수정 시각·크기가 바뀌면 다시 읽음")
            with patch.object(pages, "ASSET_CACHE_MAX", 0):
                self.assertIsNone(pages._asset_bytes(f))


class GzipNegotiationTest(unittest.TestCase):
    def test_accepts(self):
        for v, want in (("gzip, deflate, br", True), ("br", False), ("", False), (None, False), ("gzip;q=0", False), ("gzip;q=0.5", True), ("*", True), ("identity", False)):
            self.assertEqual(pages.accepts_gzip(v), want, v)


@unittest.skipUnless(os.path.exists(os.path.join(VENV, "bin", "python")), "fastportal venv 없음")
class PerfHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P = Portal({"PORTAL_PROBE": "0", "PORTAL_THREADS": "4", "PORTAL_SESSION_CACHE_S": "30", "PORTAL_TOOLS_CACHE_S": "30"})
        root = os.path.join(cls.P.tmp, "root", "assets")
        for name in ("index-A1.js", "Shell-S1.js", "Admin-D1.js"):
            f = os.path.join(root, name)
            open(f, "w").write(f"/* {name} */ " + "console.log('x');" * 200)
            with open(f, "rb") as a, gzip.open(f + ".gz", "wb") as g:
                g.write(a.read())
        cls.alice = cls.P.login("alice", signup=True)
        cls.boss = cls.P.login("boss", signup=True, owner=True)

    @classmethod
    def tearDownClass(cls):
        cls.P.close()

    def test_thread_env_logged(self):
        log = open(os.path.join(self.P.tmp, "portal.log")).read()
        self.assertIn("threads=4/16", log)

    def test_gzip_served_and_gated(self):
        P = self.P
        st, h, b = P.req("GET", "/assets/index-A1.js", {"Accept-Encoding": "gzip"})
        self.assertEqual((st, h.get("content-encoding")), (200, "gzip"))
        self.assertIn("Accept-Encoding", h["vary"])
        self.assertTrue(gzip.decompress(b).startswith(b"/* index-A1.js */"))
        st, h2, b2 = P.req("GET", "/assets/index-A1.js")
        self.assertEqual(st, 200); self.assertNotIn("content-encoding", h2)
        self.assertTrue(b2.startswith(b"/* index-A1.js */"))
        self.assertNotEqual(h["etag"], h2["etag"], "압축본과 원본의 ETag 가 다름")
        self.assertEqual(P.req("GET", "/assets/index-A1.js", {"Accept-Encoding": "gzip", "If-None-Match": h["etag"]})[0], 304)
        # 권한은 원본 이름으로 그대로
        self.assertEqual(P.req("GET", "/assets/Shell-S1.js", {"Accept-Encoding": "gzip"})[0], 401)
        st, h, _ = P.req("GET", "/assets/Shell-S1.js", {"Accept-Encoding": "gzip"}, cookie=self.alice)
        self.assertEqual((st, h.get("content-encoding")), (200, "gzip"))
        self.assertTrue(h["cache-control"].startswith("private"))
        self.assertEqual(h["vary"], "Cookie, Accept-Encoding")
        self.assertEqual(P.req("GET", "/assets/Admin-D1.js", {"Accept-Encoding": "gzip"}, cookie=self.alice)[0], 404)
        self.assertEqual(P.req("GET", "/assets/Admin-D1.js", {"Accept-Encoding": "gzip"}, cookie=self.boss)[0], 200)
        # .gz 주소를 직접 부르면 404(권한 판단을 비켜 가지 못하게)
        for who in (None, self.alice, self.boss):
            self.assertEqual(P.req("GET", "/assets/Admin-D1.js.gz", cookie=who)[0], 404)

    def test_logout_then_401(self):
        ck = self.P.login("alice")
        self.assertEqual(self.P.req("GET", "/api/me", cookie=ck)[0], 200)
        self.assertEqual(self.P.req("GET", "/api/me", cookie=ck)[0], 200)   # 캐시에 듦
        self.assertEqual(self.P.req("POST", "/api/logout", {"Content-Type": "application/json"}, {}, cookie=ck)[0], 200)
        self.assertEqual(self.P.req("GET", "/api/me", cookie=ck)[0], 401, "로그아웃 바로 다음 요청부터 401(캐시 30초여도)")

    def test_pin_change_then_401(self):
        P = self.P
        ck = P.login("carol", signup=True)
        self.assertEqual(P.req("GET", "/api/me", cookie=ck)[0], 200)
        st, h, b = P.req("POST", "/api/pin", {"Content-Type": "application/json"}, {"pin": "4821", "new": "7395"}, cookie=ck)
        self.assertEqual(st, 200, b)
        self.assertEqual(P.req("GET", "/api/me", cookie=ck)[0], 401, "PIN 변경 → 옛 세션 바로 401")
        import re
        new = re.search(r"agsid=[^;]+", h["set-cookie"])[0]
        self.assertEqual(P.req("GET", "/api/me", cookie=new)[0], 200)

    def test_disable_and_reset_then_401(self):
        P = self.P
        for uid, path, body in (("dan", "/api/admin/disable", {"disabled": True}), ("erin", "/api/admin/reset", {})):
            ck = P.login(uid, signup=True)
            self.assertEqual(P.req("GET", "/api/me", cookie=ck)[0], 200)
            st, _, b = P.req("POST", path, {"Content-Type": "application/json"}, {"uid": uid, **body}, owner=True)
            self.assertEqual(st, 200, b)
            self.assertEqual(P.req("GET", "/api/me", cookie=ck)[0], 401, path)

    def test_tools_cache_like_visible(self):
        P = self.P
        st, _, _ = P.req("GET", "/api/tools", cookie=self.alice)
        self.assertEqual(st, 200)
        import json
        d = json.loads(P.req("GET", "/api/tools", cookie=self.alice)[2])
        if not d["tools"]:
            self.skipTest("tools.json 에 도구 없음")
        t = d["tools"][0]["dir"]
        before = d["tools"][0]["usage"]["total"]
        self.assertEqual(P.req("POST", "/api/open", {"Content-Type": "application/json"}, {"dir": t}, cookie=self.alice)[0], 200)
        d = json.loads(P.req("GET", "/api/tools", cookie=self.alice)[2])
        self.assertEqual(next(x for x in d["tools"] if x["dir"] == t)["usage"]["total"], before + 1, "이 포털에서 연 것은 캐시 TTL 과 상관없이 바로")


if __name__ == "__main__":
    unittest.main()
