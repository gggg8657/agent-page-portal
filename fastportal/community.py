"""모두의 에이전트 커뮤니티 — 리뷰(별점·짧은 글)·개선/요청(+1 투표·상태)·기여 순위. 대상 키 (kind, id): agent(빌더 에이전트)·flow(저장한 흐름)·tool(포털 도구).

  볼 수 있는 대상에만 쓰고 읽음: tool = 목록에 있는(숨김 아님) 도구 · flow = 만든 사람만(흐름 공유 전) · agent = 빌더가 그 사람에게 보여 주는 것
    (빌더 GET api/agents/<id> 를 그 사람 이름 + 포털 서명으로 물어봄 — 200 이면 볼 수 있음, 응답의 mine 이면 자기 것)
  자기 대상에는 리뷰 못 씀. 리뷰는 대상마다 한 사람 1개(고치기·지우기). 요청 상태(접수→검토 중→반영됨/보류)는 대상 주인·관리자만.
  알림: 요청이 오면 대상 주인에게, 상태가 바뀌면 요청자에게 작업 알림(jobs, tool 'community').
  안전: 글 길이 상한, 사용자당 쓰기 속도 제한, 화면은 텍스트로만 그림(HTML 안 씀), 관리자 숨김·삭제, 감사 기록.
API: GET /api/community/summary?t=agent:<id>,tool:<dir>,… · /api/community/reviews?kind=&id= · /api/community/requests?kind=&id= | ?box=received|sent
     · /api/community/ranking
     POST /api/community/review {kind,id,stars,body} · /review/delete {id} · /review/hide {id,hidden}(관리자)
          /api/community/request {kind,id,type,title,body} · /request/status {id,status,note} · /request/vote {id,on} · /request/delete {id} · /request/hide {id,hidden}(관리자)"""
import json, os, re, secrets, threading, time, urllib.error, urllib.request, urllib.parse

from . import accounts, jobs, timeutil, tools, toolstate
from .db import db, now, own_row

