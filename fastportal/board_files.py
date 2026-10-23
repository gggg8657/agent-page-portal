"""자유게시판 첨부 — 그림(png·jpeg·webp·gif)과 문서(pdf·docx·xlsx·pptx·hwp·hwpx·txt·csv). zip·실행 파일은 받지 않음.

  흐름: POST /api/community/board/file?name=<원래 이름 — 확장자만 봄>(본문 = 파일 바이트) → 대기 중 첨부 id → 글 저장·댓글에 files:[id] 로 붙임.
        하루 지난 대기 첨부는 지움. 글 하나 FILES_POST(5)·댓글 하나 FILES_COMMENT(3)개, 한 개 FILE_MAX(10MB — 넘으면 413), 속도는 커뮤니티 그림과 같은 제한.
  종류: 확장자와 앞 바이트(그리고 zip 안 구성)가 맞아야 함 — 아니면 400. 문서를 열거나 실행하지 않고 바이트·XML 글자만 바꿈.
  작성자 정보 지우기(가능한 범위):
    그림  — posts.clean_image(Pillow: EXIF 회전 적용·메타데이터 없이 다시 저장, 움직이는 그림은 조각만 뺌)
    docx·xlsx·pptx — docProps/core.xml 의 만든 사람·마지막 수정한 사람, app.xml 의 회사·관리자를 비움, 메모·변경 추적의 작성자 이름(w:author 등)도 비움
    hwpx  — Contents/content.hpf 의 creator·lastsaveby 등 메타 값을 비움, 메모 작성자(author 속성)도
    pdf   — pypdf 가 있으면 Info(작성자·만든 프로그램 등)와 XMP 를 뺀 새 파일, 없으면 그대로 두고 '작성자 정보가 남을 수 있음' 표시
    hwp(OLE)·pypdf 없는 pdf — 그대로 받고 '작성자 정보가 남을 수 있음' 표시 · txt·csv — 그대로(글자 파일인지 확인)
  저장: _data/community/board/<무작위>.<확장자>. 화면에는 원래 이름 대신 '첨부N.<확장자>'·'그림N.<확장자>'와 크기만 — 이름으로 작성자가 드러나지 않게.
  보기: GET /api/community/board/file/<무작위>.<확장자> — 로그인 계정만, 붙은 글·댓글을 볼 수 있는 사람만(대기 중이면 올린 사람만). 그림은 inline·image/*,
        문서는 attachment·application/octet-stream(페이지 안에서 열지 않음), nosniff, CSP sandbox. 주소·머리에 계정이 없음."""
import io, os, re, secrets, time, urllib.parse, zipfile

from . import comments, config, posts
from .db import db, now

FILE_MAX = int(os.environ.get("BOARD_FILE_MAX", str(10 * 1024 * 1024)))
FILES_POST, FILES_COMMENT, PENDING_MAX = 5, 3, 10
IMAGES = {"png", "jpg", "gif", "webp"}
DOCS = {"pdf", "docx", "xlsx", "pptx", "hwp", "hwpx", "txt", "csv"}
EXT_ALIAS = {"jpeg": "jpg"}
NOTE_LEFT = "작성자 정보가 남을 수 있음"
FILE_RE = re.compile(r"/api/community/board/file/([0-9a-f]{24})\.([a-z]{3,4})")
OOXML_MAIN = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}
ZIP_ENTRIES, ZIP_TOTAL = 3000, 200 * 1024 * 1024
try:
    import pypdf
except ImportError:   # 없으면 PDF 는 그대로 두고 '작성자 정보가 남을 수 있음'
    pypdf = None


