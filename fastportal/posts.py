"""커뮤니티 글(활용 사례·활용 팁·질문·공지) — 쓰기 화면(/community/write)에서 마크다운 본문 + 그림 + 관련 에이전트 태그.

  글은 예전 posts 표를 그대로 씀(광장 공지·사례 칸, /api/tools posts 가 같은 행을 읽음). 예전 글(uid 없음·fmt 없음)은 글자 그대로 보이고 고칠 수 없음(관리자 숨김·삭제만).
  흐름: POST draft → 빈 초안(draft=1, 나만 봄) id → 그림 올리기(POST /api/community/image?post=<id>, 본문 = 그림 바이트) → save(제목·본문·분류·태그, draft=0).
  그림: png·jpeg·webp·gif 만(앞 바이트로 판정 — SVG·HTML 은 거절), 한 장 IMG_MAX(5MB)·글 하나 IMG_N(10)장, 메타데이터(EXIF·XMP·글 조각) 걷어 냄
        Pillow 가 있으면 풀어서 EXIF 회전을 적용(휴대폰 사진이 바로 서게)하고 긴 변 MAX_SIDE(4096) 로 줄여 같은 형식으로 다시 저장(메타데이터 없이, 색 프로필만 둠).
        움직이는 GIF·WebP·PNG 는 다시 저장하지 않고 바이트에서 메타데이터 조각만 뺌. Pillow 가 없으면 모두 바이트에서만 뺌(그때는 JPEG 회전 정보도 같이 빠짐).
        저장: _data/community/<글 id>/<16진>.<확장자>.
        보기: GET /api/community/img/<글 id>/<이름> — 로그인한 계정만, 초안은 쓴 사람만, 숨긴 글은 쓴 사람·관리자만. image/* + nosniff + inline.
  태그(agents): "tool:<dir>"·"agent:<빌더 id>" 최대 AGENTS_MAX 개 — 쓸 때 쓴 사람이 볼 수 있는지 community.target 으로 확인. 읽을 때는 id 만 주고 이름은 화면이
        자기 도구·에이전트 목록에서 찾음(못 찾으면 안 보임 — 볼 수 없는 에이전트 이름이 새지 않음).
  권한: 고치기는 쓴 사람만, 지우기는 쓴 사람·관리자, 숨기기는 관리자, 신고는 남의 글에 한 번(관리자 알림함). 공지 분류는 관리자만.
API: GET /api/community/posts?cat=&q=&agent=&sort=new|hot&limit=&offset= · /api/community/post?id= · /api/community/recent · /api/community/img/<id>/<이름>
     POST /api/community/post/draft · /post/save · /post/delete · /post/hide(관리자) · /post/report · /api/community/image?post=<id>(본문 = 그림)"""
import io, json, os, re, secrets, shutil, struct, threading, time, urllib.parse

try:
    from PIL import Image, ImageOps
except ImportError:   # Pillow 없음 — 바이트에서 메타데이터만 걷는 길로
    Image = ImageOps = None

from . import accounts, comments, community, config, jobs, tools, toolstate
from .db import db, now

CATS = {"활용사례": "활용 사례", "활용팁": "활용 팁", "질문": "질문", "공지": "공지"}
ADMIN_CATS = {"공지"}
TITLE_MAX, BODY_MAX, AGENTS_MAX, REASON_MAX = 80, 20000, 5, 300
IMG_MAX = int(os.environ.get("COMMUNITY_IMG_MAX", str(5 * 1024 * 1024)))
IMG_N = 10
MAX_SIDE, MAX_PIXELS = 4096, 60_000_000   # 다시 저장할 때 긴 변 상한 · 풀기 전에 거절할 픽셀 수(압축 폭탄)
IMG_RATE_N, IMG_RATE_S = int(os.environ.get("COMMUNITY_IMG_RATE_N", "40")), 600   # 10분에 그림 40장
DRAFTS_MAX = 5
IMG_RE = re.compile(r"/api/community/img/(\d{1,9})/([0-9a-f]{16}\.(?:png|jpg|gif|webp))")
TAG_RE = re.compile(r"(tool|agent):([A-Za-z0-9_-]{1,60})")
CTYPE = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def root():
    return os.path.join(config.DATA, "community")


