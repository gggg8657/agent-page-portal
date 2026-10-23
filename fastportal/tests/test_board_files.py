"""자유게시판 첨부(board_files.py) — docx·xlsx·pptx·hwpx 작성자 칸 비움, pdf(pypdf 있으면 Info·XMP 제거·없으면 표시), 그림 GPS 제거,
확장자·내용 불일치 거절, 글 5·댓글 3개, 남이 받기(이름은 첨부N, 응답·머리에 계정 없음), 대기 첨부는 올린 사람만, 숨긴 글 첨부 막힘, 지우면 파일도."""
import io, json, os, tempfile, unittest, zipfile
from types import SimpleNamespace as NS
from unittest.mock import patch

from fastportal import modfilter, board, board_files, comments, community, config, posts
from fastportal.db import db

NAMES = {"alice": "앨리스", "bob": "밥", "owner": "관리자"}


class Sent:
    def __init__(self, body, ctype, code=200, headers=None):
        self.body, self.ctype, self.code, self.headers = body, ctype, code, dict(headers or [])


def ctx(uid, owner=False, path="", raw=""):
    c = NS(uid=uid, uname=NAMES.get(uid, uid), agent_user=uid, agent_role="", is_owner=owner, p=path, raw=raw or path)
    c.send = lambda body, ctype="application/json", code=200, headers=None: Sent(body, ctype, code, headers)
    return c


def zbytes(parts, first=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        if first:
            z.writestr(zipfile.ZipInfo(first[0]), first[1])
        for n, t in parts.items():
            z.writestr(n, t)
    return b.getvalue()


CORE = ('<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dc="y"><dc:title>보고</dc:title><dc:creator>홍길동</dc:creator>'
        '<cp:lastModifiedBy>김철수</cp:lastModifiedBy></cp:coreProperties>')
APP = '<?xml version="1.0"?><Properties><Company>비밀연구실</Company><Manager>박부장</Manager><Pages>1</Pages></Properties>'
CT = '<?xml version="1.0"?><Types/>'


def docx():
    return zbytes({"[Content_Types].xml": CT, "word/document.xml": '<w:document><w:ins w:author="이수정" w:id="1"/></w:document>',
                   "word/comments.xml": '<w:comments><w:comment w:author="최메모" w:initials="CM"/></w:comments>', "docProps/core.xml": CORE, "docProps/app.xml": APP})


def xlsx():
    return zbytes({"[Content_Types].xml": CT, "xl/workbook.xml": "<workbook/>", "xl/comments1.xml": "<comments><authors><author>엑셀작성자</author></authors></comments>",
                   "docProps/core.xml": CORE, "docProps/app.xml": APP})


def pptx():
    return zbytes({"[Content_Types].xml": CT, "ppt/presentation.xml": "<p:presentation/>", "ppt/commentAuthors.xml": '<p:cmAuthorLst><p:cmAuthor name="발표자" initials="BJ"/></p:cmAuthorLst>',
                   "docProps/core.xml": CORE, "docProps/app.xml": APP})


def hwpx():
    hpf = ('<opf:package><opf:metadata><opf:title>제목</opf:title><opf:meta name="creator" content="text">한글작성자</opf:meta>'
           '<opf:meta name="lastsaveby" content="text">마지막저장</opf:meta></opf:metadata></opf:package>')
    return zbytes({"Contents/content.hpf": hpf, "Contents/section0.xml": '<hs:sec><hp:memo author="메모작성자"/></hs:sec>'}, first=("mimetype", "application/hwp+zip"))


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
       b"3 0 obj<</Author(PdfWriterName)/Creator(SecretApp)>>endobj\ntrailer<</Root 1 0 R/Info 3 0 R>>\n%%EOF\n")
LEAKS = ["홍길동", "김철수", "비밀연구실", "박부장", "이수정", "최메모", "엑셀작성자", "발표자", "한글작성자", "마지막저장", "메모작성자"]


class BoardFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = [patch.object(modfilter, "MOD_ON", False),   # 2단계(LLM) 거르기는 test_modfilter 에서만
                  patch.object(config, "DB_FILE", os.path.join(self.tmp.name, "portal.db")), patch.object(config, "DATA", self.tmp.name)]
        for x in self.p: x.start()
        community._RATE.clear(); posts._IMG_RATE.clear()
        with db() as c:
            c.executescript("""CREATE TABLE users(id TEXT PRIMARY KEY, name TEXT, dept TEXT, unit_id INTEGER, disabled INTEGER DEFAULT 0, deactivated TEXT);
                CREATE TABLE requests(id INTEGER PRIMARY KEY, kind TEXT, uid TEXT, dept TEXT, status TEXT);
                CREATE TABLE audit(ts TEXT, actor TEXT, action TEXT, target TEXT, detail TEXT);""")
            c.executemany("INSERT INTO users(id, name, dept, unit_id) VALUES(?,?,?,?)", [(k, v, "A", 1) for k, v in NAMES.items()])
            c.execute("INSERT INTO users(id, name, dept) VALUES('newbie', '새내기', '')")
            board.init(c); comments.init(c)
        for u in ("alice", "bob"):
            board.agree(ctx(u), {"version": board.RULES_VERSION})

    def tearDown(self):
        for x in reversed(self.p): x.stop()
        self.tmp.cleanup()

    def up(self, who, name, data):
        c = ctx(who, raw="/api/community/board/file?name=" + name)
        n, _ = board_files.upload_check(c)
        return board_files.upload(c, n, data)

    def test_office_and_hwpx_authors_blanked(self):
        for name, data, keep in (("보고서_홍길동.docx", docx(), "보고"), ("a.xlsx", xlsx(), "workbook"), ("a.pptx", pptx(), "presentation"), ("a.hwpx", hwpx(), "제목")):
            with self.subTest(name=name):
                ext, image, out, note = board_files.clean(name, data)
                self.assertFalse(image); self.assertIsNone(note)
                z = zipfile.ZipFile(io.BytesIO(out))
                text = "".join(z.read(n).decode() for n in z.namelist())
                for leak in LEAKS:
                    self.assertNotIn(leak, text)
                self.assertIn(keep, text)   # 내용은 그대로
                if ext == "hwpx":
                    self.assertEqual(z.namelist()[0], "mimetype"); self.assertEqual(z.read("mimetype"), b"application/hwp+zip")

    def test_pdf(self):
        ext, image, out, note = board_files.clean("x.pdf", PDF)   # 손상된(교차 참조 없음) PDF — 그대로 + 표시
        self.assertEqual((out, note), (PDF, board_files.NOTE_LEFT))
        self.assertEqual(board_files.clean("x.hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)[3], board_files.NOTE_LEFT)

    @unittest.skipUnless(board_files.pypdf, "pypdf 없음")
    def test_pdf_author_and_xmp_removed(self):
        import pypdf
        from pypdf.generic import NameObject, StreamObject
        w = pypdf.PdfWriter(); w.add_blank_page(100, 100); w.add_metadata({"/Author": "PdfWriterName", "/Creator": "SecretApp"})
        st = StreamObject(); st.set_data(b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><dc:creator>XmpPerson</dc:creator></x:xmpmeta>')
        w._root_object[NameObject("/Metadata")] = w._add_object(st)
        b = io.BytesIO(); w.write(b)
        self.assertIn(b"PdfWriterName", b.getvalue())
        ext, image, out, note = board_files.clean("x.pdf", b.getvalue())
        for x in (b"PdfWriterName", b"SecretApp", b"XmpPerson"):
            self.assertNotIn(x, out)
        self.assertIsNone(note)
        self.assertEqual(len(pypdf.PdfReader(io.BytesIO(out)).pages), 1)

    @unittest.skipUnless(posts.Image, "Pillow 없음")
    def test_image_gps_stripped(self):
        from PIL import Image
        ex = Image.Exif(); ex.get_ifd(0x8825)[2] = (36.0, 25.0, 12.0); ex[0x010F] = "PhoneMaker"
        b = io.BytesIO(); Image.new("RGB", (30, 20), (9, 9, 9)).save(b, "JPEG", exif=ex.tobytes())
        ext, image, out, note = board_files.clean("사진.jpeg", b.getvalue())
        self.assertEqual((ext, image), ("jpg", True)); self.assertNotIn(b"PhoneMaker", out); self.assertEqual(dict(Image.open(io.BytesIO(out)).getexif()), {})

    def test_wrong_types_rejected(self):
        for name, data in (("a.zip", zbytes({"x": "y"})), ("a.exe", b"MZ" + b"\0" * 10), ("a.pdf", b"<html>"), ("a.docx", PDF), ("a.docx", zbytes({"x.xml": "y"})),
                           ("a.png", PDF), ("a.svg", b"<svg/>"), ("a.txt", b"\x00\x01binary"), ("a.hwp", b"PK\x03\x04"), ("noext", b"abc"), ("a.hwpx", zbytes({"x": "y"}))):
            with self.subTest(name=name), self.assertRaises(ValueError):
                board_files.clean(name, data)
        self.assertEqual(board_files.clean("표.csv", "이름,값\n가,1\n".encode("cp949"))[0], "csv")

    def test_attach_limits_download_no_uid_and_neutral_name(self):
        ids = [self.up("alice", f"{i}_홍길동.docx", docx())["id"] for i in range(5)]
        extra = self.up("alice", "x.txt", b"hello")["id"]
        with self.assertRaises(ValueError):
            board.save(ctx("alice"), {"title": "t", "body": "b", "files": ids + [extra]})   # 6개
        pid = board.save(ctx("alice"), {"title": "t", "body": "b", "files": ids})["id"]
        g = board.get(ctx("bob"), pid)
        names = [f["name"] for f in g["post"]["files"]]
        self.assertEqual(names, [f"첨부{i}.docx" for i in range(1, 6)])
        s = json.dumps(g, ensure_ascii=False)
        self.assertNotIn("alice", s); self.assertNotIn("앨리스", s); self.assertNotIn("홍길동", s)
        r = board_files.serve(ctx("bob", path=g["post"]["files"][0]["url"]))
        self.assertEqual((r.code, r.ctype), (200, "application/octet-stream"))
        self.assertIn("attachment", r.headers["Content-Disposition"]); self.assertIn("%EC%B2%A8%EB%B6%801.docx", r.headers["Content-Disposition"])   # 첨부1.docx
        self.assertIn("sandbox", r.headers["Content-Security-Policy"])
        self.assertNotIn("alice", json.dumps(r.headers)); self.assertNotIn(b"\xed\x99\x8d\xea\xb8\xb8\xeb\x8f\x99", r.body)   # 홍길동 이 파일 안에도 없음
        c3 = [self.up("bob", f"{i}.txt", b"x")["id"] for i in range(4)]
        with self.assertRaises(ValueError):
            board.comment(ctx("bob"), {"post": pid, "body": "c", "files": c3})   # 댓글 3개까지
        cid = board.comment(ctx("bob"), {"post": pid, "body": "c", "files": c3[:3]})["id"]
        self.assertEqual(len(board.get(ctx("alice"), pid)["comments"][0]["files"]), 3)
        with self.assertRaises(ValueError):
            board.save(ctx("bob"), {"title": "남의 첨부", "body": "b", "files": [extra]})   # 남의 대기 첨부
        board.delete_comment(ctx("bob"), {"id": cid})
        board.delete(ctx("alice"), {"id": pid})
        with db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM board_files WHERE kind IS NOT NULL").fetchone()[0], 0)
            pending = c.execute("SELECT COUNT(*) FROM board_files").fetchone()[0]
        self.assertEqual(len(os.listdir(board_files.root())), pending)   # 지운 글·댓글의 파일은 디스크에서도 사라짐

    def test_pending_and_hidden_visibility(self):
        f = self.up("alice", "a.txt", b"hello")
        with self.assertRaises(FileNotFoundError):
            board_files.serve(ctx("bob", path=f["url"]))   # 대기 중 — 올린 사람만
        self.assertEqual(board_files.serve(ctx("alice", path=f["url"])).code, 200)
        pid = board.save(ctx("alice"), {"title": "t", "body": "b", "files": [f["id"]]})["id"]
        board.hide(ctx("owner", True), {"kind": "post", "id": pid})
        with self.assertRaises(FileNotFoundError):
            board_files.serve(ctx("bob", path=f["url"]))
        self.assertEqual(board_files.serve(ctx("owner", True, path=f["url"])).code, 200)
        for bad in ("/api/community/board/file/../../portal.db", "/api/community/board/file/" + "0" * 24 + ".pdf"):
            with self.assertRaises(FileNotFoundError):
                board_files.serve(ctx("alice", path=bad))

    def test_unverified_cannot_upload(self):
        with self.assertRaises(comments.NotVerified):
            board_files.upload_check(ctx("newbie", raw="/api/community/board/file?name=a.txt"))


if __name__ == "__main__":
    unittest.main()
