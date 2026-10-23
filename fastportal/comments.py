"""커뮤니티 댓글(평평한 한 단) — 커뮤니티 글(활용 사례·팁·질문·공지, kind=post)·개선 요청(kind=request)·리뷰(kind=review)에.
자유게시판 댓글은 가명 규칙 때문에 board.py(board_comments)가 따로 맡음 — 화면은 같은 댓글 부품.

  쓸 수 있는 사람: '확인된 사용자'(accounts.is_verified — 소속 승인됨·대기 요청 없음, 관리자)만. 아니면 403 code=not_verified.
  볼 수 있는 것: 그 대상을 볼 수 있는 사람만(글 = posts._can_see, 요청·리뷰 = 숨김 아님 + 그 도구·에이전트를 볼 수 있음 community.target).
  쓴 사람 이름은 보이고(일반 글과 같음) 계정 id 는 안 나감. 고치기 = 쓴 사람, 지우기 = 쓴 사람·관리자, 숨기기 = 관리자, 신고 = 남의 댓글에 한 번(관리자 알림함).
  속도: 1시간에 COMMENT_RATE(30)개(DB 로 셈). 글자만(화면이 글자 그대로 그림).
  답글: 한 단만(parent_id = 같은 대상의 댓글, 답글에는 답글 못 닮 → 400). 답글이 남은 댓글을 지우면 '삭제된 댓글입니다' 자리만(deleted=1).
  거르기: modfilter — 욕설 422(code=profanity), 특정인 비방은 보류(held — 쓴 사람만 '확인 중').
API: GET /api/community/comments?kind=post|request|review&id= · POST /api/community/comment {kind,id,body,parent_id?} · /comment/edit {id,body} · /comment/delete {id}
     · /comment/hide {id,hidden}(관리자) · /comment/report {id,reason}"""
import os, secrets, time

from . import accounts, community, jobs, modfilter
from .db import db, now

KINDS = ("post", "request", "review")
BODY_MAX, REASON_MAX = 1000, 300
COMMENT_RATE = int(os.environ.get("COMMUNITY_COMMENT_RATE", "30"))
NOT_VERIFIED = "부서장 승인 후 댓글을 쓸 수 있습니다 — 계정 설정에서 소속 승인을 요청하세요"


class NotVerified(PermissionError):
    """확인된 사용자가 아님 — 응답 code=not_verified(community._err)"""


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, uid TEXT NOT NULL, body TEXT NOT NULL,
                                        ts TEXT, updated TEXT, hidden INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS comments_t ON comments(kind, target, id);
    CREATE INDEX IF NOT EXISTS comments_uid ON comments(uid, ts);
    CREATE TABLE IF NOT EXISTS comment_reports(comment INTEGER NOT NULL, uid TEXT NOT NULL, reason TEXT, ts TEXT, UNIQUE(comment, uid));""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(comments)")}
    for col, ddl in (("parent_id", "INTEGER"), ("deleted", "INTEGER NOT NULL DEFAULT 0")):   # 답글(한 단) · 답글이 남은 댓글을 지우면 자리만(삭제된 댓글입니다)
        if col not in cols:
            c.execute(f"ALTER TABLE comments ADD COLUMN {col} {ddl}")
    modfilter.add_held_column(c, "comments")


def need_verified(c, uid, what="댓글"):
    if not accounts.is_verified(c, uid):
        raise NotVerified(NOT_VERIFIED.replace("댓글", what, 1))


def _target(ctx, c, kind, tid):
    """대상을 볼 수 있는지 확인 → (대상 이름, 대상 주인 계정 또는 None). 볼 수 없으면 FileNotFoundError"""
    from . import posts
    if kind not in KINDS or not isinstance(tid, (str, int)):
        raise ValueError("kind 는 post|request|review")
    tid = str(tid)
    if kind == "post":
        r = c.execute("SELECT id, uid, draft, hidden, title FROM posts WHERE id=?", (posts._pid(tid),)).fetchone()
        if not r or not posts._can_see(ctx, r):
            raise FileNotFoundError("없는 글입니다")
        return r["title"], r["uid"]
    table = "requests_fb" if kind == "request" else "reviews"
    if not community.FLOW_RE.fullmatch(tid):
        raise FileNotFoundError("없는 글입니다")
    r = c.execute(f"SELECT * FROM {table} WHERE id=?", (tid,)).fetchone()
    if not r or (r["hidden"] and r["uid"] != ctx.uid and not ctx.is_owner):
        raise FileNotFoundError("없는 글입니다")
    try:
        community.target(ctx, r["kind"], r["target"])
    except (PermissionError, ValueError, LookupError):
        raise FileNotFoundError("없는 글입니다")
    return (r["title"] if kind == "request" else f"리뷰 ★{r['stars']}"), r["uid"]