def root():
    return os.path.join(config.DATA, "community", "board")


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS board_files(id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, uid TEXT NOT NULL, kind TEXT, target INTEGER,
                                           ext TEXT NOT NULL, size INTEGER NOT NULL, image INTEGER NOT NULL, note TEXT, ts TEXT, seq INTEGER);
    CREATE INDEX IF NOT EXISTS board_files_t ON board_files(kind, target);
    CREATE INDEX IF NOT EXISTS board_files_uid ON board_files(uid, kind);""")


# ── 종류 확인·작성자 정보 지우기 ─────────────────────────────────────────
def _ext(name):
    e = os.path.splitext(str(name or ""))[1].lower().lstrip(".")
    e = EXT_ALIAS.get(e, e)
    if e not in IMAGES | DOCS:
        raise ValueError("올릴 수 있는 파일: 그림(png·jpg·webp·gif), 문서(pdf·docx·xlsx·pptx·hwp·hwpx·txt·csv)")
    return e


def _zip(b, ext):
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
        infos = z.infolist()
    except (zipfile.BadZipFile, ValueError, OSError):
        raise ValueError(f"{ext} 파일이 손상되었습니다")
    if len(infos) > ZIP_ENTRIES or sum(i.file_size for i in infos) > ZIP_TOTAL:
        raise ValueError(f"{ext} 파일 안 내용이 너무 큽니다")
    return z, infos


def _blank_xml(name, data):
    """XML 부분에서 사람 이름이 들어가는 칸만 비움(글자 바꾸기 — 구조는 그대로)"""
    t = data.decode("utf-8", "replace") if data[:3] != b"\xef\xbb\xbf" else data[3:].decode("utf-8", "replace")
    if name == "docProps/core.xml":
        t = re.sub(r"(<(dc:creator|cp:lastModifiedBy)\b[^>]*>)[^<]*(</\2>)", r"\1\3", t)
    elif name == "docProps/app.xml":
        t = re.sub(r"(<(Company|Manager)\b[^>]*>)[^<]*(</\2>)", r"\1\3", t)
    elif name.endswith("content.hpf"):   # hwpx 메타
        t = re.sub(r'(<opf:meta\b[^>]*\bname="(creator|lastsaveby|subject|description)"[^>]*>)[^<]*(</opf:meta>)', r"\1\3", t)
        t = re.sub(r"(<(dc|opf):(creator|contributor|publisher)\b[^>]*>)[^<]*(</\2:\3>)", r"\1\4", t)
    t = re.sub(r'\b((?:w|hp|hh|hc)?:?(?:author|initials))="[^"]*"', r'\1=""', t)   # 메모·변경 추적 작성자(docx w:author, hwpx author 등)
    if name.startswith("xl/comments") or name.startswith("xl/threadedComments"):
        t = re.sub(r"(<author>)[^<]*(</author>)", r"\1\2", t)
    if name == "ppt/commentAuthors.xml":
        t = re.sub(r'\b(name|initials)="[^"]*"', r'\1=""', t)
    if name == "xl/persons/person.xml":
        t = re.sub(r'\b(displayName|userId)="[^"]*"', r'\1=""', t)
    return t.encode("utf-8")


def _rezip(z, infos, touch):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as w:
        for i in infos:
            data = z.read(i.filename)
            if touch(i.filename):
                data = _blank_xml(i.filename, data)
            zi = zipfile.ZipInfo(i.filename, date_time=(1980, 1, 1, 0, 0, 0))   # 시각도 지움
            zi.compress_type = i.compress_type   # hwpx 의 mimetype 은 무압축 맨 앞 그대로
            zi.external_attr = 0o644 << 16
            w.writestr(zi, data)
    return out.getvalue()


def _ooxml(b, ext):
    z, infos = _zip(b, ext)
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or OOXML_MAIN[ext] not in names:
        raise ValueError(f"{ext} 파일이 아닙니다")
    return _rezip(z, infos, lambda n: n.endswith(".xml") and (n.startswith("docProps/") or "comment" in n.lower() or n.startswith(("word/", "xl/persons", "ppt/comment"))))


def _hwpx(b):
    z, infos = _zip(b, "hwpx")
    try:
        mt = z.read("mimetype").strip()
    except KeyError:
        mt = b""
    if mt != b"application/hwp+zip" and "Contents/content.hpf" not in {i.filename for i in infos}:
        raise ValueError("hwpx 파일이 아닙니다")
    return _rezip(z, infos, lambda n: n.endswith((".xml", ".hpf")) and (n.endswith("content.hpf") or n.startswith("Contents/") or n.startswith("META-INF/")))


def _pdf(b):
    if pypdf is None:
        return b, NOTE_LEFT
    try:
        r = pypdf.PdfReader(io.BytesIO(b))
        if r.is_encrypted:
            return b, NOTE_LEFT
        w = pypdf.PdfWriter(clone_from=r)
        try:
            w.metadata = None
        except Exception:
            pass
        root_obj = w._root_object
        if "/Metadata" in root_obj:
            del root_obj["/Metadata"]
        out = io.BytesIO()
        w.write(out)
        data = out.getvalue()
    except Exception:
        return b, NOTE_LEFT   # 읽지 못하는 PDF — 그대로 두고 표시(열거나 실행하지 않음)
    if b"/Author" in data or b"<x:xmpmeta" in data:
        return b, NOTE_LEFT
    return data, None


def clean(name, b):
    """원래 이름(확장자만)·바이트 → (확장자, 그림인지, 바이트, 남은 정보 안내 또는 None). 틀리면 ValueError"""
    ext = _ext(name)
    if not b:
        raise ValueError("파일이 비었습니다")
    if ext in IMAGES:
        got = posts.sniff(b)
        if got != ext:
            raise ValueError("확장자와 그림 내용이 맞지 않습니다")
        return ext, True, posts.clean_image(b)[1], None
    if ext == "pdf":
        if not b.startswith(b"%PDF-"):
            raise ValueError("pdf 파일이 아닙니다")
        data, note = _pdf(b)
        return ext, False, data, note
    if ext in OOXML_MAIN:
        if not b.startswith(b"PK\x03\x04"):
            raise ValueError(f"{ext} 파일이 아닙니다")
        return ext, False, _ooxml(b, ext), None
    if ext == "hwpx":
        if not b.startswith(b"PK\x03\x04"):
            raise ValueError("hwpx 파일이 아닙니다")
        return ext, False, _hwpx(b), None
    if ext == "hwp":
        if not b.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValueError("hwp 파일이 아닙니다")
        return ext, False, b, NOTE_LEFT
    # txt·csv — 글자 파일인지(NUL 없음, utf-8 또는 cp949)
    if b"\x00" in b[:65536]:
        raise ValueError(f"{ext} 는 글자 파일이어야 합니다")
    for enc in ("utf-8", "cp949"):
        try:
            b.decode(enc)
            return ext, False, b, None
        except UnicodeDecodeError:
            pass
    raise ValueError(f"{ext} 는 글자 파일이어야 합니다")


# ── 올리기·붙이기·보기 ───────────────────────────────────────────────────
def upload_check(ctx):
    """본문 읽기 전 — 확인된 사용자·대기 첨부 수·속도"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    name = (q.get("name") or [""])[0]
    ext = _ext(name)
    with db() as c:
        comments.need_verified(c, ctx.uid, "첨부")
        cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 86400))
        for r in c.execute("SELECT id, token, ext FROM board_files WHERE uid=? AND kind IS NULL AND ts<?", (ctx.uid, cut)).fetchall():
            _remove(c, r)
        if c.execute("SELECT COUNT(*) FROM board_files WHERE uid=? AND kind IS NULL", (ctx.uid,)).fetchone()[0] >= PENDING_MAX:
            raise ValueError("올려 둔 첨부가 너무 많습니다 — 글을 먼저 올리거나 빼 주세요")
    posts._img_rate(ctx.uid)
    return name, ext