def init(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(posts)")}
    # 예전 행은 uid·fmt 가 비어 있음(=예전 글) — 열만 더하고 값은 안 바꿈
    for col, ddl in (("uid", "TEXT"), ("hidden", "INTEGER NOT NULL DEFAULT 0"), ("draft", "INTEGER NOT NULL DEFAULT 0"), ("agents", "TEXT"),
                     ("fmt", "TEXT"), ("updated", "TEXT")):
        if col not in cols:
            c.execute(f"ALTER TABLE posts ADD COLUMN {col} {ddl}")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS post_reports(post INTEGER NOT NULL, uid TEXT NOT NULL, reason TEXT, ts TEXT, UNIQUE(post, uid));
    CREATE INDEX IF NOT EXISTS posts_uid ON posts(uid, draft);""")


# ── 그림 확인·메타데이터 걷기 ────────────────────────────────────────────
def sniff(b):
    """앞 바이트로 종류 → 'png'|'jpg'|'gif'|'webp' 또는 None(그 밖 — SVG·HTML·실행 파일 등)"""
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def _strip_png(b):
    out, i = [b[:8]], 8
    while i + 12 <= len(b):
        n, typ = struct.unpack(">I4s", b[i:i + 8])
        end = i + 12 + n
        if end > len(b):
            break
        if typ not in (b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"):
            out.append(b[i:end])
        i = end
        if typ == b"IEND":
            return b"".join(out)
    raise ValueError("PNG 그림이 손상되었습니다")


def _strip_jpg(b):
    out, i = [b[:2]], 2
    while i < len(b):
        if b[i] != 0xFF:
            raise ValueError("JPEG 그림이 손상되었습니다")
        m = b[i + 1] if i + 1 < len(b) else None
        if m == 0xFF:   # 채움 바이트
            i += 1
            continue
        if m is None or i + 4 > len(b):
            break
        if m in (0x01,) or 0xD0 <= m <= 0xD7:   # 길이 없는 표시
            out.append(b[i:i + 2]); i += 2
            continue
        n = struct.unpack(">H", b[i + 2:i + 4])[0]
        end = i + 2 + n
        if n < 2 or end > len(b):
            break
        if m == 0xDA:   # 그림 자료 시작 — 끝까지 그대로
            out.append(b[i:])
            return b"".join(out)
        if not (m in (0xE1, 0xED, 0xFE) or 0xE3 <= m <= 0xEC or m == 0xEF):   # APP1(EXIF·XMP)·APP13(IPTC)·COM·기타 APP 뺌 — APP0(JFIF)·APP2(ICC 색)·APP14(Adobe) 는 둠
            out.append(b[i:end])
        i = end
    raise ValueError("JPEG 그림이 손상되었습니다")


def _strip_webp(b):
    size = struct.unpack("<I", b[4:8])[0]
    if size + 8 > len(b) or size < 4:
        raise ValueError("WebP 그림이 손상되었습니다")
    out, i, end = [], 12, 8 + size
    while i + 8 <= end:
        fourcc, n = b[i:i + 4], struct.unpack("<I", b[i + 4:i + 8])[0]
        j = i + 8 + n + (n & 1)
        if i + 8 + n > end:
            raise ValueError("WebP 그림이 손상되었습니다")
        ch = b[i:min(j, end)]
        if fourcc == b"VP8X" and n >= 1:
            ch = ch[:8] + bytes([ch[8] & ~0x0C]) + ch[9:]   # EXIF·XMP 있음 표시 끔
        if fourcc not in (b"EXIF", b"XMP "):
            out.append(ch)
        i = j
    body = b"".join(out)
    if not body:
        raise ValueError("WebP 그림이 손상되었습니다")
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def _strip(ext, b):
    if ext == "png":
        return _strip_png(b)
    if ext == "jpg":
        return _strip_jpg(b)
    if ext == "webp":
        return _strip_webp(b)
    return b   # GIF 는 EXIF 가 없음 — 그대로


PIL_FMT = {"png": "PNG", "jpg": "JPEG", "gif": "GIF", "webp": "WEBP"}


def _reencode(ext, b):
    """Pillow: 회전 적용·크기 상한·메타데이터 없이 같은 형식으로. 움직이는 그림은 바이트에서만 걷음"""
    try:
        im = Image.open(io.BytesIO(b))
        if im.format != PIL_FMT[ext]:
            raise ValueError
        if im.width * im.height > MAX_PIXELS:
            raise ValueError(f"그림이 너무 큽니다({im.width}×{im.height})")
        if ext == "gif" or getattr(im, "n_frames", 1) > 1:   # 움직이는 그림은 그대로 두고 조각만 뺌
            im.seek(0); im.load()
            return _strip(ext, b)
        icc = im.info.get("icc_profile")
        trans = im.info.get("transparency")
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:   # 깨진 EXIF — 돌리지 않고 그대로(메타데이터는 어차피 안 씀)
            im.load()
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        out, kw = io.BytesIO(), {"icc_profile": icc} if icc else {}
        if ext == "jpg":
            im = im if im.mode in ("RGB", "L", "CMYK") else im.convert("RGB")
            im.save(out, "JPEG", quality=90, optimize=True, exif=b"", **kw)
        elif ext == "png":
            if trans is not None and im.mode in ("P", "L", "RGB"):
                kw["transparency"] = trans
            im.save(out, "PNG", optimize=True, **kw)
        else:
            im.save(out, "WEBP", quality=90, exif=b"", xmp=b"", **kw)
        return out.getvalue()
    except ValueError as e:
        raise ValueError(str(e) or "그림이 손상되었습니다")
    except (OSError, SyntaxError, Image.DecompressionBombError) as e:
        raise ValueError("그림이 손상되었습니다") from e


def clean_image(b):
    """그림 바이트 → (확장자, 메타데이터 뺀 바이트). 지원 안 하는 종류·손상은 ValueError"""
    ext = sniff(b)
    if not ext:
        raise ValueError("png·jpeg·webp·gif 그림만 올릴 수 있습니다")
    return ext, (_reencode(ext, b) if Image else _strip(ext, b))


# ── 속도 제한(그림) ──────────────────────────────────────────────────────
_IMG_RATE, _LOCK = {}, threading.Lock()   # shortcut: community._RATE 처럼 프로세스 메모리


def _img_rate(uid):
    t = time.time()
    with _LOCK:
        v = [x for x in _IMG_RATE.get(uid, ()) if x > t - IMG_RATE_S]
        if len(v) >= IMG_RATE_N:
            raise ValueError("그림을 너무 자주 올리고 있습니다 — 잠시 뒤에 다시 해 주세요")
        v.append(t)
        _IMG_RATE[uid] = v


# ── 행 → 응답 ────────────────────────────────────────────────────────────
def _pid(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise FileNotFoundError("없는 글입니다")
    if not 0 < n < 10 ** 9:
        raise FileNotFoundError("없는 글입니다")
    return n


def _row(c, pid):
    r = c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes FROM posts p WHERE id=?", (pid,)).fetchone()
    if not r:
        raise FileNotFoundError("없는 글입니다")
    return r


def _can_see(ctx, r):
    mine = bool(r["uid"]) and r["uid"] == ctx.uid
    if r["draft"]:
        return mine
    return not r["hidden"] or mine or ctx.is_owner


def _cat(tags):
    for t in tags:
        if t.lstrip("#") in CATS:
            return t.lstrip("#")
    return ""


def excerpt(body, n=200):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body or "")   # 그림
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)   # 링크 → 글자
    t = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+", "", t)
    t = re.sub(r"[`*_~|]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n] + ("…" if len(t) > n else "")


def _thumb(r):
    m = IMG_RE.search(r["body"] or "")
    return m.group(0) if m and r["fmt"] == "md" and int(m.group(1)) == r["id"] else None


def _pub(c, r, ctx, full=False, liked=None, reports=None):
    tags = json.loads(r["tags"] or "[]")
    mine = bool(r["uid"]) and r["uid"] == ctx.uid
    if liked is None:
        liked = bool(c.execute("SELECT 1 FROM post_likes WHERE post=? AND user=?", (r["id"], ctx.uid)).fetchone())
    d = {"id": r["id"], "title": r["title"] or "", "author": r["author"] or "", "dept": r["dept"] or "", "tags": tags, "cat": _cat(tags),
         "ts": r["ts"] or "", "updated": r["updated"] or r["ts"] or "", "likes": r["likes"], "liked": liked, "agents": json.loads(r["agents"] or "[]"),
         "fmt": r["fmt"] or "text", "mine": mine, "can_edit": mine and r["fmt"] == "md", "can_delete": mine or bool(ctx.is_owner),
         "hidden": bool(r["hidden"]), "draft": bool(r["draft"]), "thumb": _thumb(r), "excerpt": excerpt(r["body"]) if r["fmt"] == "md" else (r["body"] or "")[:200]}
    if full:
        d["body"] = r["body"] or ""
        d["reported"] = bool(c.execute("SELECT 1 FROM post_reports WHERE post=? AND uid=?", (r["id"], ctx.uid)).fetchone())
    if ctx.is_owner:
        d["reports"] = reports if reports is not None else c.execute("SELECT COUNT(*) FROM post_reports WHERE post=?", (r["id"],)).fetchone()[0]
    return d


def _tag_like(cat):
    """tags 열(JSON, 예전 /api/post 처럼 한글은 \\uXXXX)에서 분류 태그 찾기"""
    return "%" + json.dumps("#" + cat) + "%"


# ── 읽기 ─────────────────────────────────────────────────────────────────
def listing(ctx, cat="", q="", agent="", sort="new", limit=30, offset=0):
    if cat and cat not in CATS:
        raise ValueError("cat 은 " + "|".join(CATS))
    if agent and not TAG_RE.fullmatch(agent):
        raise ValueError("agent 는 tool:<dir>|agent:<id>")
    limit, offset = max(1, min(int(limit or 30), 100)), max(0, int(offset or 0))
    base = "FROM posts p WHERE draft=0" + ("" if ctx.is_owner else " AND (hidden=0 OR uid=?)")
    args = [] if ctx.is_owner else [ctx.uid]
    where, wargs = "", []
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:100] + "%"
        where += " AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\')"
        wargs += [like, like, like]
    if agent:
        where += " AND agents LIKE ?"
        wargs.append(f'%"{agent}"%')
    with db() as c:
        counts ={"": c.execute("SELECT COUNT(*) " + base + where, args + wargs).fetchone()[0]}
        for k in CATS:
            counts[k] = c.execute("SELECT COUNT(*) " + base + where + " AND tags LIKE ?", args + wargs + [_tag_like(k)]).fetchone()[0]
        cw = where + (" AND tags LIKE ?" if cat else "")
        ca = wargs + ([_tag_like(cat)] if cat else [])
        order = "likes DESC, id DESC" if sort == "hot" else "id DESC"
        rows = c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes " + base + cw + f" ORDER BY {order} LIMIT ? OFFSET ?",
                         args + ca + [limit, offset]).fetchall()
        liked = {r[0] for r in c.execute("SELECT post FROM post_likes WHERE user=?", (ctx.uid,))}
        reps = {r[0]: r[1] for r in c.execute("SELECT post, COUNT(*) FROM post_reports GROUP BY post")} if ctx.is_owner else {}
        hot = {}
        for (a,) in c.execute("SELECT agents FROM posts WHERE draft=0 AND hidden=0 AND agents IS NOT NULL AND agents!='[]'"):
            for t in json.loads(a or "[]"):
                hot[t] = hot.get(t, 0) + 1
        posts = [_pub(c, r, ctx, liked=r["id"] in liked, reports=reps.get(r["id"], 0)) for r in rows]
        cm = comments.counts(c, "post", [p["id"] for p in posts])
        for p in posts:
            p["comments"] = cm.get(str(p["id"]), 0)
    return {"posts": posts, "counts": counts, "cats": CATS, "total": counts[cat] if cat else counts[""],
            "hot_agents": [{"id": k, "posts": v} for k, v in sorted(hot.items(), key=lambda x: (-x[1], x[0]))[:20]],
            "can_notice": bool(ctx.is_owner), "limits": {"title": TITLE_MAX, "body": BODY_MAX, "agents": AGENTS_MAX, "img_bytes": IMG_MAX, "img_n": IMG_N}}


def get(ctx, pid):
    pid = _pid(pid)
    with db() as c:
        r = _row(c, pid)
        if not _can_see(ctx, r):
            raise FileNotFoundError("없는 글입니다")
        d = _pub(c, r, ctx, full=True)
        d["comments"] = comments.counts(c, "post", [pid]).get(str(pid), 0)
        return {"post": d, "images": _images(pid) if (r["uid"] == ctx.uid) else None}


def _images(pid):
    d = os.path.join(root(), str(pid))
    try:
        return sorted(f for f in os.listdir(d) if IMG_RE.fullmatch(f"/api/community/img/{pid}/{f}"))
    except FileNotFoundError:
        return []


def recent(ctx):
    """요청/리뷰 칸: 도구·공유 에이전트에 최근 달린 리뷰·요청(숨김 빼고) — 볼 수 있는 대상만(흐름은 만든 사람만 보므로 뺌)"""
    off = set() if ctx.is_owner else toolstate.off()
    out, ok = [], {}
    with db() as c:
        names = community._names(c)
        rows = [("review", r) for r in c.execute("SELECT * FROM reviews WHERE hidden=0 AND kind IN ('tool','agent') ORDER BY updated DESC LIMIT 40")] + \
               [("request", r) for r in c.execute("SELECT * FROM requests_fb WHERE hidden=0 AND kind IN ('tool','agent') ORDER BY created DESC LIMIT 40")]
        rows.sort(key=lambda x: x[1]["updated"] or x[1]["created"] or "", reverse=True)
        for typ, r in rows:
            key = f"{r['kind']}:{r['target']}"
            if key not in ok:
                if r["kind"] == "tool" and r["target"] in off:
                    ok[key] = None
                elif len(ok) >= 30:   # 빌더에 묻는 수 상한(한 요청에)
                    continue
                else:
                    try:
                        ok[key] = community.target(ctx, r["kind"], r["target"])["name"]
                    except (PermissionError, ValueError, LookupError):
                        ok[key] = None
            if not ok[key]:
                continue
            d = {"type": typ, "id": r["id"], "kind": r["kind"], "target": r["target"], "target_name": ok[key], "name": names.get(r["uid"], r["uid"]),
                 "created": r["created"], "updated": r["updated"], "body": r["body"] or ""}
            if typ == "review":
                d["stars"] = r["stars"]
            else:
                d |= {"title": r["title"], "status": r["status"], "status_label": community.REQ_STATES.get(r["status"], r["status"]),
                      "type_label": community.REQ_TYPES.get(r["type"], r["type"])}
            out.append(d)
            if len(out) >= 30:
                break
        for kind in ("review", "request"):
            cm = comments.counts(c, kind, [d["id"] for d in out if d["type"] == kind])
            for d in out:
                if d["type"] == kind:
                    d["comments"] = cm.get(d["id"], 0)
    return {"items": out}


# ── 쓰기 ─────────────────────────────────────────────────────────────────
def _drop_dir(pid):
    shutil.rmtree(os.path.join(root(), str(pid)), ignore_errors=True)


def draft(ctx, req):
    """빈 초안 하나 — 그림을 먼저 올릴 자리. 하루 지난 내 초안은 지우고, 초안은 사람당 DRAFTS_MAX 개(넘으면 오래된 것부터 지움)"""
    if not isinstance(ctx.uid, str) or not ctx.uid:
        raise PermissionError("로그인이 필요합니다")
    community._rate(ctx.uid)
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 86400))
    with db() as c:
        old = [r[0] for r in c.execute("SELECT id FROM posts WHERE uid=? AND draft=1 ORDER BY id DESC", (ctx.uid,))]
        stale = {r[0] for r in c.execute("SELECT id FROM posts WHERE uid=? AND draft=1 AND COALESCE(updated, ts, '')<?", (ctx.uid, cut))}
        drop = [i for i in old if i in stale] + [i for i in old if i not in stale][DRAFTS_MAX - 1:]
        for i in drop:
            c.execute("DELETE FROM posts WHERE id=?", (i,))
        pid = c.execute("INSERT INTO posts(title, body, author, dept, tags, ts, uid, draft, fmt, updated, agents) VALUES('', '', ?, '', '[]', ?, ?, 1, 'md', ?, '[]')",
                        (ctx.uname or "", now(), ctx.uid, now())).lastrowid
    for i in drop:
        _drop_dir(i)
    return {"ok": True, "id": pid}


def _agents(ctx, v):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > AGENTS_MAX or not all(isinstance(x, str) for x in v):
        raise ValueError(f"관련 에이전트는 {AGENTS_MAX}개까지 고를 수 있습니다")
    out = []
    for x in v:
        m = TAG_RE.fullmatch(x)
        if not m:
            raise ValueError("관련 에이전트 형식이 틀립니다")
        if x in out:
            continue
        try:
            community.target(ctx, m[1], m[2])
        except PermissionError:
            raise ValueError("볼 수 없는 에이전트는 고를 수 없습니다")
        out.append(x)
    return out


def save(ctx, req):
    pid = _pid(req.get("id"))
    title = community._text(req.get("title"), TITLE_MAX, "제목", required=True)
    body = community._text(req.get("body"), BODY_MAX, "본문", required=True)
    cat = req.get("cat")
    if cat not in CATS:
        raise ValueError("분류를 골라 주세요")
    if cat in ADMIN_CATS and not ctx.is_owner:
        raise PermissionError("공지는 관리자만 쓸 수 있습니다")
    agents = _agents(ctx, req.get("agents"))
    with db() as c:
        r = _row(c, pid)
        if r["draft"] and config.COMMUNITY_POST_VERIFIED_ONLY:   # 새 글만(고치기는 그대로) — 기본 꺼짐
            comments.need_verified(c, ctx.uid, "글")
        if not (r["uid"] and r["uid"] == ctx.uid) or r["fmt"] != "md":
            raise PermissionError("내가 쓴 글만 고칠 수 있습니다")
    community._rate(ctx.uid)
    with db() as c:
        dept = (c.execute("SELECT dept FROM users WHERE id=?", (ctx.uid,)).fetchone() or [""])[0] or ""
        first = bool(r["draft"])
        c.execute("UPDATE posts SET title=?, body=?, tags=?, agents=?, draft=0, updated=?, ts=?, author=?, dept=? WHERE id=?",
                  (title, body, json.dumps(["#" + cat]), json.dumps(agents), now(), now() if first else r["ts"],
                   ctx.uname or r["author"], dept if first else r["dept"], pid))
        accounts.audit(c, ctx.uid, "post_write" if first else "post_edit", f"post:{pid}", f"#{cat} 그림 {len(_images(pid))}")
    used = {m.group(2) for m in IMG_RE.finditer(body) if int(m.group(1)) == pid}
    for f in _images(pid):   # 본문에서 뺀 그림은 지움
        if f not in used:
            try:
                os.remove(os.path.join(root(), str(pid), f))
            except OSError:
                pass
    tools.stats_reset()
    return {"ok": True, "id": pid}


def delete(ctx, req):
    pid = _pid(req.get("id"))
    with db() as c:
        r = _row(c, pid)
        mine = bool(r["uid"]) and r["uid"] == ctx.uid
        if not (mine or ctx.is_owner):
            raise PermissionError("다른 사람의 글입니다")
        c.execute("DELETE FROM posts WHERE id=?", (pid,))
        c.execute("DELETE FROM post_likes WHERE post=?", (pid,))
        c.execute("DELETE FROM post_reports WHERE post=?", (pid,))
        community._drop_comments(c, "post", pid)
        accounts.audit(c, ctx.uid, "post_delete", f"post:{pid}", (r["title"] or "")[:60] + ("" if mine else " (관리자)"))
    _drop_dir(pid)
    tools.stats_reset()
    return {"ok": True}


def hide(ctx, req):
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    pid = _pid(req.get("id"))
    on = bool(req.get("hidden", True))
    with db() as c:
        _row(c, pid)
        c.execute("UPDATE posts SET hidden=? WHERE id=?", (int(on), pid))
        accounts.audit(c, ctx.uid, "post_hide" if on else "post_unhide", f"post:{pid}")
    tools.stats_reset()
    return {"ok": True, "hidden": on}


def report(ctx, req):
    pid = _pid(req.get("id"))
    reason = community._text(req.get("reason"), REASON_MAX, "신고 이유")
    with db() as c:
        r = _row(c, pid)
        if r["draft"] or (r["hidden"] and not ctx.is_owner):
            raise FileNotFoundError("없는 글입니다")
        if r["uid"] and r["uid"] == ctx.uid:
            raise PermissionError("내 글은 신고할 수 없습니다")
    community._rate(ctx.uid)
    with db() as c:
        new = c.execute("INSERT OR IGNORE INTO post_reports VALUES(?,?,?,?)", (pid, ctx.uid, reason, now())).rowcount
        if new:
            accounts.audit(c, ctx.uid, "post_report", f"post:{pid}", reason[:120])
    if new:
        try:
            j = jobs.upsert("owner", community.JOB_TOOL, "done", f"report-{pid}-{secrets.token_hex(3)}", f"글 신고: {(r['title'] or '')[:80]}",
                            f"/community/post/{pid}", (reason or "이유 없음")[:400])
            if j:
                jobs.HUB.publish("job", j, j["user"])
        except Exception as e:
            print("posts: 신고 알림 실패", type(e).__name__, e, flush=True)
    return {"ok": True, "already": not new}


# ── 그림 올리기·보기 ─────────────────────────────────────────────────────
def upload_target(ctx):
    """본문을 읽기 전 확인 — 내 글(초안 포함)·장 수·속도. 글 id 또는 예외"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    pid = _pid((q.get("post") or [""])[0])
    with db() as c:
        r = _row(c, pid)
    if not (r["uid"] and r["uid"] == ctx.uid) or r["fmt"] != "md":
        raise PermissionError("내 글에만 그림을 올릴 수 있습니다")
    if len(_images(pid)) >= IMG_N:
        raise ValueError(f"그림은 글 하나에 {IMG_N}장까지 올릴 수 있습니다")
    _img_rate(ctx.uid)
    return pid


def upload(ctx, pid, body):
    if not body:
        raise ValueError("그림이 비었습니다")
    ext, data = clean_image(body)
    d = os.path.join(root(), str(pid))
    os.makedirs(d, exist_ok=True)
    if len(_images(pid)) >= IMG_N:   # 동시에 여러 장 — 쓰기 직전에 다시
        raise ValueError(f"그림은 글 하나에 {IMG_N}장까지 올릴 수 있습니다")
    name = f"{secrets.token_hex(8)}.{ext}"
    tmp = os.path.join(d, "." + name)
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(d, name))
    with db() as c:
        accounts.audit(c, ctx.uid, "post_image", f"post:{pid}", f"{name} {len(data)}B")
    return {"ok": True, "url": f"/api/community/img/{pid}/{name}", "name": name, "size": len(data), "type": CTYPE[ext]}


