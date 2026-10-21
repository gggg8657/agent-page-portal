"""모두의 에이전트 커뮤니티(community.py) — 볼 수 있는 대상만·자기 것 리뷰 금지·한 사람 1개·남의 리뷰 수정/삭제 403·
요청 상태는 주인/관리자만(위조 403)·투표 1회·알림·길이·속도 제한·관리자 숨김·순위(id 안 나감). 빌더는 가짜 응답."""
import os, tempfile, unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import community, config, flows
from fastportal.db import db

TOOLS = {"sql-local": {"dir": "sql-local", "port": 1, "name": "SQL", "author": "alice"},
         "secret-local": {"dir": "secret-local", "port": 2, "name": "숨김", "hidden": True, "author": "alice"}}


def ctx(uid, owner=False):
    return NS(uid=uid, agent_user="owner" if owner else uid, is_owner=owner)


class CommunityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(community.tools, "tool", side_effect=lambda d: TOOLS.get(d)),
                  patch.object(community.tools, "tools", return_value=list(TOOLS.values())),
                  patch.object(community, "_notify", side_effect=lambda *a: self.sent.append(a)),
                  patch.object(community, "_agent_owner", side_effect=lambda aid: "alice" if aid == "abc123" else None),
                  patch.object(community, "_builder", side_effect=self.fake_builder)]
        for x in self.p: x.start()
        community._RATE.clear()
        with db() as c:
            c.execute("CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT)")
            c.executemany("INSERT INTO users VALUES(?,?,?)", [("alice", "앨리스", "A"), ("bob", "밥", "A"), ("carol", "캐롤", "B")])
            c.execute("CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT)")
            flows.init(c); community.init(c)
            c.execute("INSERT INTO flows VALUES('flow123456','alice','내 흐름','[]','t','t')")

    def fake_builder(self, user, path):
        if path == "/api/agents/abc123":   # alice 의 부서 공개 에이전트 — 같은 부서(bob)·주인만 봄
            return (200, {"name": "규정 도우미", "mine": user == "alice"}) if user in ("alice", "bob", "owner") else (404, None)
        if path == "/api/stats":
            return 200, {"creators_detail": [{"uid": "alice", "name": "앨리스", "dept": "A", "agents": 1, "uses_30d": 7, "forks_30d": 2}]}
        return 404, None

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def test_review_again_updates_same_row(self):
        a = community.write_review(ctx("bob"), {"kind": "tool", "id": "sql-local", "stars": 3})
        b = community.write_review(ctx("bob"), {"kind": "tool", "id": "sql-local", "stars": 5, "body": "다시"})
        self.assertEqual(a["id"], b["id"])
        with db() as c:
            self.assertEqual(tuple(c.execute("SELECT COUNT(*), MAX(stars) FROM reviews WHERE uid='bob'").fetchone()), (1, 5))

    def test_visibility_and_own(self):
        with self.assertRaises(PermissionError):
            community.write_review(ctx("alice"), {"kind": "tool", "id": "sql-local", "stars": 5})   # 자기 도구
        for who in (ctx("bob"), ctx("owner", True)):   # 숨긴 도구는 관리자도 403
            with self.assertRaises(PermissionError):
                community.write_review(who, {"kind": "tool", "id": "secret-local", "stars": 5})
        with self.assertRaises(PermissionError):
            community.write_review(ctx("bob"), {"kind": "flow", "id": "flow123456", "stars": 5})   # 남의 흐름(공유 전)
        with self.assertRaises(PermissionError):
            community.write_review(ctx("carol"), {"kind": "agent", "id": "abc123", "stars": 5})   # 다른 부서는 못 봄
        with self.assertRaises(PermissionError):
            community.reviews(ctx("carol"), "agent", "abc123")
        with self.assertRaises(PermissionError):
            community.write_review(ctx("alice"), {"kind": "agent", "id": "abc123", "stars": 5})   # 자기 에이전트
        r = community.write_review(ctx("bob"), {"kind": "agent", "id": "abc123", "stars": 4, "body": "<b>좋아요</b>"})
        self.assertEqual(r["summary"]["reviews"], 1)
        got = community.reviews(ctx("alice"), "agent", "abc123")
        self.assertEqual(got["reviews"][0]["body"], "<b>좋아요</b>", "글 그대로 저장(화면은 텍스트로만 그림)")
        self.assertFalse(got["can_review"])

    def test_one_review_per_person_and_forgery(self):
        a = community.write_review(ctx("bob"), {"kind": "tool", "id": "sql-local", "stars": 2})
        b = community.write_review(ctx("bob"), {"kind": "tool", "id": "sql-local", "stars": 5, "body": "고쳐졌어요"})
        self.assertEqual(a["id"], b["id"])
        with db() as c:
            s = community.summary(c, ["tool:sql-local"])["tool:sql-local"]
        self.assertEqual((s["stars"], s["reviews"]), (5, 1))
        with self.assertRaises(PermissionError):
            community.delete_review(ctx("carol"), {"id": a["id"]})   # 남의 리뷰 삭제
        with self.assertRaises(PermissionError):
            community.hide(ctx("carol"), "reviews", {"id": a["id"]})   # 관리자 아님
        community.hide(ctx("owner", True), "reviews", {"id": a["id"]})
        self.assertEqual(community.reviews(ctx("carol"), "tool", "sql-local")["reviews"], [], "숨긴 리뷰는 남에게 안 보임")
        self.assertEqual(len(community.reviews(ctx("bob"), "tool", "sql-local")["reviews"]), 1, "쓴 사람은 봄")
        community.delete_review(ctx("owner", True), {"id": a["id"]})
        for bad in ({"stars": 0}, {"stars": 6}, {"stars": "5"}, {"stars": True}, {"stars": 3, "body": "x" * 501}):
            with self.assertRaises(ValueError):
                community.write_review(ctx("carol"), {"kind": "tool", "id": "sql-local", **bad})

    def test_requests_status_votes_notify(self):
        r = community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", "type": "bug", "title": "표가 잘림", "body": "500행"})
        self.assertEqual(sorted(x[0] for x in self.sent), ["alice", "owner"], "도구 요청 → author 계정 + 관리자 알림함")
        adm = community.requests_box(ctx("owner", True), "received")["requests"]
        self.assertEqual([x["id"] for x in adm], [r["id"]], "관리자는 도구 요청을 받은 요청에서 봄")
        community.set_status(ctx("owner", True), {"id": r["id"], "status": "review"})
        self.assertEqual(self.sent[-1][0], "bob")
        with self.assertRaises(PermissionError):
            community.set_status(ctx("bob"), {"id": r["id"], "status": "done"})   # 요청자도 상태는 못 바꿈
        with self.assertRaises(PermissionError):
            community.set_status(ctx("carol"), {"id": r["id"], "status": "hold"})
        community.set_status(ctx("alice"), {"id": r["id"], "status": "done", "note": "고침"})
        self.assertEqual(self.sent[-1][0], "bob", "요청자에게 상태 알림")
        with self.assertRaises(PermissionError):
            community.vote(ctx("bob"), {"id": r["id"]})   # 내 요청
        community.vote(ctx("carol"), {"id": r["id"]}); v = community.vote(ctx("carol"), {"id": r["id"]})
        self.assertEqual(v["votes"], 1)
        box = community.requests_box(ctx("alice"), "received")["requests"]
        self.assertEqual((box[0]["status"], box[0]["votes"], box[0]["can_status"], box[0]["target_name"]), ("done", 1, True, "SQL"))
        self.assertEqual(community.requests_box(ctx("carol"), "received")["requests"], [])
        with self.assertRaises(PermissionError):
            community.delete_request(ctx("carol"), {"id": r["id"]})
        for bad in ({"type": "x", "title": "a"}, {"type": "bug", "title": ""}, {"type": "bug", "title": "a" * 81}):
            with self.assertRaises(ValueError):
                community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", **bad})

    def test_rate_limit(self):
        with patch.object(community, "RATE_N", 3):
            for _ in range(3):
                community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", "type": "feature", "title": "t"})
            with self.assertRaises(ValueError):
                community.write_request(ctx("bob"), {"kind": "tool", "id": "sql-local", "type": "feature", "title": "t"})

    def test_ranking_scores_without_ids(self):
        community.write_review(ctx("bob"), {"kind": "agent", "id": "abc123", "stars": 5})
        r = community.write_request(ctx("bob"), {"kind": "agent", "id": "abc123", "type": "improve", "title": "예시 더"})
        community.set_status(ctx("alice"), {"id": r["id"], "status": "done"})
        top = community.ranking()["ranking"][0]
        self.assertEqual((top["name"], top["forks_30d"], top["reviews_30d"], top["done_30d"]), ("앨리스", 2, 1, 1))
        self.assertEqual(top["score"], 7 + 10 * 2 + 5 + 20)
        self.assertNotIn("uid", top)



class BuilderDownTest(unittest.TestCase):
    def test_no_answer_skips_next_calls(self):
        calls = []
        def fail(*a, **k):
            calls.append(1); raise (ConnectionRefusedError("refused") if len(calls) == 1 else TimeoutError("timed out"))
        with patch.object(community.tools, "tool", return_value={"dir": community.BUILDER, "port": 1}), \
             patch.object(community.tools, "tool_host", return_value="127.0.0.1"), \
             patch.object(community.jobs.TOOL_OPENER, "open", fail), patch.object(community, "_DOWN", [0.0]):
            for aid in ("abc123", "def456", "ghi789"):
                self.assertEqual(community._builder("owner", f"/api/agents/{aid}"), (502, None))
        self.assertEqual(len(calls), 2, "연결 거절은 다음에 다시 부르고, 시간 초과 뒤에는 30초 동안 안 부름")


if __name__ == "__main__":
    unittest.main()