def _remove(c, r):
    c.execute("DELETE FROM board_files WHERE id=?", (r["id"],))
    try:
        os.remove(os.path.join(root(), f"{r['token']}.{r['ext']}"))
    except OSError:
        pass


def upload(ctx, name, body):
    ext, image, data, note = clean(name, body)
    token = secrets.token_hex(12)
    os.makedirs(root(), exist_ok=True)
    tmp = os.path.join(root(), f".{token}.{ext}")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(root(), f"{token}.{ext}"))
    with db() as c:
        fid = c.execute("INSERT INTO board_files(token, uid, ext, size, image, note, ts) VALUES(?,?,?,?,?,?,?)",
                        (token, ctx.uid, ext, len(data), int(image), note, now())).lastrowid
    return {"ok": True, "id": fid, **pub({"token": token, "ext": ext, "size": len(data), "image": image, "note": note}, 1, image)}


def pub(r, n, image=None):
    image = bool(r["image"]) if image is None else image
    return {"url": f"/api/community/board/file/{r['token']}.{r['ext']}", "name": f"{'그림' if image else '첨부'}{n}.{r['ext']}",
            "ext": r["ext"], "size": r["size"], "image": image, "note": r["note"]}


def attach(c, ctx, kind, target, ids):
    """files:[id] — 내 대기 첨부(또는 이미 이 글·댓글에 붙은 것)만. 목록에서 빠진 기존 첨부는 지움"""
    if ids is None:
        return
    mx = FILES_POST if kind == "post" else FILES_COMMENT
    if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        raise ValueError("files 는 첨부 id 목록")
    ids = list(dict.fromkeys(ids))
    if len(ids) > mx:
        raise ValueError(f"첨부는 {'글' if kind == 'post' else '댓글'} 하나에 {mx}개까지입니다")
    for i in ids:
        r = c.execute("SELECT uid, kind, target FROM board_files WHERE id=?", (i,)).fetchone()
        if not r or r["uid"] != ctx.uid or not (r["kind"] is None or (r["kind"] == kind and r["target"] == target)):
            raise ValueError("붙일 수 없는 첨부입니다")
    for r in c.execute("SELECT id, token, ext FROM board_files WHERE kind=? AND target=?", (kind, target)).fetchall():
        if r["id"] not in ids:
            _remove(c, r)
    for seq, i in enumerate(ids, 1):
        c.execute("UPDATE board_files SET kind=?, target=?, seq=? WHERE id=?", (kind, target, seq, i))


