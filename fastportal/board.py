"""커뮤니티 자유게시판 — 가명(익명 N) 글·댓글. 다른 사람에게는 '익명(글쓴이)'·'익명 N' 으로만, 계정은 서버에만 둠.

  가명: 글마다 따로 매김 — 글쓴이 0 = '익명(글쓴이)', 댓글을 처음 단 순서대로 1, 2, … (같은 글 안에서 같은 사람은 늘 같은 번호, 지워도 번호 유지).
  응답에 계정 id·이름을 넣지 않음(내 것인지는 mine 참·거짓만). 글·댓글 쓰기는 감사 기록에도 남기지 않음(감사 표 actor 로 작성자가 드러나므로).
  내용은 로그에 찍지 않음. 본문은 글자만 — 화면도 링크를 누를 수 없는 글자로 그림. 그림·문서 첨부는 board_files.py(작성자 정보 지우기·중립 이름).
  작성자 확인(관리자만): 신고가 1건 이상 있는 글·댓글에만, 이유를 적어야 하고, 감사 기록(board_reveal: 누가·언제·어느 글·이유)을 남긴 뒤 계정을 돌려줌.
  쓰기(새 글·댓글)는 '확인된 사용자'(accounts.is_verified)만 — 아니면 403 code=not_verified. 댓글은 쓴 사람이 고칠 수 있음(/board/comment/edit).
  안전: 처음 쓰기 전 규칙 동의(board_agree), 사람당 1시간에 글 POST_RATE(5)·댓글 COMMENT_RATE(30)(DB 로 셈 — 포털 프로세스가 여럿이어도 같음),
        서로 다른 신고자 N 명(기본 3, 관리자 설정)이면 자동 숨김, 관리자 숨기기·지우기, 신고는 관리자 알림함(작성자 없이).
API: GET /api/community/board?q=&offset= · /board/post?id= · /board/reports(관리자)
     POST /board/agree · /board/save {id?,title,body} · /board/delete {id} · /board/comment {post,body} · /board/comment/delete {id}
          /board/report {kind:post|comment,id,reason} · /board/hide {kind,id,hidden}(관리자) · /board/reveal {kind,id,reason}(관리자) · /board/settings {autohide}(관리자)"""
import os, re, secrets, time

from . import accounts, board_files, comments, community, jobs, modfilter
from .db import db, now

