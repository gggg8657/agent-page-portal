"""거르기(modfilter.py)·답글 — 1단계 욕설: 정규화 변형(씨 발·시.발·ㅅ1ㅂ·숫자)·예외(시발점·18세·3시 발표)·관리자 목록 · 2단계: 지칭 후보,
보류 저장(쓴 사람만 '확인 중')·관리자 승인/삭제, fail-open/closed·끔, 판정 기록, 422 code=profanity · 답글: 한 단, 다른 글·답글의 답글 400,
답글 남은 댓글 지우면 자리만, 댓글 수에 답글 포함, 자유게시판 답글 가명·계정 안 나감. LLM 은 가짜."""
import json, os, tempfile, unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import board, comments, community, config, modfilter, posts
from fastportal.db import db

NAMES = {"alice": "앨리스", "bob": "밥", "carol": "캐롤", "owner": "관리자"}


class Sent:
    def __init__(self, body, code=200):
        self.body, self.code = body, code


def ctx(uid, owner=False, path=""):
    c = NS(uid=uid, uname=NAMES.get(uid, uid), agent_user=uid, agent_role="", is_owner=owner, p=path, raw=path)
    c.send = lambda body, ctype="application/json", code=200, headers=None: Sent(body, code)
    return c


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.llm = {"v": {"targets_person": False, "defamatory": False, "reason": "일반 글"}, "calls": []}
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(config, "DATA", self.tmp.name),
                  patch.object(modfilter, "_llm", side_effect=self.fake_llm), patch.object(modfilter.jobs, "upsert", return_value=None),
                  patch.object(comments.jobs, "upsert", return_value=None)]
        for x in self.p: x.start()
        community._RATE.clear()
        with db() as c:
            c.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, unit_id INTEGER, disabled INTEGER DEFAULT 0, deactivated TEXT);
                CREATE TABLE requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT);
                CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
                CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
                CREATE TABLE post_likes(post INTEGER, user TEXT, UNIQUE(post, user));
                CREATE TABLE org_units(id TEXT PRIMARY KEY, name TEXT, active INTEGER DEFAULT 1);""")
            c.executemany("INSERT INTO users(id, name, dept, unit_id) VALUES(?,?,?,?)", [(k, v, "A", 1) for k, v in NAMES.items()] + [("hgd", "홍길동", "A", 1)])
            c.execute("INSERT INTO org_units VALUES('u1', '가상기술실', 1)")
            community.init(c); posts.init(c); comments.init(c); board.init(c)
        for u in ("alice", "bob", "carol", "owner"):
            board.agree(ctx(u, u == "owner"), {"version": board.RULES_VERSION})

    def fake_llm(self, user):
        self.llm["calls"].append(user)
        v = self.llm["v"]
        if isinstance(v, Exception):
            raise v
        return v, "fake-model"

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def post(self, who="alice", title="질문 있어요", body="도구 쓰는 법이 궁금합니다", cat="질문"):
        pid = posts.draft(ctx(who), {})["id"]
        return pid, posts.save(ctx(who, who == "owner"), {"id": pid, "title": title, "body": body, "cat": cat})


class ProfanityTest(Base):
    def test_variants_blocked(self):
        for t in ("씨발 진짜", "씨 발", "시.발", "시1발", "ㅅㅂ 뭐야", "ㅅ1ㅂ", "ㅅ ㅂ", "개 새 끼", "병신같네", "18놈아", "tlqkf", "ㅄ", "존 나 짜증"):
            with self.subTest(t=t):
                self.assertTrue(modfilter.profanity(t), t)

    def test_exceptions_and_normal_text(self):
        for t in ("시발점부터 다시 봅시다", "18세 이상", "오후 3시 발표 자료", "사법 제도", "2018년 보고서", "씨 발아 실험", "병 신고 절차",
                  "등신대 사진", "회의 18분 남음", "이번 시바견 사진", "ㅎㅎ 좋아요", "ㅋㅋㅋ"):
            with self.subTest(t=t):
                self.assertEqual(modfilter.profanity(t), [], t)

    def test_mask_and_422(self):
        hits = modfilter.profanity("이 씨발 도구")
        self.assertTrue(hits and all("*" in h for h in hits) and not any("씨발" == h for h in hits))
        pid = posts.draft(ctx("alice"), {})["id"]
        r = community.handle_post(ctx("alice", path="/api/community/post/save"), {"id": pid, "title": "제목", "body": "씨발 안 되네", "cat": "질문"})
        self.assertEqual((r.code, r.body["code"]), (422, "profanity")); self.assertTrue(r.body["matches"])
        pub, _ = self.post()
        for path, body in (("/api/community/comment", {"kind": "post", "id": pub, "body": "ㅅㅂ"}),
                           ("/api/community/board/save", {"title": "병신", "body": "x"}),
                           ("/api/community/request", {"kind": "tool", "id": "x", "type": "bug", "title": "씨 발", "body": ""})):
            with self.subTest(path=path):
                with patch.object(community.tools, "tool", return_value={"dir": "x", "port": 1, "name": "X"}), patch.object(community.toolstate, "is_off", return_value=False):
                    r = community.handle_post(ctx("bob", path=path), body)
                self.assertEqual(r.code, 422, (path, r.body))

    def test_admin_lists(self):
        self.assertEqual(modfilter.profanity("가상욕설 테스트"), [])
        with self.assertRaises(PermissionError):
            modfilter.set_words(ctx("bob"), {"added": ["가상욕설"]})
        modfilter.set_words(ctx("owner", True), {"added": ["가상욕설"], "removed": ["졸라"], "allow": ["가상욕설없음"]})
        self.assertTrue(modfilter.profanity("가상 욕설 테스트"))
        self.assertEqual(modfilter.profanity("가상욕설없음"), [])
        self.assertEqual(modfilter.profanity("졸라 맛있다"), [])   # 관리자가 뺀 기본 낱말
        w = modfilter.get_words(ctx("owner", True))
        self.assertIn("가상욕설", w["added"]); self.assertIn("씨발", w["base"])
        self.assertTrue(os.path.exists(modfilter.words_file()))
        with open(modfilter.BASE_FILE, encoding="utf-8") as f:
            self.assertNotIn("가상욕설", f.read())   # 기본 파일(공개 저장소)은 안 바뀜


class HoldTest(Base):
    def defame(self):
        self.llm["v"] = {"targets_person": True, "defamatory": True, "reason": "특정인을 비방"}

    def test_hints_and_hold_flow(self):
        self.defame()
        pid, r = self.post(body="가상기술실 실장 홍길동은 무능하다")
        self.assertEqual((r["held"], r["notice"]), (True, modfilter.HELD_MSG))
        self.assertIn("홍길동", self.llm["calls"][-1]); self.assertIn("가상기술실 실장", self.llm["calls"][-1])
        with self.assertRaises(FileNotFoundError):
            posts.get(ctx("bob"), pid)   # 다른 사람에게 안 보임
        self.assertNotIn(pid, [p["id"] for p in posts.listing(ctx("bob"))["posts"]])
        mine = posts.get(ctx("alice"), pid)["post"]
        self.assertTrue(mine["held"])
        held = modfilter.held_list(ctx("owner", True))["items"]
        self.assertEqual([(h["kind"], h["id"], h["reason"]) for h in held], [("post", pid, "특정인을 비방")])
        with self.assertRaises(PermissionError):
            modfilter.held_list(ctx("bob"))
        with self.assertRaises(PermissionError):
            modfilter.decide(ctx("bob"), {"kind": "post", "id": pid, "action": "approve"})
        modfilter.decide(ctx("owner", True), {"kind": "post", "id": pid, "action": "approve"})
        self.assertFalse(posts.get(ctx("bob"), pid)["post"]["held"])
        with db() as c:
            log = c.execute("SELECT kind, target, stage, held, targets_person, defamatory, reason, model FROM moderation_log").fetchall()
        self.assertEqual([tuple(x) for x in log], [("post", str(pid), "llm", 1, 1, 1, "특정인을 비방", "fake-model")])

    def test_edit_clean_releases_hold_and_delete(self):
        self.defame()
        pid, _ = self.post(body="홍길동 박사는 거짓말쟁이")
        self.llm["v"] = {"targets_person": False, "defamatory": False, "reason": "일반"}
        posts.save(ctx("alice"), {"id": pid, "title": "질문", "body": "도구 질문입니다", "cat": "질문"})
        self.assertFalse(posts.get(ctx("bob"), pid)["post"]["held"])
        self.defame()
        cid = comments.write(ctx("bob"), {"kind": "post", "id": pid, "body": "김 실장 정말 형편없다"})["id"]
        self.assertEqual(len(comments.listing(ctx("carol"), "post", pid)["comments"]), 0)
        self.assertTrue(comments.listing(ctx("bob"), "post", pid)["comments"][0]["held"])
        modfilter.decide(ctx("owner", True), {"kind": "comment", "id": cid, "action": "delete"})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM comments").fetchone()[0], 0)

    def test_board_hold_shows_author_to_admin_only_and_audits(self):
        self.defame()
        r = board.save(ctx("alice"), {"title": "가상 부장 이야기", "body": "가상기술실 부장 홍길동 최악"})
        self.assertTrue(r["held"])
        self.assertNotIn("alice", json.dumps(r, ensure_ascii=False))
        with self.assertRaises(FileNotFoundError):
            board.get(ctx("bob"), r["id"])
        self.assertTrue(board.get(ctx("alice"), r["id"])["post"]["held"])
        items = modfilter.held_list(ctx("owner", True))["items"]
        self.assertEqual(items[0]["author"], {"uid": "alice", "name": "앨리스"})
        modfilter.held_list(ctx("owner", True))
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='board_held_author'").fetchone()[0], 1)   # 한 번만

    def test_fail_open_closed_and_off(self):
        self.llm["v"] = TimeoutError("8초 넘김")
        pid, r = self.post()
        self.assertNotIn("held", r)   # fail-open
        with patch.object(modfilter, "MOD_FAIL_HOLD", True):
            pid2, r2 = self.post()
            self.assertTrue(r2["held"])
        with db() as c:
            st = [tuple(x) for x in c.execute("SELECT stage, held, error IS NOT NULL FROM moderation_log ORDER BY id")]
        self.assertEqual(st, [("error", 0, 1), ("error", 1, 1)])
        n = len(self.llm["calls"])
        with patch.object(modfilter, "MOD_ON", False):
            self.defame()
            pid3, r3 = self.post()
            self.assertNotIn("held", r3)
        self.assertEqual(len(self.llm["calls"]), n)   # 끄면 LLM 안 부름

    def test_review_request_hold(self):
        self.defame()
        with patch.object(community.tools, "tool", side_effect=lambda d: {"dir": "sql-local", "port": 1, "name": "SQL"} if d == "sql-local" else None), \
             patch.object(community.toolstate, "is_off", return_value=False):
            rid = community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", "type": "bug", "title": "홍길동 책임 때문에", "body": "무능함"})
            self.assertTrue(rid["held"])
            self.assertEqual(community.requests_for(ctx("carol"), "tool", "sql-local")["requests"], [])
            self.assertTrue(community.requests_for(ctx("bob"), "tool", "sql-local")["requests"][0]["held"])


class ReplyTest(Base):
    def test_replies_one_level_and_tombstone(self):
        pid, _ = self.post()
        q, _ = self.post(title="다른 글")
        top = comments.write(ctx("bob"), {"kind": "post", "id": pid, "body": "댓글"})["id"]
        rep = comments.write(ctx("carol"), {"kind": "post", "id": pid, "body": "답글", "parent_id": top})["id"]
        with self.assertRaises(ValueError):
            comments.write(ctx("carol"), {"kind": "post", "id": pid, "body": "답글의 답글", "parent_id": rep})
        with self.assertRaises(ValueError):
            comments.write(ctx("carol"), {"kind": "post", "id": q, "body": "다른 글", "parent_id": top})
        r = community.handle_post(ctx("carol", path="/api/community/comment"), {"kind": "post", "id": q, "body": "x", "parent_id": top})
        self.assertEqual(r.code, 400)
        self.assertEqual(posts.get(ctx("alice"), pid)["post"]["comments"], 2)   # 답글 포함
        comments.delete(ctx("bob"), {"id": top})
        L = comments.listing(ctx("alice"), "post", pid)["comments"]
        self.assertEqual([(x["deleted"], x["author"], x["body"], x["parent_id"]) for x in L], [(True, "", "", None), (False, "캐롤", "답글", top)])
        self.assertEqual(posts.get(ctx("alice"), pid)["post"]["comments"], 1)
        with self.assertRaises(ValueError):
            comments.write(ctx("bob"), {"kind": "post", "id": pid, "body": "x", "parent_id": top})   # 지운 댓글에 답글 못 담
        comments.delete(ctx("carol"), {"id": rep})   # 마지막 답글을 지우면 빈 자리도 치움
        self.assertEqual(comments.listing(ctx("alice"), "post", pid)["comments"], [])
        c1 = comments.write(ctx("bob"), {"kind": "post", "id": pid, "body": "c"})["id"]
        comments.write(ctx("carol"), {"kind": "post", "id": pid, "body": "r", "parent_id": c1})
        posts.delete(ctx("alice"), {"id": pid})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM comments WHERE kind='post' AND target=?", (str(pid),)).fetchone()[0], 0)

    def test_board_replies_pseudonym_no_uid(self):
        p = board.save(ctx("alice"), {"title": "t", "body": "b"})["id"]
        top = board.comment(ctx("bob"), {"post": p, "body": "댓글"})["id"]
        r1 = board.comment(ctx("carol"), {"post": p, "body": "답글", "parent_id": top})
        self.assertEqual(r1["author"], "익명 2")
        board.comment(ctx("alice"), {"post": p, "body": "글쓴이 답글", "parent_id": top})
        with self.assertRaises(ValueError):
            board.comment(ctx("bob"), {"post": p, "body": "x", "parent_id": r1["id"]})
        q = board.save(ctx("alice"), {"title": "다른", "body": "b"})["id"]
        with self.assertRaises(ValueError):
            board.comment(ctx("bob"), {"post": q, "body": "x", "parent_id": top})
        board.delete_comment(ctx("bob"), {"id": top})
        g = board.get(ctx("carol"), p)
        self.assertEqual([(x["author"], x["parent_id"], x["deleted"]) for x in g["comments"]],
                         [("", None, True), ("익명 2", top, False), ("익명(글쓴이)", top, False)])
        s = json.dumps([board.get(ctx(u), p) for u in ("alice", "bob", "carol")] + [board.listing(ctx("bob"))], ensure_ascii=False)
        for u in ("alice", "bob", "carol", "앨리스", "밥", "캐롤"):
            self.assertNotIn(f'"{u}"', s)
        self.assertEqual(next(x for x in board.listing(ctx("bob"))["posts"] if x["id"] == p)["comments"], 2)
        board.delete(ctx("alice"), {"id": p})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM board_comments WHERE post=?", (p,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