def listing(c, kind, target):
    rows = c.execute("SELECT * FROM board_files WHERE kind=? AND target=? ORDER BY seq, id", (kind, target)).fetchall()
    n = {True: 0, False: 0}
    out = []
    for r in rows:
        n[bool(r["image"])] += 1
        out.append({"id": r["id"], **pub(r, n[bool(r["image"])])})
    return out


def drop(c, kind, targets):
    for t in targets:
        for r in c.execute("SELECT id, token, ext FROM board_files WHERE kind=? AND target=?", (kind, t)).fetchall():
            _remove(c, r)


def serve(ctx):
    m = FILE_RE.fullmatch(ctx.p)
    if not m:
        raise FileNotFoundError("없는 파일입니다")
    with db() as c:
        r = c.execute("SELECT * FROM board_files WHERE token=? AND ext=?", (m.group(1), m.group(2))).fetchone()
        if not r:
            raise FileNotFoundError("없는 파일입니다")
        if r["kind"] is None:
            if r["uid"] != ctx.uid:
                raise FileNotFoundError("없는 파일입니다")
            n = 1
        else:
            if r["kind"] == "post":
                p = c.execute("SELECT uid, hidden FROM board_posts WHERE id=?", (r["target"],)).fetchone()
                ok = p and (not p["hidden"] or p["uid"] == ctx.uid or ctx.is_owner)
            else:
                m2 = c.execute("SELECT m.uid, m.hidden, p.uid puid, p.hidden phidden FROM board_comments m JOIN board_posts p ON p.id=m.post WHERE m.id=?",
                               (r["target"],)).fetchone()
                ok = m2 and (ctx.is_owner or ((not m2["hidden"] or m2["uid"] == ctx.uid) and (not m2["phidden"] or m2["puid"] == ctx.uid)))
            if not ok:
                raise FileNotFoundError("없는 파일입니다")
            n = next((x["name"] for x in listing(c, r["kind"], r["target"]) if x["url"].endswith(f"{r['token']}.{r['ext']}")), f"첨부.{r['ext']}")
    try:
        with open(os.path.join(root(), f"{r['token']}.{r['ext']}"), "rb") as f:
            data = f.read(FILE_MAX + 1)
    except OSError:
        raise FileNotFoundError("없는 파일입니다")
    name = n if isinstance(n, str) else f"첨부.{r['ext']}"
    hd = [("Cache-Control", "private, max-age=3600"), ("Content-Security-Policy", "default-src 'none'; sandbox"), ("Cross-Origin-Resource-Policy", "same-origin")]
    if r["image"]:
        return ctx.send(data, posts.CTYPE[r["ext"]], headers=hd + [("Content-Disposition", "inline")])
    q = urllib.parse.quote(name)
    return ctx.send(data, "application/octet-stream", headers=hd + [("Content-Disposition", f"attachment; filename=\"attach.{r['ext']}\"; filename*=UTF-8''{q}")])