def counts(c, kind, ids):
    """목록 카드용 {대상 id: 보이는 댓글 수(답글 포함, 지운 자리·숨김 빼고)}"""
    ids = [str(i) for i in ids][:200]
    if not ids:
        return {}
    q = f"SELECT target, COUNT(*) FROM comments WHERE kind=? AND hidden=0 AND deleted=0 AND target IN ({','.join('?' * len(ids))}) GROUP BY target"
    return {r[0]: r[1] for r in c.execute(q, [kind, *ids])}


def listing(ctx, kind, tid):
    with db() as c:
        _target(ctx, c, kind, tid)
        names = community._names(c)
        rep = {r[0] for r in c.execute("SELECT comment FROM comment_reports WHERE uid=?", (ctx.uid,))}
        q = "SELECT * FROM comments WHERE kind=? AND target=?" + ("" if ctx.is_owner else " AND (hidden=0 OR uid=?)") + " ORDER BY id LIMIT 500"
        out = []
        for m in c.execute(q, (kind, str(tid)) if ctx.is_owner else (kind, str(tid), ctx.uid)):
            gone = bool(m["deleted"])   # 답글이 남아 자리만 둔 지운 댓글 — 쓴 사람·내용 없음
            d = {"id": m["id"], "parent_id": m["parent_id"], "deleted": gone, "author": "" if gone else names.get(m["uid"], "알 수 없음"),
                 "body": "" if gone else m["body"], "ts": m["ts"], "updated": m["updated"],
                 "mine": not gone and m["uid"] == ctx.uid, "hidden": bool(m["hidden"]), "held": bool(m["held"]), "reported": m["id"] in rep}
            if ctx.is_owner:
                d["reports"] = c.execute("SELECT COUNT(*) FROM comment_reports WHERE comment=?", (m["id"],)).fetchone()[0]
            out.append(d)
        v = accounts.is_verified(c, ctx.uid)
    return {"comments": out, "can_comment": v, "not_verified": "" if v else NOT_VERIFIED, "admin": bool(ctx.is_owner)}


def _rate(c, uid):
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 3600))
    if c.execute("SELECT COUNT(*) FROM comments WHERE uid=? AND ts>=?", (uid, cut)).fetchone()[0] >= COMMENT_RATE:
        raise ValueError(f"댓글은 1시간에 {COMMENT_RATE}개까지 쓸 수 있습니다 — 잠시 뒤에 다시 해 주세요")


def parent(c, table, where, args, pid):
    """답글의 부모 — 같은 글의 댓글이고 그 자체가 답글이 아니어야 함(한 단). 아니면 ValueError(400)"""
    if pid is None:
        return None
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise ValueError("parent_id 는 댓글 id")
    r = c.execute(f"SELECT id, parent_id, deleted FROM {table} WHERE id=? AND {where}", (pid, *args)).fetchone()
    if not r:
        raise ValueError("이 글의 댓글에만 답글을 달 수 있습니다")
    if r["parent_id"] is not None:
        raise ValueError("답글에는 다시 답글을 달 수 없습니다")
    if r["deleted"]:
        raise ValueError("지운 댓글에는 답글을 달 수 없습니다")
    return pid


def write(ctx, req):
    kind, tid = req.get("kind"), req.get("id")
    body = community._text(req.get("body"), BODY_MAX, "댓글", required=True)
    with db() as c:
        need_verified(c, ctx.uid)
        _target(ctx, c, kind, tid)
        _rate(c, ctx.uid)
        pid = parent(c, "comments", "kind=? AND target=?", (kind, str(tid)), req.get("parent_id"))
    v = modfilter.check(ctx, "comment", body)   # 욕설이면 422, 특정인 비방이면 보류(LLM — DB 잠금 밖)
    with db() as c:
        cid = c.execute("INSERT INTO comments(kind, target, uid, body, ts, updated, parent_id) VALUES(?,?,?,?,?,?,?)",
                        (kind, str(tid), ctx.uid, body, now(), now(), pid)).lastrowid
        extra = modfilter.apply(c, "comment", cid, ctx.uid, v)
    if v.held:
        modfilter.notify_held("comment", cid)
    return {"ok": True, "id": cid, **extra}


