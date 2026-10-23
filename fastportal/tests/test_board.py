"""자유게시판(board.py) — 가명 번호(글마다·같은 사람 같은 번호·글쓴이), 응답에 계정·이름이 안 나옴, 규칙 동의, 고치기·지우기 권한,
속도 제한(글·댓글), 서로 다른 신고자 N 명 자동 숨김(관리자 설정), 작성자 확인 = 관리자 + 신고 + 이유 + 감사, 쓰기는 감사에 안 남음."""
import json, os, tempfile, unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import modfilter, board, comments, community, config
from fastportal.db import db

NAMES = {"alice": "앨리스", "bob": "밥", "carol": "캐롤", "dave": "데이브", "owner": "관리자"}


def ctx(uid, owner=False):
    return NS(uid=uid, uname=NAMES.get(uid, uid), agent_user=uid, agent_role="", is_owner=owner)


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = []
        self.p = [patch.object(modfilter, "MOD_ON", False),   # 2단계(LLM) 거르기는 test_modfilter 에서만
                  patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(board.jobs, "upsert", side_effect=lambda *a, **k: self.jobs.append(a) or None)]
        for x in self.p: x.start()
        community._RATE.clear()
        with db() as c:
            c.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, unit_id INTEGER, disabled INTEGER DEFAULT 0, deactivated TEXT);
                CREATE TABLE requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT);
                CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);""")
            c.executemany("INSERT INTO users(id, name, dept, unit_id) VALUES(?,?,?,?)", [(k, v, "A", 1) for k, v in NAMES.items()])
            c.execute("INSERT INTO users(id, name, dept) VALUES('eve', '이브', 'A')")
            board.init(c); comments.init(c)
        for u in ("alice", "bob", "carol", "dave"):
            board.agree(ctx(u), {"version": board.RULES_VERSION})

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def leak(self, obj, uids=("alice", "bob", "carol", "dave")):
        s = json.dumps(obj, ensure_ascii=False)
        for u in uids:
            self.assertNotIn(u, s, f"계정 {u} 가 응답에 나옴")
            self.assertNotIn(NAMES[u], s, f"이름 {NAMES[u]} 가 응답에 나옴")

    def test_pseudonyms_stable_per_post_and_no_leak(self):
        p1 = board.save(ctx("alice"), {"title": "첫 글", "body": "본문"})["id"]
        p2 = board.save(ctx("bob"), {"title": "둘째 글", "body": "본문"})["id"]
        self.assertEqual(board.comment(ctx("bob"), {"post": p1, "body": "b1"})["author"], "익명 1")
        self.assertEqual(board.comment(ctx("carol"), {"post": p1, "body": "c1"})["author"], "익명 2")
        self.assertEqual(board.comment(ctx("bob"), {"post": p1, "body": "b2"})["author"], "익명 1")   # 같은 사람 같은 번호
        self.assertEqual(board.comment(ctx("alice"), {"post": p1, "body": "a"})["author"], "익명(글쓴이)")
        self.assertEqual(board.comment(ctx("carol"), {"post": p2, "body": "c"})["author"], "익명 1")   # 글마다 따로
        g = board.get(ctx("dave"), p1)
        self.assertEqual([x["author"] for x in g["comments"]], ["익명 1", "익명 2", "익명 1", "익명(글쓴이)"])
        self.assertEqual(g["post"]["author"], "익명(글쓴이)")
        self.assertFalse(g["post"]["mine"]); self.assertTrue(board.get(ctx("alice"), p1)["post"]["mine"])
        cid = g["comments"][0]["id"]
        board.delete_comment(ctx("bob"), {"id": cid})
        self.assertEqual(board.comment(ctx("bob"), {"post": p1, "body": "b3"})["author"], "익명 1")   # 지워도 번호 유지
        for who in ("alice", "bob", "dave"):
            self.leak(board.listing(ctx(who))); self.leak(board.get(ctx(who), p1)); self.leak(board.get(ctx(who), p2))
        self.leak(board.listing(ctx("owner", True))); self.leak(board.get(ctx("owner", True), p1))
        board.report(ctx("dave"), {"kind": "post", "id": p1, "reason": "비방"})
        self.leak(board.reports(ctx("owner", True)))
        self.leak(self.jobs)
        with db() as c:   # 쓰기는 감사 기록에도 안 남음(actor 로 작성자가 드러나므로)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE actor IN ('alice','bob','carol')").fetchone()[0], 0)

    def test_agree_required(self):
        with self.assertRaises(PermissionError):
            board.save(ctx("eve"), {"title": "t", "body": "b"})
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        with self.assertRaises(PermissionError):
            board.comment(ctx("eve"), {"post": p, "body": "c"})
        self.assertFalse(board.listing(ctx("eve"))["agreed"])
        with self.assertRaises(ValueError):
            board.agree(ctx("eve"), {"version": 0})
        board.agree(ctx("eve"), {"version": board.RULES_VERSION})
        self.assertTrue(board.listing(ctx("eve"))["agreed"])
        board.comment(ctx("eve"), {"post": p, "body": "c"})

    def test_edit_delete_permissions(self):
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        with self.assertRaises(PermissionError):
            board.save(ctx("bob"), {"id": p, "title": "x", "body": "y"})
        board.agree(ctx("owner", True), {"version": board.RULES_VERSION})
        with self.assertRaises(PermissionError):   # 관리자도 남의 글은 못 고침(숨기기·지우기만)
            board.save(ctx("owner", True), {"id": p, "title": "x", "body": "y"})
        board.save(ctx("alice"), {"id": p, "title": "고침", "body": "y"})
        self.assertEqual(board.get(ctx("bob"), p)["post"]["title"], "고침")
        c = board.comment(ctx("bob"), {"post": p, "body": "c"})["id"]
        with self.assertRaises(PermissionError):
            board.delete_comment(ctx("alice"), {"id": c})
        with self.assertRaises(PermissionError):
            board.delete(ctx("bob"), {"id": p})
        board.delete(ctx("alice"), {"id": p})
        with self.assertRaises(FileNotFoundError):
            board.get(ctx("alice"), p)

    def test_rate_limits(self):
        with patch.object(board, "POST_RATE", 2), patch.object(board, "COMMENT_RATE", 3):
            p = board.save(ctx("alice"), {"title": "1", "body": "b"})["id"]
            board.save(ctx("alice"), {"title": "2", "body": "b"})
            with self.assertRaises(ValueError):
                board.save(ctx("alice"), {"title": "3", "body": "b"})
            board.save(ctx("alice"), {"id": p, "title": "고치기는 됨", "body": "b"})
            for i in range(3):
                board.comment(ctx("bob"), {"post": p, "body": str(i)})
            with self.assertRaises(ValueError):
                board.comment(ctx("bob"), {"post": p, "body": "x"})
            board.comment(ctx("carol"), {"post": p, "body": "다른 사람은 됨"})

    def test_autohide_distinct_reporters_and_setting(self):
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        with self.assertRaises(PermissionError):
            board.report(ctx("alice"), {"kind": "post", "id": p, "reason": ""})
        self.assertFalse(board.report(ctx("bob"), {"kind": "post", "id": p, "reason": "1"})["hidden"])
        self.assertTrue(board.report(ctx("bob"), {"kind": "post", "id": p, "reason": "again"})["already"])   # 같은 사람은 한 번
        self.assertFalse(board.report(ctx("carol"), {"kind": "post", "id": p, "reason": "2"})["hidden"])
        self.assertTrue(board.report(ctx("dave"), {"kind": "post", "id": p, "reason": "3"})["hidden"])   # 서로 다른 3명
        with self.assertRaises(FileNotFoundError):
            board.get(ctx("bob"), p)
        self.assertTrue(board.get(ctx("alice"), p)["post"]["hidden"])   # 쓴 사람은 봄
        self.assertNotIn(p, [x["id"] for x in board.listing(ctx("bob"))["posts"]])
        with self.assertRaises(PermissionError):
            board.settings(ctx("bob"), {"autohide": 1})
        board.settings(ctx("owner", True), {"autohide": 1})
        q = board.save(ctx("alice"), {"title": "t2", "body": "b"})["id"]
        c = board.comment(ctx("alice"), {"post": q, "body": "댓글"})["id"]
        self.assertTrue(board.report(ctx("bob"), {"kind": "comment", "id": c, "reason": "x"})["hidden"])
        self.assertEqual(board.get(ctx("carol"), q)["comments"], [])
        board.hide(ctx("owner", True), {"kind": "comment", "id": c, "hidden": False})
        self.assertEqual(len(board.get(ctx("carol"), q)["comments"]), 1)

    def test_reveal_needs_admin_report_reason_and_is_audited(self):
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        with self.assertRaises(PermissionError):
            board.reveal(ctx("owner", True), {"kind": "post", "id": p, "reason": "확인 필요함"})   # 신고 없음
        board.report(ctx("bob"), {"kind": "post", "id": p, "reason": "비방"})
        with self.assertRaises(PermissionError):
            board.reveal(ctx("bob"), {"kind": "post", "id": p, "reason": "궁금해서요"})   # 관리자 아님
        for bad in ("", "짧음"):
            with self.subTest(reason=bad), self.assertRaises(ValueError):
                board.reveal(ctx("owner", True), {"kind": "post", "id": p, "reason": bad})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='board_reveal'").fetchone()[0], 0)
        r = board.reveal(ctx("owner", True), {"kind": "post", "id": p, "reason": "비방 신고 확인"})
        self.assertEqual((r["uid"], r["name"]), ("alice", "앨리스"))
        with db() as c:
            a = c.execute("SELECT actor, target, detail, ts FROM audit WHERE action='board_reveal'").fetchall()
        self.assertEqual([(x[0], x[1], x[2]) for x in a], [("owner", f"board:post:{p}", "비방 신고 확인")])
        self.assertTrue(a[0][3])

    def test_unverified_cannot_write_and_comment_edit(self):
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        with db() as c:
            c.execute("INSERT INTO users(id, name, dept) VALUES('frank', '프랭크', '')")   # 소속 없음
            c.execute("INSERT INTO requests(kind, uid, dept, status) VALUES('dept_join', 'carol', 'B', 'pending')")   # 옮기는 중(대기)
        for who in ("frank", "carol"):
            board.agree(ctx(who), {"version": board.RULES_VERSION})
            for f, a in ((board.save, {"title": "t", "body": "b"}), (board.comment, {"post": p, "body": "c"})):
                with self.subTest(who=who, f=f.__name__), self.assertRaises(comments.NotVerified):
                    f(ctx(who), a)
        self.assertFalse(board.get(ctx("frank"), p)["verified"]); self.assertTrue(board.get(ctx("bob"), p)["verified"])
        cid = board.comment(ctx("bob"), {"post": p, "body": "처음"})["id"]
        with self.assertRaises(PermissionError):
            board.edit_comment(ctx("alice"), {"id": cid, "body": "남의 것"})
        board.edit_comment(ctx("bob"), {"id": cid, "body": "고침"})
        c0 = board.get(ctx("dave"), p)["comments"][0]
        self.assertEqual((c0["body"], c0["author"]), ("고침", "익명 1")); self.assertTrue(c0["updated"])

    def test_text_limits_and_bad_input(self):
        for bad in ({"title": "", "body": "b"}, {"title": "t", "body": ""}, {"title": "가" * 81, "body": "b"}, {"title": 1, "body": "b"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                board.save(ctx("alice"), bad)
        with self.assertRaises(FileNotFoundError):
            board.get(ctx("alice"), "x")
        with self.assertRaises(ValueError):
            board.report(ctx("bob"), {"kind": "user", "id": 1, "reason": ""})


if __name__ == "__main__":
    unittest.main()
