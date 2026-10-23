"""커뮤니티 글(posts.py) — 초안→그림→저장, 분류·공지 권한, 태그(볼 수 있는 것만), 남의 글 고치기·그림 올리기 403, 초안·숨긴 글·그림 보기 권한,
그림 종류(앞 바이트)·SVG 거절·크기·장 수·메타데이터 걷기, 신고 한 번·알림, 지우기(그림 폴더까지), 광장 목록에서 초안·숨김 빠짐, 예전 글."""
import json, os, struct, tempfile, unittest, zlib
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import community, config, db as dbm, posts, tools
from fastportal.db import db

TOOLS = {"sql-local": {"dir": "sql-local", "port": 1, "name": "SQL"},
         "secret-local": {"dir": "secret-local", "port": 2, "name": "숨김", "hidden": True}}


class Sent:
    def __init__(self, body, ctype, code=200, headers=None):
        self.body, self.ctype, self.code, self.headers = body, ctype, code, dict(headers or [])


def ctx(uid, owner=False, path="", raw=""):
    c = NS(uid=uid, uname={"alice": "앨리스", "bob": "밥"}.get(uid, uid), agent_user=uid, agent_role="admin" if owner else "", is_owner=owner, p=path, raw=raw or path)
    c.send = lambda body, ctype="application/json", code=200, headers=None: Sent(body, ctype, code, headers)
    return c


def png(extra=b""):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + extra
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def png_chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))


def seg(m, d):
    return bytes([0xFF, m]) + struct.pack(">H", len(d) + 2) + d


JPG = (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00") + seg(0xE1, b"Exif\x00\x00GPS-SECRET-12") + seg(0xFE, b"hello!")
       + seg(0xDA, b"\x01\x01\x00\x00?\x00") + b"\x12\x34\xff\xd9")


def webp_with_exif():
    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([0x0C]) + b"\x00" * 9
    vp8l = b"VP8L" + struct.pack("<I", 5) + b"\x2f\x00\x00\x00\x00" + b"\x00"
    exif = b"EXIF" + struct.pack("<I", 6) + b"GPS!!!"
    body = b"WEBP" + vp8x + vp8l + exif
    return b"RIFF" + struct.pack("<I", len(body)) + body


class PostsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.jobs = []
        self.p = [patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")),
                  patch.object(config, "DATA", self.tmp.name),
                  patch.object(community.tools, "tool", side_effect=lambda d: TOOLS.get(d)),
                  patch.object(community.toolstate, "is_off", return_value=False),
                  patch.object(community, "_builder", side_effect=self.fake_builder),
                  patch.object(posts.jobs, "upsert", side_effect=lambda *a, **k: self.jobs.append(a) or None)]
        for x in self.p: x.start()
        community._RATE.clear(); posts._IMG_RATE.clear()
        with db() as c:
            c.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT);
                CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);
                CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT, body TEXT, author TEXT, dept TEXT, tags TEXT, ts TEXT);
                CREATE TABLE post_likes(post INTEGER, user TEXT, UNIQUE(post, user));""")
            c.executemany("INSERT INTO users VALUES(?,?,?)", [("alice", "앨리스", "A"), ("bob", "밥", "B")])
            # 예전 글(글자만, uid 없음) — 열을 더하기 전에 넣어 둠
            c.execute("INSERT INTO posts(title, body, author, dept, tags, ts) VALUES('예전 사례', '# 그냥 글', '홍길동', 'A', ?, '2026-10-01T00:00:00')",
                      (json.dumps(["#활용사례"]),))
            community.init(c); posts.init(c)

    def fake_builder(self, user, path, role=""):
        if path == "/api/agents/abc123":   # alice 부서만 보는 에이전트
            return (200, {"name": "규정 도우미", "mine": False}) if user == "alice" or role == "admin" else (404, None)
        return 404, None

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def new(self, who="alice", **kw):
        pid = posts.draft(ctx(who), {})["id"]
        body = {"id": pid, "title": "회의록 정리 사례", "body": "본문 **굵게**", "cat": "활용사례", "agents": []} | kw
        posts.save(ctx(who, owner=who == "owner"), body)
        return pid

    def up(self, who, pid, data):
        c = ctx(who, raw=f"/api/community/image?post={pid}")
        return posts.upload(c, posts.upload_target(c), data)

    # ── 쓰기·고치기 ──
    def test_draft_save_list_get(self):
        pid = posts.draft(ctx("alice"), {})["id"]
        self.assertEqual(posts.listing(ctx("alice"))["counts"][""], 1)   # 초안은 목록에 없음(예전 글 1개만)
        r = self.up("alice", pid, png())
        posts.save(ctx("alice"), {"id": pid, "title": "제목", "body": f"설명\n\n![그림]({r['url']})", "cat": "활용사례", "agents": ["tool:sql-local"]})
        L = posts.listing(ctx("bob"))
        self.assertEqual(L["counts"]["활용사례"], 2)
        p = next(x for x in L["posts"] if x["id"] == pid)
        self.assertEqual((p["author"], p["dept"], p["agents"], p["thumb"], p["mine"]), ("앨리스", "A", ["tool:sql-local"], r["url"], False))
        self.assertNotIn("uid", p)
        self.assertEqual(L["hot_agents"], [{"id": "tool:sql-local", "posts": 1}])
        self.assertEqual(posts.listing(ctx("bob"), agent="tool:sql-local")["total"], 1)
        g = posts.get(ctx("alice"), str(pid))
        self.assertTrue(g["post"]["can_edit"]); self.assertEqual(g["images"], [r["name"]])
        self.assertIsNone(posts.get(ctx("bob"), pid)["images"])

    def test_other_cannot_edit_or_upload(self):
        pid = self.new()
        with self.assertRaises(PermissionError):
            posts.save(ctx("bob"), {"id": pid, "title": "x", "body": "y", "cat": "질문"})
        with self.assertRaises(PermissionError):
            posts.save(ctx("owner", True), {"id": pid, "title": "x", "body": "y", "cat": "질문"})   # 관리자도 남의 글은 고치지 않음(숨김·삭제만)
        with self.assertRaises(PermissionError):
            self.up("bob", pid, png())
        with self.assertRaises(PermissionError):   # 예전 글은 고칠 수 없음
            posts.save(ctx("alice"), {"id": 1, "title": "x", "body": "y", "cat": "질문"})

    def test_notice_admin_only_and_validation(self):
        pid = posts.draft(ctx("alice"), {})["id"]
        with self.assertRaises(PermissionError):
            posts.save(ctx("alice"), {"id": pid, "title": "공지", "body": "x", "cat": "공지"})
        for bad in ({"cat": "잡담"}, {"title": ""}, {"body": ""}, {"title": "가" * 81}, {"agents": ["tool:a", "tool:b", "tool:c", "tool:d", "tool:e", "tool:f"]},
                    {"agents": "tool:sql-local"}, {"agents": ["bad id"]}, {"agents": ["tool:secret-local"]}, {"agents": ["agent:abc123"]}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, PermissionError)):
                posts.save(ctx("bob" if bad.get("agents") == ["agent:abc123"] else "alice"),
                           {"id": pid if "agent:abc123" not in str(bad) else posts.draft(ctx("bob"), {})["id"], "title": "t", "body": "b", "cat": "질문"} | bad)
        posts.save(ctx("alice"), {"id": pid, "title": "t", "body": "b", "cat": "질문", "agents": ["agent:abc123"]})   # alice 는 그 에이전트를 봄
        o = posts.draft(ctx("owner", True), {})["id"]
        posts.save(ctx("owner", True), {"id": o, "title": "점검 안내", "body": "내일", "cat": "공지"})
        self.assertEqual(posts.listing(ctx("bob"), cat="공지")["total"], 1)

    def test_drafts_and_hidden_visibility(self):
        d = posts.draft(ctx("alice"), {})["id"]
        with self.assertRaises(FileNotFoundError):
            posts.get(ctx("bob"), d)
        with self.assertRaises(FileNotFoundError):
            posts.get(ctx("owner", True), d)   # 초안은 쓴 사람만
        pid = self.new()
        with self.assertRaises(PermissionError):
            posts.hide(ctx("alice"), {"id": pid})
        posts.hide(ctx("owner", True), {"id": pid, "hidden": True})
        with self.assertRaises(FileNotFoundError):
            posts.get(ctx("bob"), pid)
        self.assertTrue(posts.get(ctx("alice"), pid)["post"]["hidden"])
        self.assertTrue(posts.get(ctx("owner", True), pid)["post"]["hidden"])
        self.assertNotIn(pid, [p["id"] for p in posts.listing(ctx("bob"))["posts"]])
        with db() as c:   # 광장(/api/tools) 목록에서도 초안·숨김 빠짐
            ids = [p["id"] for p in tools._posts_common(c)]
        self.assertEqual(ids, [1])

    # ── 그림 ──
    def test_image_types_size_count(self):
        pid = posts.draft(ctx("alice"), {})["id"]
        for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"<html>", b"GIF8", b"\x00" * 10, b""):
            with self.subTest(bad=bad[:8]), self.assertRaises(ValueError):
                self.up("alice", pid, bad)
        with self.assertRaises(ValueError):   # PNG 머리만 있고 손상
            self.up("alice", pid, b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
        self.assertEqual(self.up("alice", pid, b"GIF89a" + b"\x00" * 20)["type"], "image/gif")
        for _ in range(posts.IMG_N - 1):
            self.up("alice", pid, png())
        with self.assertRaises(ValueError):
            self.up("alice", pid, png())   # 11장째
        self.assertEqual(posts.IMG_MAX, 5 * 1024 * 1024)   # 크기 상한은 app.route 가 본문을 읽을 때(413) — 값만 확인

    def test_upload_rate_limit(self):
        with patch.object(posts, "IMG_RATE_N", 2):
            pid = posts.draft(ctx("alice"), {})["id"]
            self.up("alice", pid, png()); self.up("alice", pid, png())
            with self.assertRaises(ValueError):
                self.up("alice", pid, png())

    def test_metadata_stripped(self):
        p = posts.clean_image(png(png_chunk(b"tEXt", b"Comment\x00secret") + png_chunk(b"eXIf", b"GPSDATA")))[1]
        self.assertNotIn(b"secret", p); self.assertNotIn(b"GPSDATA", p); self.assertTrue(p.endswith(b"IEND\xaeB`\x82"))
        ext, j = posts.clean_image(JPG)
        self.assertEqual(ext, "jpg"); self.assertNotIn(b"GPS-SECRET", j); self.assertNotIn(b"hello!", j)
        self.assertIn(b"JFIF", j); self.assertTrue(j.endswith(b"\x12\x34\xff\xd9"))
        ext, w = posts.clean_image(webp_with_exif())
        self.assertEqual(ext, "webp"); self.assertNotIn(b"GPS!!!", w)
        self.assertEqual(struct.unpack("<I", w[4:8])[0], len(w) - 8)
        self.assertEqual(w[20] & 0x0C, 0)   # VP8X 의 EXIF·XMP 표시 꺼짐

    def test_image_view_permissions_and_headers(self):
        pid = posts.draft(ctx("alice"), {})["id"]
        r = self.up("alice", pid, png())
        see = lambda who, owner=False: posts.image(ctx(who, owner, path=r["url"]))
        self.assertEqual(see("alice").ctype, "image/png")
        for who, owner in (("bob", False), ("owner", True)):   # 초안의 그림은 쓴 사람만
            with self.assertRaises(FileNotFoundError):
                see(who, owner)
        posts.save(ctx("alice"), {"id": pid, "title": "t", "body": f"![a]({r['url']})", "cat": "활용팁"})
        s = see("bob")
        self.assertEqual((s.code, s.ctype, s.headers["Content-Disposition"]), (200, "image/png", "inline"))
        self.assertIn("sandbox", s.headers["Content-Security-Policy"])
        posts.hide(ctx("owner", True), {"id": pid})
        with self.assertRaises(FileNotFoundError):
            see("bob")
        self.assertEqual(see("owner", True).code, 200)
        for bad in (f"/api/community/img/{pid}/../../portal.db", f"/api/community/img/{pid}/x.svg", f"/api/community/img/{pid}/{'a' * 16}.png"):
            with self.subTest(bad=bad), self.assertRaises(FileNotFoundError):
                posts.image(ctx("alice", path=bad))

    def test_save_drops_unused_images_delete_removes_dir(self):
        pid = posts.draft(ctx("alice"), {})["id"]
        a, b = self.up("alice", pid, png()), self.up("alice", pid, png())
        posts.save(ctx("alice"), {"id": pid, "title": "t", "body": f"![a]({a['url']})", "cat": "활용사례"})
        self.assertEqual(posts.get(ctx("alice"), pid)["images"], [a["name"]])
        with self.assertRaises(PermissionError):
            posts.delete(ctx("bob"), {"id": pid})
        posts.delete(ctx("owner", True), {"id": pid})
        self.assertFalse(os.path.exists(os.path.join(posts.root(), str(pid))))
        with self.assertRaises(FileNotFoundError):
            posts.get(ctx("alice"), pid)

    def test_report_once_and_notify(self):
        pid = self.new()
        with self.assertRaises(PermissionError):
            posts.report(ctx("alice"), {"id": pid, "reason": "x"})
        self.assertFalse(posts.report(ctx("bob"), {"id": pid, "reason": "광고"})["already"])
        self.assertTrue(posts.report(ctx("bob"), {"id": pid, "reason": "광고"})["already"])
        self.assertEqual(len(self.jobs), 1); self.assertEqual(self.jobs[0][0], "owner"); self.assertIn(f"/community/post/{pid}", self.jobs[0])
        self.assertEqual(posts.get(ctx("owner", True), pid)["post"]["reports"], 1)
        self.assertNotIn("reports", posts.get(ctx("bob"), pid)["post"])

    def test_legacy_post_renders_as_text(self):
        p = posts.get(ctx("bob"), 1)["post"]
        self.assertEqual((p["fmt"], p["body"], p["can_edit"], p["can_delete"], p["cat"]), ("text", "# 그냥 글", False, False, "활용사례"))
        self.assertTrue(posts.get(ctx("owner", True), 1)["post"]["can_delete"])

    def test_search_escapes_like(self):
        self.new(title="100% 좋아요")
        self.assertEqual(posts.listing(ctx("bob"), q="100%")["total"], 1)
        self.assertEqual(posts.listing(ctx("bob"), q="%")["total"], 1)
        self.assertEqual(posts.listing(ctx("bob"), q="_")["total"], 0)
        with self.assertRaises(ValueError):
            posts.listing(ctx("bob"), agent="x' OR 1=1")

    def test_drafts_capped(self):
        ids = [posts.draft(ctx("alice"), {})["id"] for _ in range(posts.DRAFTS_MAX + 3)]
        with db() as c:
            n = c.execute("SELECT COUNT(*) FROM posts WHERE uid='alice' AND draft=1").fetchone()[0]
        self.assertEqual(n, posts.DRAFTS_MAX)
        self.assertEqual(posts.get(ctx("alice"), ids[-1])["post"]["draft"], True)


if __name__ == "__main__":
    unittest.main()