def _own(c, ctx, cid, admin_ok):
    try:
        cid = int(cid)
    except (TypeError, ValueError):
        raise FileNotFoundError("없는 댓글입니다")
    r = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    if not r:
        raise FileNotFoundError("없는 댓글입니다")
    if r["uid"] != ctx.uid and not (admin_ok and ctx.is_owner):
        raise PermissionError("내가 쓴 댓글만 할 수 있습니다")
    return r


def edit(ctx, req):
    body = community._text(req.get("body"), BODY_MAX, "댓글", required=True)
    with db() as c:
        r = _own(c, ctx, req.get("id"), False)
        if r["deleted"]:
            raise FileNotFoundError("없는 댓글입니다")
    v = modfilter.check(ctx, "comment", body)
    with db() as c:
        c.execute("UPDATE comments SET body=?, updated=? WHERE id=?", (body, now(), r["id"]))
        extra = modfilter.apply(c, "comment", r["id"], ctx.uid, v)
    if v.held:
        modfilter.notify_held("comment", r["id"])
    return {"ok": True, **extra}


def delete(ctx, req):
    with db() as c:
        r = _own(c, ctx, req.get("id"), True)
        if r["deleted"]:
            raise FileNotFoundError("없는 댓글입니다")
        c.execute("DELETE FROM comment_reports WHERE comment=?", (r["id"],))
        if c.execute("SELECT 1 FROM comments WHERE parent_id=?", (r["id"],)).fetchone():   # 답글이 있으면 자리만 남김
            c.execute("UPDATE comments SET body='', deleted=1, hidden=0, held=0 WHERE id=?", (r["id"],))
        else:
            c.execute("DELETE FROM comments WHERE id=?", (r["id"],))
            if r["parent_id"] is not None and not c.execute("SELECT 1 FROM comments WHERE parent_id=?", (r["parent_id"],)).fetchone():
                c.execute("DELETE FROM comments WHERE id=? AND deleted=1", (r["parent_id"],))   # 마지막 답글이 지워지면 빈 자리도 치움
        if r["uid"] != ctx.uid:
            accounts.audit(c, ctx.uid, "comment_delete", f"{r['kind']}:{r['target']}", f"#{r['id']} (관리자)")
    return {"ok": True}


def hide(ctx, req):
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    on = bool(req.get("hidden", True))
    with db() as c:
        r = _own(c, ctx, req.get("id"), True)
        c.execute("UPDATE comments SET hidden=?" + ("" if on else ", held=0") + " WHERE id=?", (int(on), r["id"]))   # 숨김을 풀면 보류도 풂
        accounts.audit(c, ctx.uid, "comment_hide" if on else "comment_unhide", f"{r['kind']}:{r['target']}", f"#{r['id']}")
    return {"ok": True, "hidden": on}


def report(ctx, req):
    reason = community._text(req.get("reason"), REASON_MAX, "신고 이유")
    community._rate(ctx.uid)
    with db() as c:
        try:
            cid = int(req.get("id"))
        except (TypeError, ValueError):
            raise FileNotFoundError("없는 댓글입니다")
        r = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
        if not r or r["deleted"] or (r["hidden"] and not ctx.is_owner):
            raise FileNotFoundError("없는 댓글입니다")
        _target(ctx, c, r["kind"], r["target"])
        if r["uid"] == ctx.uid:
            raise PermissionError("내 댓글은 신고할 수 없습니다")
        new = c.execute("INSERT OR IGNORE INTO comment_reports VALUES(?,?,?,?)", (cid, ctx.uid, reason, now())).rowcount
        if new:
            accounts.audit(c, ctx.uid, "comment_report", f"{r['kind']}:{r['target']}", f"#{cid}")
    if new:
        link = f"/community/post/{r['target']}" if r["kind"] == "post" else "/community?cat=요청"
        try:
            j = jobs.upsert("owner", community.JOB_TOOL, "done", f"comment-report-{cid}-{secrets.token_hex(3)}", "댓글 신고", link, (reason or "이유 없음")[:400])
            if j:
                jobs.HUB.publish("job", j, j["user"])
        except Exception as e:
            print("comments: 신고 알림 실패", type(e).__name__, flush=True)
    return {"ok": True, "already": not new}


GETS = {"/api/community/comments": lambda ctx, one: listing(ctx, one("kind"), one("id"))}
POSTS = {"/api/community/comment": write, "/api/community/comment/edit": edit, "/api/community/comment/delete": delete,
         "/api/community/comment/hide": hide, "/api/community/comment/report": report}
