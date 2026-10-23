"""커뮤니티 댓글(comments.py) — 확인된 사용자만(소속 승인·대기 없음·관리자, 403 not_verified), 볼 수 있는 대상만(글·요청·리뷰),
고치기 = 쓴 사람·지우기 = 쓴 사람·관리자·숨기기 = 관리자·신고 한 번, 목록 카드 댓글 수, 계정 id 안 나감, 공지는 관리자만, 대상 지우면 댓글도."""
import json, os, tempfile, unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import modfilter, accounts, comments, community, config, posts
from fastportal.db import db

TOOLS = {"sql-local": {"dir": "sql-local", "port": 1, "name": "SQL"}}


class Sent:
    def __init__(self, body, code=200):
        self.body, self.code = body, code


def ctx(uid, owner=False, path=""):
    c = NS(uid=uid, uname=uid, agent_user=uid, agent_role="", is_owner=owner, p=path, raw=path)
    c.send = lambda body, ctype="application/json", code=200, headers=None: Sent(body, code)
    return c


class CommentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = []
        self.p = [patch.object(modfilter, "MOD_ON", False),   # 2단계(LLM) 거르기는 test_modfilter 에서만
                  patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(config, "DATA", self.tmp.name),
                  patch.object(community.tools, "tool", side_effect=lambda d: TOOLS.get(d)),
                  patch.object(community.toolstate, "is_off", return_value=False),
                  patch.object(comments.jobs, "upsert", side_effect=lambda *a, **k: self.jobs.append(a) or None)]
        for x in self.p: x.start()
        community._RATE.clear()
        with db() as c:
            c.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, unit_id INTEGER, disabled INTEGER DEFAULT 0, deactivated TEXT);
                CREATE TABLE requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT);
                CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
                CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
                CREATE TABLE post_likes(post INTEGER, user TEXT, UNIQUE(post, user));""")
            c.executemany("INSERT INTO users(id, name, dept, unit_id) VALUES(?,?,?,?)",
                          [("alice", "앨리스", "A", 1), ("bob", "밥", "A", 1), ("newbie", "새내기", "", None), ("mover", "옮기는이", "A", 1)])
            c.execute("INSERT INTO requests(kind, uid, dept, status) VALUES('dept_join', 'mover', 'B', 'pending')")
            c.execute("INSERT INTO requests(kind, uid, dept, status) VALUES('dept_join', 'newbie', 'A', 'pending')")
            community.init(c); posts.init(c); comments.init(c)
        self.pid = posts.draft(ctx("alice"), {})["id"]
        posts.save(ctx("alice"), {"id": self.pid, "title": "사례", "body": "본문", "cat": "활용사례"})

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def test_verified_rule(self):
        with db() as c:
            got = {u: accounts.is_verified(c, u) for u in ("alice", "newbie", "mover", "owner", "ghost")}
        self.assertEqual(got, {"alice": True, "newbie": False, "mover": False, "owner": True, "ghost": False})
        for who in ("newbie", "mover"):
            with self.subTest(who=who), self.assertRaises(comments.NotVerified):
                comments.write(ctx(who), {"kind": "post", "id": self.pid, "body": "x"})
        comments.write(ctx("bob"), {"kind": "post", "id": self.pid, "body": "좋아요"})
        comments.write(ctx("owner", True), {"kind": "post", "id": self.pid, "body": "관리자 댓글"})
        L = comments.listing(ctx("newbie"), "post", self.pid)
        self.assertEqual((len(L["comments"]), L["can_comment"]), (2, False)); self.assertIn("부서장 승인", L["not_verified"])
        self.assertTrue(comments.listing(ctx("bob"), "post", self.pid)["can_comment"])

    def test_not_verified_http_code(self):
        r = community.handle_post(ctx("newbie", path="/api/community/comment"), {"kind": "post", "id": self.pid, "body": "x"})
        self.assertEqual((r.code, r.body["code"]), (403, "not_verified"))
        r = community.handle_post(ctx("bob", path="/api/community/comment"), {"kind": "post", "id": self.pid, "body": "x"})
        self.assertEqual(r.code, 200)

    def test_edit_delete_hide_report_and_counts(self):
        a = comments.write(ctx("bob"), {"kind": "post", "id": self.pid, "body": "첫"})["id"]
        comments.write(ctx("alice"), {"kind": "post", "id": self.pid, "body": "답"})
        with self.assertRaises(PermissionError):
            comments.edit(ctx("alice"), {"id": a, "body": "남의 것"})
        with self.assertRaises(PermissionError):
            comments.edit(ctx("owner", True), {"id": a, "body": "관리자도 못 고침"})
        comments.edit(ctx("bob"), {"id": a, "body": "고친 첫"})
        L = comments.listing(ctx("alice"), "post", self.pid)["comments"]
        self.assertEqual([(x["author"], x["body"], x["mine"]) for x in L], [("밥", "고친 첫", False), ("앨리스", "답", True)])
        self.assertNotIn("bob", json.dumps(L, ensure_ascii=False))   # 이름은 보여도 계정 id 는 안 나감
        self.assertEqual(next(p for p in posts.listing(ctx("bob"))["posts"] if p["id"] == self.pid)["comments"], 2)
        self.assertEqual(posts.get(ctx("bob"), self.pid)["post"]["comments"], 2)
        with self.assertRaises(PermissionError):
            comments.report(ctx("bob"), {"id": a, "reason": "내 것"})
        self.assertFalse(comments.report(ctx("alice"), {"id": a, "reason": "광고"})["already"])
        self.assertTrue(comments.report(ctx("alice"), {"id": a, "reason": "광고"})["already"])
        self.assertEqual(len(self.jobs), 1)
        with self.assertRaises(PermissionError):
            comments.hide(ctx("alice"), {"id": a})
        comments.hide(ctx("owner", True), {"id": a, "hidden": True})
        self.assertEqual(len(comments.listing(ctx("alice"), "post", self.pid)["comments"]), 1)
        self.assertEqual(len(comments.listing(ctx("bob"), "post", self.pid)["comments"]), 2)   # 쓴 사람은 숨긴 것도 봄
        self.assertEqual(posts.get(ctx("bob"), self.pid)["post"]["comments"], 1)
        with self.assertRaises(PermissionError):
            comments.delete(ctx("alice"), {"id": a})
        comments.delete(ctx("owner", True), {"id": a})
        posts.delete(ctx("alice"), {"id": self.pid})   # 글을 지우면 댓글도
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM comments").fetchone()[0], 0)

    def test_targets_visibility_requests_reviews(self):
        rq = community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", "type": "bug", "title": "오류", "body": "b"})["id"]
        rv = community.write_review(ctx("bob"), {"kind": "tool", "id": "sql-local", "stars": 4})["id"]
        comments.write(ctx("alice"), {"kind": "request", "id": rq, "body": "저도요"})
        comments.write(ctx("alice"), {"kind": "review", "id": rv, "body": "동의"})
        items = posts.recent(ctx("bob"))["items"]
        self.assertEqual({(x["type"], x["comments"]) for x in items}, {("request", 1), ("review", 1)})
        with self.assertRaises(FileNotFoundError):
            comments.listing(ctx("bob"), "request", "nope123")
        d = posts.draft(ctx("alice"), {})["id"]   # 초안에는 남이 못 담
        with self.assertRaises(FileNotFoundError):
            comments.write(ctx("bob"), {"kind": "post", "id": d, "body": "x"})
        community.delete_request(ctx("bob"), {"id": rq})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM comments WHERE kind='request'").fetchone()[0], 0)

    def test_notice_admin_only_and_verified_post_flag(self):
        d = posts.draft(ctx("bob"), {})["id"]
        with self.assertRaises(PermissionError):
            posts.save(ctx("bob"), {"id": d, "title": "공지", "body": "x", "cat": "공지"})
        posts.save(ctx("newbie"), {"id": posts.draft(ctx("newbie"), {})["id"], "title": "질문", "body": "x", "cat": "질문"})   # 기본: 일반 글은 누구나
        with patch.object(config, "COMMUNITY_POST_VERIFIED_ONLY", True):
            with self.assertRaises(comments.NotVerified):
                posts.save(ctx("newbie"), {"id": posts.draft(ctx("newbie"), {})["id"], "title": "질문", "body": "x", "cat": "질문"})
            posts.save(ctx("bob"), {"id": d, "title": "질문", "body": "x", "cat": "질문"})

    def test_rate_limit(self):
        with patch.object(comments, "COMMENT_RATE", 2):
            for i in range(2):
                comments.write(ctx("bob"), {"kind": "post", "id": self.pid, "body": str(i)})
            with self.assertRaises(ValueError):
                comments.write(ctx("bob"), {"kind": "post", "id": self.pid, "body": "x"})


if __name__ == "__main__":
    unittest.main()