TITLE_MAX, BODY_MAX, COMMENT_MAX, REASON_MAX = 80, 5000, 1000, 300
POST_RATE = int(os.environ.get("BOARD_POST_RATE", "5"))        # 1시간에 글
COMMENT_RATE = int(os.environ.get("BOARD_COMMENT_RATE", "30"))  # 1시간에 댓글
AUTOHIDE_DEFAULT = 3
RULES_VERSION = 1
NOTICE = "다른 사용자에게는 익명으로 보이지만, 신고된 글에 한해 관리자가 기록을 남기고 작성자를 확인할 수 있습니다."
RULES = ["다른 사람을 비방하지 않습니다", "개인정보(이름·연락처·사번 등)를 쓰지 않습니다", "내부 기밀·대외비 자료를 쓰지 않습니다"]
KINDS = {"post": "board_posts", "comment": "board_comments"}


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS board_posts(id INTEGER PRIMARY KEY, uid TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, ts TEXT, updated TEXT,
                                           hidden INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS board_posts_uid ON board_posts(uid, ts);
    CREATE TABLE IF NOT EXISTS board_comments(id INTEGER PRIMARY KEY, post INTEGER NOT NULL, uid TEXT NOT NULL, body TEXT NOT NULL, ts TEXT,
                                              hidden INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS board_comments_post ON board_comments(post, id);
    CREATE INDEX IF NOT EXISTS board_comments_uid ON board_comments(uid, ts);
    CREATE TABLE IF NOT EXISTS board_alias(post INTEGER NOT NULL, uid TEXT NOT NULL, n INTEGER NOT NULL, UNIQUE(post, uid), UNIQUE(post, n));
    CREATE TABLE IF NOT EXISTS board_reports(kind TEXT NOT NULL, target INTEGER NOT NULL, uid TEXT NOT NULL, reason TEXT, ts TEXT, UNIQUE(kind, target, uid));
    CREATE TABLE IF NOT EXISTS board_agree(uid TEXT PRIMARY KEY, version INTEGER NOT NULL, ts TEXT);
    CREATE TABLE IF NOT EXISTS board_settings(k TEXT PRIMARY KEY, v TEXT);""")
    board_files.init(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(board_comments)")}
    for col, ddl in (("updated", "TEXT"), ("parent_id", "INTEGER"), ("deleted", "INTEGER NOT NULL DEFAULT 0")):   # 고치기 · 답글(한 단) · 지운 자리
        if col not in cols:
            c.execute(f"ALTER TABLE board_comments ADD COLUMN {col} {ddl}")
    modfilter.add_held_column(c, "board_posts")
    modfilter.add_held_column(c, "board_comments")


# ── 도움 ─────────────────────────────────────────────────────────────────
def _id(v, what="글"):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise FileNotFoundError(f"없는 {what}입니다")
    if not 0 < n < 10 ** 9:
        raise FileNotFoundError(f"없는 {what}입니다")
    return n


def _label(n):
    return "익명(글쓴이)" if n == 0 else f"익명 {n}"


def _alias(c, post, uid, author_uid):
    """그 글에서 이 사람의 번호 — 글쓴이 0, 없으면 다음 번호를 매김"""
    if uid == author_uid:
        return 0
    r = c.execute("SELECT n FROM board_alias WHERE post=? AND uid=?", (post, uid)).fetchone()
    if r:
        return r[0]
    n = (c.execute("SELECT MAX(n) FROM board_alias WHERE post=?", (post,)).fetchone()[0] or 0) + 1
    c.execute("INSERT INTO board_alias(post, uid, n) VALUES(?,?,?)", (post, uid, n))
    return n


def autohide_n(c):
    r = c.execute("SELECT v FROM board_settings WHERE k='autohide'").fetchone()
    try:
        return max(1, int(r[0])) if r else AUTOHIDE_DEFAULT
    except ValueError:
        return AUTOHIDE_DEFAULT


def agreed(c, uid):
    r = c.execute("SELECT version FROM board_agree WHERE uid=?", (uid,)).fetchone()
    return bool(r) and r[0] >= RULES_VERSION


def _need_agree(c, uid):
    if not agreed(c, uid):
        raise PermissionError("자유게시판 규칙에 먼저 동의해 주세요")


def _rate(c, table, uid, n, what):
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
    if c.execute(f"SELECT COUNT(*) FROM {table} WHERE uid=? AND ts>=?", (uid, cut)).fetchone()[0] >= n:
        raise ValueError(f"{what}은(는) 1시간에 {n}개까지 쓸 수 있습니다 — 잠시 뒤에 다시 해 주세요")


def _post_row(c, pid, ctx):
    r = c.execute("SELECT * FROM board_posts WHERE id=?", (pid,)).fetchone()
    if not r or (r["hidden"] and r["uid"] != ctx.uid and not ctx.is_owner):
        raise FileNotFoundError("없는 글입니다")
    return r


def _reports(c, kind, target):
    return c.execute("SELECT COUNT(*) FROM board_reports WHERE kind=? AND target=?", (kind, target)).fetchone()[0]


# ── 읽기 ─────────────────────────────────────────────────────────────────
def listing(ctx, q="", offset=0):
    offset = max(0, int(offset or 0))
    where, args = ("" if ctx.is_owner else " WHERE (p.hidden=0 OR p.uid=?)"), ([] if ctx.is_owner else [ctx.uid])
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")[:100] + "%"
        where += (" AND" if where else " WHERE") + " (p.title LIKE ? ESCAPE '\\' OR p.body LIKE ? ESCAPE '\\')"
        args += [like, like]
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM board_posts p" + where, args).fetchone()[0]
        rows = c.execute("SELECT p.id, p.uid, p.title, p.body, p.ts, p.updated, p.hidden, "
                         "(SELECT COUNT(*) FROM board_comments m WHERE m.post=p.id AND m.deleted=0 AND (m.hidden=0 OR ?)) comments, p.held FROM board_posts p" + where +
                         " ORDER BY p.id DESC LIMIT 30 OFFSET ?", [int(bool(ctx.is_owner))] + args + [offset]).fetchall()
        out = []
        for r in rows:
            d = {"id": r["id"], "title": r["title"], "excerpt": re.sub(r"\s+", " ", r["body"])[:160], "ts": r["ts"], "updated": r["updated"],
                 "author": _label(0), "comments": r["comments"], "hidden": bool(r["hidden"]), "held": bool(r["held"]), "mine": r["uid"] == ctx.uid,
                 "files": c.execute("SELECT COUNT(*) FROM board_files WHERE kind='post' AND target=?", (r["id"],)).fetchone()[0]}
            if ctx.is_owner:
                d["reports"] = _reports(c, "post", r["id"])
            out.append(d)
        v = accounts.is_verified(c, ctx.uid)
        return {"posts": out, "total": total, "verified": v, "not_verified": "" if v else comments.NOT_VERIFIED, "agreed": agreed(c, ctx.uid), "notice": NOTICE, "rules": RULES, "rules_version": RULES_VERSION,
                "limits": {"title": TITLE_MAX, "body": BODY_MAX, "comment": COMMENT_MAX, "posts_per_hour": POST_RATE, "comments_per_hour": COMMENT_RATE},
                "admin": bool(ctx.is_owner)}


def get(ctx, pid):
    pid = _id(pid)
    with db() as c:
        r = _post_row(c, pid, ctx)
        alias = {x[0]: x[1] for x in c.execute("SELECT uid, n FROM board_alias WHERE post=?", (pid,))}
        mine_rep = {(x[0], x[1]) for x in c.execute("SELECT kind, target FROM board_reports WHERE uid=?", (ctx.uid,))}
        cq = "SELECT * FROM board_comments WHERE post=?" + ("" if ctx.is_owner else " AND (hidden=0 OR uid=?)") + " ORDER BY id"
        cs = []
        for m in c.execute(cq, (pid,) if ctx.is_owner else (pid, ctx.uid)):
            gone = bool(m["deleted"])   # 답글이 남아 자리만 둔 지운 댓글
            d = {"id": m["id"], "parent_id": m["parent_id"], "deleted": gone, "author": "" if gone else _label(0 if m["uid"] == r["uid"] else alias.get(m["uid"], 0)),
                 "body": "" if gone else m["body"], "ts": m["ts"], "updated": m["updated"], "mine": not gone and m["uid"] == ctx.uid, "hidden": bool(m["hidden"]),
                 "held": bool(m["held"]), "reported": ("comment", m["id"]) in mine_rep}
            if ctx.is_owner:
                d["reports"] = _reports(c, "comment", m["id"])
            d["files"] = board_files.listing(c, "comment", m["id"])
            cs.append(d)
        post = {"id": r["id"], "title": r["title"], "body": r["body"], "ts": r["ts"], "updated": r["updated"], "author": _label(0),
                "mine": r["uid"] == ctx.uid, "hidden": bool(r["hidden"]), "held": bool(r["held"]), "reported": ("post", pid) in mine_rep,
                "files": board_files.listing(c, "post", pid)}
        if ctx.is_owner:
            post["reports"] = _reports(c, "post", pid)
        v = accounts.is_verified(c, ctx.uid)
        return {"post": post, "comments": cs, "verified": v, "not_verified": "" if v else comments.NOT_VERIFIED, "agreed": agreed(c, ctx.uid), "notice": NOTICE, "rules": RULES, "rules_version": RULES_VERSION,
                "admin": bool(ctx.is_owner)}


def reports(ctx):
    """관리자: 신고된 글·댓글(신고 수·이유) — 작성자는 없음(작성자 확인은 reveal 로만)"""
    if not ctx.is_owner:
        raise PermissionError("관리자만 볼 수 있습니다")
    with db() as c:
        out = []
        for kind, target, n, last in c.execute("SELECT kind, target, COUNT(*), MAX(ts) FROM board_reports GROUP BY kind, target ORDER BY MAX(ts) DESC LIMIT 100"):
            if kind == "post":
                r = c.execute("SELECT id, title, body, hidden FROM board_posts WHERE id=?", (target,)).fetchone()
                if not r:
                    continue
                d = {"kind": kind, "id": r["id"], "post": r["id"], "title": r["title"], "body": r["body"][:300], "hidden": bool(r["hidden"])}
            else:
                r = c.execute("SELECT m.id, m.post, m.body, m.hidden, p.title FROM board_comments m JOIN board_posts p ON p.id=m.post WHERE m.id=?", (target,)).fetchone()
                if not r:
                    continue
                d = {"kind": kind, "id": r["id"], "post": r["post"], "title": r["title"], "body": r["body"][:300], "hidden": bool(r["hidden"])}
            d |= {"reports": n, "last": last, "reasons": [x[0] for x in c.execute("SELECT reason FROM board_reports WHERE kind=? AND target=? AND reason!='' ORDER BY ts DESC LIMIT 10", (kind, target))]}
            out.append(d)
        return {"items": out, "autohide": autohide_n(c)}


# ── 쓰기 ─────────────────────────────────────────────────────────────────
def agree(ctx, req):
    if req.get("version") != RULES_VERSION:
        raise ValueError("규칙 판이 바뀌었습니다 — 화면을 새로고침해 주세요")
    with db() as c:
        c.execute("INSERT INTO board_agree(uid, version, ts) VALUES(?,?,?) ON CONFLICT(uid) DO UPDATE SET version=excluded.version, ts=excluded.ts",
                  (ctx.uid, RULES_VERSION, now()))
    return {"ok": True}


def save(ctx, req):
    title = community._text(req.get("title"), TITLE_MAX, "제목", required=True)
    body = community._text(req.get("body"), BODY_MAX, "본문", required=True)
    with db() as c:   # 권한·속도 먼저(가벼움) — 거르기(LLM)는 그 뒤 잠금 밖에서
        _need_agree(c, ctx.uid)
        if req.get("id") is None:
            comments.need_verified(c, ctx.uid, "글")
            _rate(c, "board_posts", ctx.uid, POST_RATE, "글")
    v = modfilter.check(ctx, "board_post", title, body)
    out = _save(ctx, req, title, body, v)
    if v.held:
        modfilter.notify_held("board_post", out["id"])
    return out


def _save(ctx, req, title, body, v):
    with db() as c:
        if req.get("id") is not None:
            pid = _id(req.get("id"))
            r = c.execute("SELECT uid FROM board_posts WHERE id=?", (pid,)).fetchone()
            if not r:
                raise FileNotFoundError("없는 글입니다")
            if r["uid"] != ctx.uid:
                raise PermissionError("내가 쓴 글만 고칠 수 있습니다")
            c.execute("UPDATE board_posts SET title=?, body=?, updated=? WHERE id=?", (title, body, now(), pid))
            board_files.attach(c, ctx, "post", pid, req.get("files"))
            return {"ok": True, "id": pid, **modfilter.apply(c, "board_post", pid, ctx.uid, v)}
        pid = c.execute("INSERT INTO board_posts(uid, title, body, ts, updated) VALUES(?,?,?,?,?)", (ctx.uid, title, body, now(), now())).lastrowid
        board_files.attach(c, ctx, "post", pid, req.get("files"))
        return {"ok": True, "id": pid, **modfilter.apply(c, "board_post", pid, ctx.uid, v)}


def _drop_post(c, pid):
    board_files.drop(c, "comment", [r[0] for r in c.execute("SELECT id FROM board_comments WHERE post=?", (pid,))])
    board_files.drop(c, "post", [pid])
    c.execute("DELETE FROM board_reports WHERE kind='comment' AND target IN (SELECT id FROM board_comments WHERE post=?)", (pid,))
    for t in ("DELETE FROM board_comments WHERE post=?", "DELETE FROM board_alias WHERE post=?", "DELETE FROM board_reports WHERE kind='post' AND target=?",
              "DELETE FROM board_posts WHERE id=?"):
        c.execute(t, (pid,))


def delete(ctx, req):
    pid = _id(req.get("id"))
    with db() as c:
        r = c.execute("SELECT uid FROM board_posts WHERE id=?", (pid,)).fetchone()
        if not r:
            raise FileNotFoundError("없는 글입니다")
        if r["uid"] != ctx.uid and not ctx.is_owner:
            raise PermissionError("내가 쓴 글만 지울 수 있습니다")
        _drop_post(c, pid)
        if r["uid"] != ctx.uid:   # 관리자 삭제만 감사(쓴 사람 삭제는 남기지 않음 — actor 가 작성자라)
            accounts.audit(c, ctx.uid, "board_delete", f"board:post:{pid}", "(관리자)")
    return {"ok": True}


def comment(ctx, req):
    pid = _id(req.get("post"))
    body = community._text(req.get("body"), COMMENT_MAX, "댓글", required=True)
    with db() as c:
        comments.need_verified(c, ctx.uid)
        _need_agree(c, ctx.uid)
        r = _post_row(c, pid, ctx)
        _rate(c, "board_comments", ctx.uid, COMMENT_RATE, "댓글")
        par = comments.parent(c, "board_comments", "post=?", (pid,), req.get("parent_id"))
    v = modfilter.check(ctx, "board_comment", body)
    with db() as c:
        n = _alias(c, pid, ctx.uid, r["uid"])
        cid = c.execute("INSERT INTO board_comments(post, uid, body, ts, parent_id) VALUES(?,?,?,?,?)", (pid, ctx.uid, body, now(), par)).lastrowid
        board_files.attach(c, ctx, "comment", cid, req.get("files"))
        extra = modfilter.apply(c, "board_comment", cid, ctx.uid, v)
    if v.held:
        modfilter.notify_held("board_comment", cid)
    return {"ok": True, "id": cid, "author": _label(n), **extra}


def edit_comment(ctx, req):
    cid = _id(req.get("id"), "댓글")
    body = community._text(req.get("body"), COMMENT_MAX, "댓글", required=True)
    with db() as c:
        r = c.execute("SELECT uid, deleted FROM board_comments WHERE id=?", (cid,)).fetchone()
        if not r or r["deleted"]:
            raise FileNotFoundError("없는 댓글입니다")
        if r["uid"] != ctx.uid:
            raise PermissionError("내가 쓴 댓글만 고칠 수 있습니다")
    v = modfilter.check(ctx, "board_comment", body)
    with db() as c:
        c.execute("UPDATE board_comments SET body=?, updated=? WHERE id=?", (body, now(), cid))
        board_files.attach(c, ctx, "comment", cid, req.get("files"))
        extra = modfilter.apply(c, "board_comment", cid, ctx.uid, v)
    if v.held:
        modfilter.notify_held("board_comment", cid)
    return {"ok": True, **extra}


def delete_comment(ctx, req):
    cid = _id(req.get("id"), "댓글")
    with db() as c:
        r = c.execute("SELECT uid, parent_id, deleted FROM board_comments WHERE id=?", (cid,)).fetchone()
        if not r or r["deleted"]:
            raise FileNotFoundError("없는 댓글입니다")
        if r["uid"] != ctx.uid and not ctx.is_owner:
            raise PermissionError("내가 쓴 댓글만 지울 수 있습니다")
        c.execute("DELETE FROM board_reports WHERE kind='comment' AND target=?", (cid,))
        board_files.drop(c, "comment", [cid])
        if c.execute("SELECT 1 FROM board_comments WHERE parent_id=?", (cid,)).fetchone():   # 답글이 있으면 자리만('삭제된 댓글입니다')
            c.execute("UPDATE board_comments SET body='', deleted=1, hidden=0, held=0 WHERE id=?", (cid,))
        else:
            c.execute("DELETE FROM board_comments WHERE id=?", (cid,))   # 가명 번호(board_alias)는 그대로 — 다시 달아도 같은 번호
            if r["parent_id"] is not None and not c.execute("SELECT 1 FROM board_comments WHERE parent_id=?", (r["parent_id"],)).fetchone():
                c.execute("DELETE FROM board_comments WHERE id=? AND deleted=1", (r["parent_id"],))
        if r["uid"] != ctx.uid:
            accounts.audit(c, ctx.uid, "board_delete", f"board:comment:{cid}", "(관리자)")
    return {"ok": True}


def _target(c, kind, tid):
    if kind not in KINDS:
        raise ValueError("kind 는 post|comment")
    r = c.execute(f"SELECT * FROM {KINDS[kind]} WHERE id=?", (tid,)).fetchone()
    if not r:
        raise FileNotFoundError("없는 글입니다")
    return r


def report(ctx, req):
    kind, tid = req.get("kind"), _id(req.get("id"))
    reason = community._text(req.get("reason"), REASON_MAX, "신고 이유")
    community._rate(ctx.uid)
    with db() as c:
        r = _target(c, kind, tid)
        if (r["hidden"] and not ctx.is_owner) or (kind == "comment" and r["deleted"]):
            raise FileNotFoundError("없는 글입니다")
        if r["uid"] == ctx.uid:
            raise PermissionError("내 글은 신고할 수 없습니다")
        new = c.execute("INSERT OR IGNORE INTO board_reports VALUES(?,?,?,?,?)", (kind, tid, ctx.uid, reason, now())).rowcount
        n = _reports(c, kind, tid)
        auto = bool(new) and not r["hidden"] and n >= autohide_n(c)
        if auto:
            c.execute(f"UPDATE {KINDS[kind]} SET hidden=1 WHERE id=?", (tid,))
            accounts.audit(c, "system", "board_autohide", f"board:{kind}:{tid}", f"신고 {n}명")
        post = r["id"] if kind == "post" else r["post"]
    if new:
        try:
            j = jobs.upsert("owner", community.JOB_TOOL, "done", f"board-report-{kind}-{tid}-{secrets.token_hex(3)}",
                            f"자유게시판 {'글' if kind == 'post' else '댓글'} 신고 {n}건" + (" — 자동 숨김" if auto else ""),
                            f"/community/board/{post}", (reason or "이유 없음")[:400])
            if j:
                jobs.HUB.publish("job", j, j["user"])
        except Exception as e:
            print("board: 신고 알림 실패", type(e).__name__, flush=True)
    return {"ok": True, "already": not new, "hidden": auto}


def hide(ctx, req):
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    kind, tid, on = req.get("kind"), _id(req.get("id")), bool(req.get("hidden", True))
    with db() as c:
        _target(c, kind, tid)
        c.execute(f"UPDATE {KINDS[kind]} SET hidden=?" + ("" if on else ", held=0") + " WHERE id=?", (int(on), tid))   # 숨김을 풀면 보류도 풂
        accounts.audit(c, ctx.uid, "board_hide" if on else "board_unhide", f"board:{kind}:{tid}")
    return {"ok": True, "hidden": on}


def reveal(ctx, req):
    """관리자 작성자 확인 — 신고된 글·댓글에만, 이유 필수, 감사 기록 뒤에만 계정을 돌려줌"""
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    kind, tid = req.get("kind"), _id(req.get("id"))
    reason = community._text(req.get("reason"), REASON_MAX, "확인 이유", required=True)
    if len(reason) < 4:
        raise ValueError("확인 이유를 4자 이상 적어 주세요")
    with db() as c:
        r = _target(c, kind, tid)
        if not _reports(c, kind, tid):
            raise PermissionError("신고된 글만 작성자를 확인할 수 있습니다")
        accounts.audit(c, ctx.uid, "board_reveal", f"board:{kind}:{tid}", reason)
        u = c.execute("SELECT id, name, dept FROM users WHERE id=?", (r["uid"],)).fetchone()
    return {"ok": True, "uid": r["uid"], "name": (u["name"] if u else "") or r["uid"], "dept": (u["dept"] if u else "") or ""}


def settings(ctx, req):
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    n = req.get("autohide")
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 100:
        raise ValueError("autohide 는 1~100")
    with db() as c:
        c.execute("INSERT INTO board_settings VALUES('autohide', ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(n),))
        accounts.audit(c, ctx.uid, "board_settings", "board", f"autohide={n}")
    return {"ok": True, "autohide": n}


GETS = {"/api/community/board": lambda ctx, one: listing(ctx, one("q").strip(), one("offset") or 0),
        "/api/community/board/post": lambda ctx, one: get(ctx, one("id")),
        "/api/community/board/reports": lambda ctx, one: reports(ctx)}
POSTS = {"/api/community/board/agree": agree, "/api/community/board/save": save, "/api/community/board/delete": delete,
         "/api/community/board/comment": comment, "/api/community/board/comment/delete": delete_comment,
         "/api/community/board/comment/edit": edit_comment,
         "/api/community/board/report": report, "/api/community/board/hide": hide, "/api/community/board/reveal": reveal,
         "/api/community/board/settings": settings}