def image(ctx):
    m = IMG_RE.fullmatch(ctx.p)
    if not m:
        raise FileNotFoundError("없는 그림입니다")
    pid = int(m.group(1))
    with db() as c:
        r = c.execute("SELECT id, uid, draft, hidden FROM posts WHERE id=?", (pid,)).fetchone()
    if not r or not _can_see(ctx, r):
        raise FileNotFoundError("없는 그림입니다")
    try:
        with open(os.path.join(root(), str(pid), m.group(2)), "rb") as f:
            data = f.read(IMG_MAX + 1)
    except OSError:
        raise FileNotFoundError("없는 그림입니다")
    ext = m.group(2).rsplit(".", 1)[1]
    return ctx.send(data, CTYPE[ext], headers=[("Content-Disposition", "inline"), ("Cache-Control", "private, max-age=86400"),
                                               ("Content-Security-Policy", "default-src 'none'; sandbox"), ("Cross-Origin-Resource-Policy", "same-origin")])


GETS = {"/api/community/posts": lambda ctx, one: listing(ctx, one("cat"), one("q").strip(), one("agent"), one("sort"), one("limit") or 30, one("offset") or 0),
        "/api/community/post": lambda ctx, one: get(ctx, one("id")),
        "/api/community/recent": lambda ctx, one: recent(ctx)}
POSTS = {"/api/community/post/draft": draft, "/api/community/post/save": save, "/api/community/post/delete": delete,
         "/api/community/post/hide": hide, "/api/community/post/report": report}