KINDS = ("agent", "flow", "tool")
REQ_TYPES = {"improve": "개선", "bug": "버그", "feature": "새 기능"}
REQ_STATES = {"open": "접수", "review": "검토 중", "done": "반영됨", "hold": "보류"}
REVIEW_MAX, TITLE_MAX, BODY_MAX, NOTE_MAX = 500, 80, 2000, 300
RATE_N, RATE_S = int(os.environ.get("COMMUNITY_RATE_N", "20")), 600   # 10분에 쓰기 20번
AGENT_RE = re.compile(r"[0-9a-f]{6,16}")
FLOW_RE = re.compile(r"[A-Za-z0-9_-]{6,40}")
TOOL_RE = re.compile(r"[A-Za-z0-9_-]{1,60}")
JOB_TOOL = "community"
BUILDER = "agent-builder-local"


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, uid TEXT NOT NULL, stars INTEGER NOT NULL,
                                       body TEXT, created TEXT, updated TEXT, hidden INTEGER NOT NULL DEFAULT 0, UNIQUE(kind, target, uid));
    CREATE INDEX IF NOT EXISTS reviews_t ON reviews(kind, target);
    CREATE TABLE IF NOT EXISTS requests_fb(id TEXT PRIMARY KEY, kind TEXT NOT NULL, target TEXT NOT NULL, uid TEXT NOT NULL, owner TEXT,
                                           type TEXT NOT NULL, title TEXT NOT NULL, body TEXT, status TEXT NOT NULL, note TEXT,
                                           created TEXT, updated TEXT, status_by TEXT, status_ts TEXT, hidden INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS requests_fb_t ON requests_fb(kind, target);
    CREATE INDEX IF NOT EXISTS requests_fb_owner ON requests_fb(owner);
    CREATE TABLE IF NOT EXISTS request_votes(req TEXT NOT NULL, uid TEXT NOT NULL, ts TEXT, UNIQUE(req, uid));""")


# ── 속도 제한 ────────────────────────────────────────────────────────────
_RATE, _RATE_LOCK = {}, threading.Lock()   # shortcut: 프로세스 메모리 — 포털 프로세스가 여럿이면 글쓰기 제한이 그만큼 느슨, 그때 DB 로


def _rate(uid):
    t = time.time()
    with _RATE_LOCK:
        v = [x for x in _RATE.get(uid, ()) if x > t - RATE_S]
        if len(v) >= RATE_N:
            raise ValueError("글을 너무 자주 쓰고 있습니다 — 잠시 뒤에 다시 해 주세요")
        v.append(t)
        _RATE[uid] = v


def _text(v, n, label, required=False):
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ValueError(f"{label} 은(는) 글이어야 합니다")
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
    if required and not v:
        raise ValueError(f"{label} 을(를) 적어 주세요")
    if len(v) > n:
        raise ValueError(f"{label} 은(는) {n}자 이하로 적어 주세요")
    return v


# ── 대상: 볼 수 있는지·주인 ──────────────────────────────────────────────
def _builder(user, path, role=""):
    """빌더에 그 사람 이름 + 포털 서명(관리자 계정이면 서명된 역할도)으로 GET → (상태, JSON). 꺼져 있으면 (502, None)"""
    t = tools.tool(BUILDER)
    if not t or not tools.tool_host(t) or time.time() < _DOWN[0]:
        return 502, None
    hd = {**jobs.tool_headers(user, BUILDER, role), "Accept": "application/json"}
    try:
        with jobs.TOOL_OPENER.open(urllib.request.Request(f"http://{tools.tool_host(t)}:{int(t['port'])}{path}", headers=hd), timeout=5) as r:
            return r.status, json.loads(r.read(4 * 1024 * 1024) or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError) as e:
        if isinstance(getattr(e, "reason", e), TimeoutError):   # 느린 응답만 — 연결 거절(재시작 중)은 원래 바로 실패하니 막지 않음
            _DOWN[0] = time.time() + 30   # 30초 동안은 바로 502(목록 행마다 5초씩 기다리며 스레드를 잡지 않게)
        return 502, None
    except ValueError:
        return 502, None


_DOWN = [0.0]   # 빌더가 응답하지 않아 건너뛸 시각까지
_OWNERS = {}   # 에이전트 id → (주인 계정, 이름, 읽은 시각) — 5분 캐시


def _agent_info(aid):
    """빌더 에이전트의 (주인 계정, 이름) — 포털이 관리자 서명으로 물어봄(빌더가 다른 컨테이너여도 됨). 화면에는 계정 id 를 안 내보냄"""
    hit = _OWNERS.get(aid)
    if hit and time.time() - hit[2] < 300:
        return hit[0], hit[1]
    st, a = _builder("owner", f"/api/agents/{aid}")
    if st != 200 or not isinstance(a, dict):
        return None, None
    _OWNERS[aid] = (a.get("owner_id"), a.get("name"), time.time())
    return a.get("owner_id"), a.get("name")


def _agent_owner(aid):
    return _agent_info(aid)[0]


def target(ctx, kind, tid):
    """대상 확인 → {kind, id, name, owner(계정 id 또는 None), mine}. 볼 수 없으면 PermissionError, 형식이 틀리면 ValueError"""
    if kind not in KINDS or not isinstance(tid, str):
        raise ValueError("kind 는 agent|flow|tool, id 는 글")
    if kind == "tool":
        t = tools.tool(tid) if TOOL_RE.fullmatch(tid) else None
        if not t or t.get("hidden"):   # 숨긴 도구는 관리자도 리뷰·요청 대상이 아님
            raise PermissionError("볼 수 없는 도구입니다")
        if toolstate.is_off(tid) and not ctx.is_owner:   # 관리자가 끈 도구 — 일반 계정에는 없는 것처럼
            raise PermissionError("볼 수 없는 도구입니다")
        author = str(t.get("author") or "")
        with db() as c:
            owner = author if author and c.execute("SELECT 1 FROM users WHERE id=?", (author,)).fetchone() else None
        # 도구 주인 = 관리자 전원(+ tools.json author 가 포털 계정이면 그 계정). owner 열에는 author 계정(없으면 None) — 관리자는 늘 상태를 바꿀 수 있음
        return {"kind": kind, "id": tid, "name": t.get("name") or tid, "owner": owner, "mine": bool(owner) and owner == ctx.uid, "admin_owned": True}
    if kind == "flow":
        if not FLOW_RE.fullmatch(tid):
            raise ValueError("흐름 id 형식")
        with db() as c:
            r = c.execute("SELECT uid, name FROM flows WHERE id=?", (tid,)).fetchone()
        if not r or r["uid"] != ctx.uid:   # 흐름 공유 전 — 만든 사람만 봄
            raise PermissionError("볼 수 없는 흐름입니다")
        return {"kind": kind, "id": tid, "name": r["name"], "owner": r["uid"], "mine": True}
    if not AGENT_RE.fullmatch(tid):
        raise ValueError("에이전트 id 형식")
    st, a = _builder(ctx.agent_user, f"/api/agents/{tid}", ctx.agent_role)
    if st == 502:
        raise LookupError("에이전트 빌더가 꺼져 있습니다 — 홈에서 시작한 뒤 다시 해 주세요")
    if st != 200 or not isinstance(a, dict):
        raise PermissionError("볼 수 없는 에이전트입니다")
    return {"kind": kind, "id": tid, "name": a.get("name") or tid, "owner": _agent_owner(tid), "mine": bool(a.get("mine"))}


# ── 요약·목록 ────────────────────────────────────────────────────────────
def _names(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, name FROM users")} | {"owner": "관리자"}


def summary(c, keys, admin=False):
    """카드용 {"kind:id": {stars(평균), reviews, requests_open}} — 수만(내용 없음). 대상 확인은 하지 않음(무작위 id 의 수만 나감)"""
    out = {}
    for k in keys[:200]:
        kind, _, tid = k.partition(":")
        if kind not in KINDS or not tid or len(tid) > 60:
            continue
        r = c.execute("SELECT AVG(stars), COUNT(*) FROM reviews WHERE kind=? AND target=? AND hidden=0", (kind, tid)).fetchone()
        o = c.execute("SELECT COUNT(*) FROM requests_fb WHERE kind=? AND target=? AND hidden=0 AND status IN ('open','review')", (kind, tid)).fetchone()[0]
        out[k] = {"stars": round(r[0], 2) if r[0] else None, "reviews": r[1], "requests_open": o}
    return out


def _review_pub(r, names, me):
    return {"id": r["id"], "name": names.get(r["uid"], r["uid"]), "stars": r["stars"], "body": r["body"] or "", "created": r["created"], "updated": r["updated"],
            "mine": r["uid"] == me, "hidden": bool(r["hidden"])}


def reviews(ctx, kind, tid):
    tg = target(ctx, kind, tid)
    with db() as c:
        names = _names(c)
        q = "SELECT * FROM reviews WHERE kind=? AND target=?" + ("" if ctx.is_owner else " AND (hidden=0 OR uid=?)") + " ORDER BY updated DESC LIMIT 200"
        rows = c.execute(q, (kind, tid) if ctx.is_owner else (kind, tid, ctx.uid)).fetchall()
        s = summary(c, [f"{kind}:{tid}"])[f"{kind}:{tid}"]
    hist = {n: 0 for n in range(1, 6)}
    for r in rows:
        if not r["hidden"]:
            hist[r["stars"]] += 1
    return {"target": {k: tg[k] for k in ("kind", "id", "name", "mine")}, "summary": s, "histogram": hist,
            "reviews": [_review_pub(r, names, ctx.uid) for r in rows], "can_review": not tg["mine"]}


def _req_pub(c, r, names, me, admin):
    votes = c.execute("SELECT COUNT(*) FROM request_votes WHERE req=?", (r["id"],)).fetchone()[0]
    voted = bool(c.execute("SELECT 1 FROM request_votes WHERE req=? AND uid=?", (r["id"], me)).fetchone())
    return {"id": r["id"], "kind": r["kind"], "target": r["target"], "type": r["type"], "type_label": REQ_TYPES.get(r["type"], r["type"]),
            "title": r["title"], "body": r["body"] or "", "status": r["status"], "status_label": REQ_STATES.get(r["status"], r["status"]),
            "note": r["note"] or "", "name": names.get(r["uid"], r["uid"]), "mine": r["uid"] == me, "created": r["created"], "updated": r["updated"],
            "votes": votes, "voted": voted, "can_status": admin or r["owner"] == me, "hidden": bool(r["hidden"])}


def requests_for(ctx, kind, tid):
    tg = target(ctx, kind, tid)
    with db() as c:
        names = _names(c)
        q = "SELECT * FROM requests_fb WHERE kind=? AND target=?" + ("" if ctx.is_owner else " AND (hidden=0 OR uid=?)") + " ORDER BY created DESC LIMIT 200"
        rows = c.execute(q, (kind, tid) if ctx.is_owner else (kind, tid, ctx.uid)).fetchall()
        return {"target": {k: tg[k] for k in ("kind", "id", "name", "mine")}, "types": REQ_TYPES, "states": REQ_STATES,
                "requests": [_req_pub(c, r, names, ctx.uid, ctx.is_owner) for r in rows]}


def requests_box(ctx, box):
    """내 에이전트 탭: received(내 대상에 온 요청 — 숨김 제외) · sent(내가 쓴 요청)"""
    with db() as c:
        names = _names(c)
        if box == "received":   # 관리자는 도구 요청 전부(도구 주인 = 관리자)도 받음
            rows = c.execute("SELECT * FROM requests_fb WHERE (owner=? OR (? AND kind='tool')) AND (hidden=0 OR ?) ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'review' THEN 1 ELSE 2 END, created DESC LIMIT 200",
                             (ctx.uid, int(bool(ctx.is_owner)), int(bool(ctx.is_owner)))).fetchall()
        elif box == "sent":
            rows = c.execute("SELECT * FROM requests_fb WHERE uid=? ORDER BY created DESC LIMIT 200", (ctx.uid,)).fetchall()
        else:
            raise ValueError("box 는 received|sent")
        out = []
        off = set() if ctx.is_owner else toolstate.off()
        for r in rows:
            if r["kind"] == "tool" and r["target"] in off:   # 관리자가 끈 도구에 쓴 요청은 일반 계정 목록에서 뺌(지우지 않음)
                continue
            d = _req_pub(c, r, names, ctx.uid, ctx.is_owner)
            d["target_name"] = _target_name(c, r["kind"], r["target"])
            out.append(d)
        return {"box": box, "requests": out, "states": REQ_STATES, "types": REQ_TYPES}


def _target_name(c, kind, tid):
    if kind == "tool":
        t = tools.tool(tid)
        return (t or {}).get("name") or tid
    if kind == "flow":
        r = c.execute("SELECT name FROM flows WHERE id=?", (tid,)).fetchone()
        return r[0] if r else tid
    return (_agent_info(tid)[1] if AGENT_RE.fullmatch(tid) else None) or tid


# ── 쓰기 ─────────────────────────────────────────────────────────────────
def _notify(user, rid, status, title, msg):
    if not user:
        return
    try:
        j = jobs.upsert("owner" if accounts.is_admin(user) else user, JOB_TOOL, status, f"{rid}-{secrets.token_hex(3)}", title[:200], "/create?tab=mine", msg[:400])
        if j:
            jobs.HUB.publish("job", j, j["user"])
    except Exception as e:
        print("community: 알림 실패", type(e).__name__, e, flush=True)


def write_review(ctx, req):
    kind, tid = req.get("kind"), req.get("id")
    stars = req.get("stars")
    if not isinstance(stars, int) or isinstance(stars, bool) or not 1 <= stars <= 5:
        raise ValueError("별점은 1~5")
    body = _text(req.get("body"), REVIEW_MAX, "리뷰")
    tg = target(ctx, kind, tid)
    if tg["mine"]:
        raise PermissionError("자기 것에는 리뷰를 쓸 수 없습니다")
    _rate(ctx.uid)
    with db() as c:
        c.execute("INSERT INTO reviews(id, kind, target, uid, stars, body, created, updated) VALUES(?,?,?,?,?,?,?,?) "   # 한 사람 하나 — 동시에 두 번 와도(UNIQUE) 고쳐 씀
                  "ON CONFLICT(kind, target, uid) DO UPDATE SET stars=excluded.stars, body=excluded.body, updated=excluded.updated",
                  (secrets.token_urlsafe(9), kind, tid, ctx.uid, stars, body, now(), now()))
        rid = c.execute("SELECT id FROM reviews WHERE kind=? AND target=? AND uid=?", (kind, tid, ctx.uid)).fetchone()[0]
        accounts.audit(c, ctx.uid, "review_write", f"{kind}:{tid}", f"#{rid} ★{stars}")
        return {"ok": True, "id": rid, "summary": summary(c, [f"{kind}:{tid}"])[f"{kind}:{tid}"]}


def _own_row(c, table, rid, ctx, admin_ok=True):
    return own_row(c, table, rid, ctx.uid, "없습니다", "다른 사람의 글입니다", admin_ok and ctx.is_owner)


def delete_review(ctx, req):
    with db() as c:
        r = _own_row(c, "reviews", req.get("id"), ctx)
        c.execute("DELETE FROM reviews WHERE id=?", (r["id"],))
        accounts.audit(c, ctx.uid, "review_delete", f"{r['kind']}:{r['target']}", f"#{r['id']}" + (" (관리자)" if r["uid"] != ctx.uid else ""))
    return {"ok": True}


def hide(ctx, table, req):
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    with db() as c:
        r = _own_row(c, table, req.get("id"), ctx)
        on = bool(req.get("hidden", True))
        c.execute(f"UPDATE {table} SET hidden=? WHERE id=?", (int(on), r["id"]))
        accounts.audit(c, ctx.uid, ("review" if table == "reviews" else "request") + ("_hide" if on else "_unhide"), f"{r['kind']}:{r['target']}", f"#{r['id']}")
    return {"ok": True}


def write_request(ctx, req):
    kind, tid = req.get("kind"), req.get("id")
    typ = req.get("type")
    if typ not in REQ_TYPES:
        raise ValueError("종류는 improve|bug|feature")
    title = _text(req.get("title"), TITLE_MAX, "제목", required=True)
    body = _text(req.get("body"), BODY_MAX, "내용")
    tg = target(ctx, kind, tid)
    _rate(ctx.uid)
    rid = secrets.token_urlsafe(9)
    with db() as c:
        c.execute("INSERT INTO requests_fb(id, kind, target, uid, owner, type, title, body, status, created, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, kind, tid, ctx.uid, tg["owner"], typ, title, body, "open", now(), now()))
        accounts.audit(c, ctx.uid, "request_write", f"{kind}:{tid}", f"#{rid} {REQ_TYPES[typ]}")
    # 받는 사람: 대상 주인(author 계정) + 도구·주인 없는 대상은 관리자 알림함. 관리자 계정은 모두 'owner' 알림함 하나로(두 번 안 감), 보낸 사람 자신은 뺌
    to = set()
    if tg["owner"] and tg["owner"] != ctx.uid:
        to.add("owner" if accounts.is_admin(tg["owner"]) else tg["owner"])
    if (tg.get("admin_owned") or not tg["owner"]) and not ctx.is_owner:
        to.add("owner")
    for u in to:
        _notify(u, rid, "done", f"새 {REQ_TYPES[typ]} 요청: {tg['name']}", title)
    return {"ok": True, "id": rid}


def set_status(ctx, req):
    st = req.get("status")
    if st not in REQ_STATES:
        raise ValueError("상태는 open|review|done|hold")
    note = _text(req.get("note"), NOTE_MAX, "메모")
    with db() as c:
        if not isinstance(req.get("id"), str) or not FLOW_RE.fullmatch(req["id"]):
            raise FileNotFoundError("없습니다")
        r = c.execute("SELECT * FROM requests_fb WHERE id=?", (req["id"],)).fetchone()
        if not r:
            raise FileNotFoundError("없습니다")
        if not (ctx.is_owner or (r["owner"] and r["owner"] == ctx.uid)):
            raise PermissionError("대상을 만든 사람과 관리자만 상태를 바꿀 수 있습니다")
        c.execute("UPDATE requests_fb SET status=?, note=?, status_by=?, status_ts=?, updated=? WHERE id=?", (st, note or r["note"], ctx.uid, now(), now(), r["id"]))
        accounts.audit(c, ctx.uid, "request_status", f"{r['kind']}:{r['target']}", f"#{r['id']} {r['status']}→{st}")
        tname = _target_name(c, r["kind"], r["target"])
    if r["uid"] != ctx.uid and st != r["status"]:
        _notify(r["uid"], r["id"], "done", f"요청 상태: {REQ_STATES[st]} — {tname}", r["title"] + (f" · {note}" if note else ""))
    return {"ok": True, "status": st}


def vote(ctx, req):
    with db() as c:
        if not isinstance(req.get("id"), str) or not FLOW_RE.fullmatch(req["id"]):
            raise FileNotFoundError("없습니다")
        r = c.execute("SELECT * FROM requests_fb WHERE id=? AND hidden=0", (req["id"],)).fetchone()
        if not r:
            raise FileNotFoundError("없습니다")
    target(ctx, r["kind"], r["target"])   # 그 대상을 볼 수 있는 사람만
    if r["uid"] == ctx.uid:
        raise PermissionError("내 요청에는 투표할 수 없습니다")
    on = bool(req.get("on", True))
    with db() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO request_votes VALUES(?,?,?)", (r["id"], ctx.uid, now()))
        else:
            c.execute("DELETE FROM request_votes WHERE req=? AND uid=?", (r["id"], ctx.uid))
        n = c.execute("SELECT COUNT(*) FROM request_votes WHERE req=?", (r["id"],)).fetchone()[0]
    return {"ok": True, "votes": n, "voted": on}


def delete_request(ctx, req):
    with db() as c:
        r = _own_row(c, "requests_fb", req.get("id"), ctx)
        c.execute("DELETE FROM request_votes WHERE req=?", (r["id"],))
        c.execute("DELETE FROM requests_fb WHERE id=?", (r["id"],))
        accounts.audit(c, ctx.uid, "request_delete", f"{r['kind']}:{r['target']}", f"#{r['id']}" + (" (관리자)" if r["uid"] != ctx.uid else ""))
    return {"ok": True}


# ── 기여 순위(이달) ──────────────────────────────────────────────────────
W_USE, W_FORK, W_REVIEW, W_DONE = 1, 10, 5, 20


def ranking():
    """최근 30일 기여 점수 = 질문 수 + 10×복제당한 수 + 5×받은 리뷰 수 + 20×반영한 요청 수. 부서에 공개한 사람(빌더) + 리뷰·요청 받은 사람.
    응답에 계정 id 는 넣지 않음(이름·부서·수만)"""
    since = (timeutil.utcnow() - __import__("datetime").timedelta(days=30)).isoformat(timespec="seconds")
    st, b = _builder("owner", "/api/stats")
    people = {}
    for x in (b or {}).get("creators_detail") or []:
        people[x["uid"]] = {"name": x["name"], "dept": x["dept"], "agents": x["agents"], "uses_30d": x["uses_30d"], "forks_30d": x["forks_30d"], "reviews_30d": 0, "stars": None, "done_30d": 0}
    with db() as c:
        users = {r[0]: (r[1], r[2]) for r in c.execute("SELECT id, name, dept FROM users")}
        rv = {}
        for kind, tid, stars in c.execute("SELECT kind, target, stars FROM reviews WHERE hidden=0 AND created>=?", (since,)):
            o = _agent_owner(tid) if kind == "agent" else str((tools.tool(tid) or {}).get("author") or "") if kind == "tool" else \
                (c.execute("SELECT uid FROM flows WHERE id=?", (tid,)).fetchone() or [None])[0]
            if o:
                rv.setdefault(o, []).append(stars)
        done = {r[0]: r[1] for r in c.execute("SELECT owner, COUNT(*) FROM requests_fb WHERE status='done' AND status_ts>=? AND owner IS NOT NULL GROUP BY owner", (since,))}
    for uid in set(rv) | set(done):
        if uid not in people:
            if uid not in users and uid != "owner":
                continue
            n, d = users.get(uid, ("관리자", ""))
            people[uid] = {"name": n or uid, "dept": d or "", "agents": 0, "uses_30d": 0, "forks_30d": 0, "reviews_30d": 0, "stars": None, "done_30d": 0}
        p = people[uid]
        if uid in rv:
            p["reviews_30d"], p["stars"] = len(rv[uid]), round(sum(rv[uid]) / len(rv[uid]), 2)
        p["done_30d"] = done.get(uid, 0)
    for p in people.values():
        p["score"] = W_USE * p["uses_30d"] + W_FORK * p["forks_30d"] + W_REVIEW * p["reviews_30d"] + W_DONE * p["done_30d"]
    top = sorted(people.values(), key=lambda p: (-p["score"], p["name"]))[:10]
    return {"since": since, "weights": {"use": W_USE, "fork": W_FORK, "review": W_REVIEW, "request_done": W_DONE}, "builder": st == 200, "ranking": top}


# ── 라우팅 ───────────────────────────────────────────────────────────────
def _q(ctx):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)


def _err(ctx, fn):
    try:
        return ctx.send(fn())
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e), "code": "not_found"}, code=404)
    except LookupError as e:
        return ctx.send({"error": str(e), "code": "unavailable"}, code=503)
    except (ValueError, KeyError, TypeError) as e:
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다", "code": "bad_request"}, code=400)


def handle_get(ctx):
    from . import board, posts   # 글(posts.py)·자유게시판(board.py)이 이 모듈의 대상 확인·글 검사를 씀 — 순환 import 를 피해 여기서
    p, q = ctx.p, _q(ctx)
    one = lambda k: (q.get(k) or [""])[0]
    if p.startswith("/api/community/img/"):
        try:
            return posts.image(ctx)
        except FileNotFoundError as e:
            return ctx.send({"error": str(e), "code": "not_found"}, code=404)
    if p in posts.GETS:
        return _err(ctx, lambda: posts.GETS[p](ctx, one))
    if p in board.GETS:
        return _err(ctx, lambda: board.GETS[p](ctx, one))
    if p == "/api/community/summary":
        keys = [x for x in one("t").split(",") if x]
        def f():
            with db() as c:
                return {"summary": summary(c, keys)}
        return _err(ctx, f)
    if p == "/api/community/reviews":
        return _err(ctx, lambda: reviews(ctx, one("kind"), one("id")))
    if p == "/api/community/requests":
        return _err(ctx, lambda: requests_box(ctx, one("box")) if one("box") else requests_for(ctx, one("kind"), one("id")))
    if p == "/api/community/ranking":
        return _err(ctx, ranking)
    return ctx.send({"error": "not found"}, code=404)


POSTS = {"/api/community/review": write_review, "/api/community/review/delete": delete_review,
         "/api/community/review/hide": lambda ctx, req: hide(ctx, "reviews", req),
         "/api/community/request": write_request, "/api/community/request/status": set_status, "/api/community/request/vote": vote,
         "/api/community/request/delete": delete_request, "/api/community/request/hide": lambda ctx, req: hide(ctx, "requests_fb", req)}


def handle_post(ctx, req):
    from . import board, posts
    fn = POSTS.get(ctx.p) or posts.POSTS.get(ctx.p) or board.POSTS.get(ctx.p)
    if not fn:
        return ctx.send({"error": "not found"}, code=404)
    return _err(ctx, lambda: fn(ctx, req))
