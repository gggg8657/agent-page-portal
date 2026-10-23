"""부서·부서장·요청/승인·관리·감사·비활성화/익명화·탈퇴 요청·부서 보관 + 계정 설정·승인·관리 API(/api/account·/api/approvals·/api/admin).
포털 쪽 사용자 정보(users 표의 이름·부서·테마·비활성 등)는 계정 저장소가 무엇이든 여기서 다룬다 — 사용자 키는 아이디."""
import datetime, json, re, secrets, time

from . import auth, config, org, sessions, timeutil
from .auth.local import ITERS
from .config import OWNERS, ANON_AFTER_DAYS
from .db import db, now


# ── 외부 계정 저장소 로그인 → 포털 쪽 사용자 정보 ─────────────────────────────
def ensure_profile(c, user):
    """외부 저장소로 로그인한 사람의 포털 쪽 행(users)을 만들거나(처음) 그대로 둠 — PIN 열은 비움, 이름은 처음에만(포털에서 바꿀 수 있음).
    포털에서 비활성·정지·익명화한 계정이면 False(로그인 불가)"""
    r = c.execute("SELECT disabled FROM users WHERE id=?", (user.id,)).fetchone()
    if r:
        return not r["disabled"]
    c.execute("INSERT INTO users(id, name, dept, ts, onboarded) VALUES(?,?,?,?,0)", (user.id, (user.name or user.id)[:40], "", now()))
    return True


# ── 부서·역할·요청 ───────────────────────────────────────────────────────
KIND = {"dept_join": "소속", "dept_head": "소속 장 권한", "withdraw": "탈퇴 요청", "org_add": "조직 추가"}


def is_admin(uid):
    return uid == "owner" or uid in OWNERS


def is_verified(c, uid):
    """'확인된 사용자'(커뮤니티 댓글·자유게시판 쓰기 조건) — 판단은 이 함수 한 곳에서만.
    관리자이거나, 정지·비활성이 아니고 소속(users.unit_id — 소속 장·관리자가 승인한 것만 들어감)이 있으며 대기 중인 소속 요청(dept_join)이 없음.
    조직 모델이 바뀌면 여기만 고침(예전 부서 이름 users.dept 만 있는 계정도 소속으로 봄)"""
    if is_admin(uid):
        return True
    r = c.execute("SELECT unit_id, dept, disabled, deactivated FROM users WHERE id=?", (uid,)).fetchone()
    if not r or r["disabled"] or r["deactivated"] or not (r["unit_id"] or r["dept"]):
        return False
    return not c.execute("SELECT 1 FROM requests WHERE uid=? AND kind='dept_join' AND status='pending'", (uid,)).fetchone()


def audit(c, actor, action, target, detail=""):
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))


def heads_of(c, dept):
    """예전 호출 호환(이름) — 새 코드는 org.heads(unit_id)"""
    return [r[0] for r in c.execute("SELECT uid FROM dept_heads WHERE dept=?", (dept,))]


def my_heads(c, uid):
    return [r[0] for r in c.execute("SELECT dept FROM dept_heads WHERE uid=? ORDER BY dept", (uid,))]


def role(c, uid):
    return "관리자" if is_admin(uid) else "부서장" if my_heads(c, uid) else "일반"


def visible_depts(c):
    """예전 화면 호환(/api/depts·가입 dept): 고를 수 있는 단위 이름. 새 화면은 /api/org(계층)"""
    return [r[0] for r in c.execute("SELECT name FROM org_units WHERE active=1 ORDER BY name")]


def unit_arg(c, req, key="unit_id", legacy="dept"):
    """요청 본문의 단위 — unit_id(새) 또는 dept 이름(예전). 고를 수 있는(살아 있는) 단위만. 둘 다 없으면 None"""
    u = req.get(key)
    if u in (None, "") and req.get(legacy):
        u = org.by_name(c, str(req[legacy]))
        if not u:
            raise ValueError("목록에 있는 소속을 고르세요 (목록에 없으면 '조직 추가 요청')")
    if u in (None, ""):
        return None
    r = org.unit(c, str(u))
    if not r or not r["active"]:
        raise ValueError("목록에 있는 소속을 고르세요 (목록에 없으면 '조직 추가 요청')")
    return r["id"]


def route(c, r, nm):
    """요청이 갈 곳(화면 표시): 소속 요청은 고른 단위부터 위로 첫 장, 없으면 관리자"""
    if r["kind"] != "dept_join":
        return "관리자"
    at, hs = org.approvers(c, r["unit_id"], r["uid"])
    if not hs:
        return "관리자"
    return f"{org.label(c, at)} 장 " + ", ".join(nm.get(h, h) for h in hs)


def names(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, name FROM users")}


def req_rows(c, where, args=()):
    nm = names(c) | {"owner": "관리자(로컬)"}
    dis = display_ids(c)   # 익명화된 계정의 아이디는 deleted-… 로만
    return [dict(r) | {"uid": dis.get(r["uid"], r["uid"]), "approver": dis.get(r["approver"], r["approver"]), "uname": nm.get(r["uid"], r["uid"]), "approver_name": nm.get(r["approver"] or "", r["approver"] or ""), "label": KIND.get(r["kind"], r["kind"]),
                       "route": route(c, r, nm), "unit": org.info(c, r["unit_id"]) if r["kind"] != "org_add" else None,
                       "proposal": _proposal(c, r)}
            for r in c.execute(f"SELECT * FROM requests WHERE {where} ORDER BY id DESC LIMIT 200", args)]


def _proposal(c, r):
    """조직 추가 요청의 내용 {name, type, parent, parent_path, note} — 다른 요청은 None"""
    if r["kind"] != "org_add":
        return None
    try:
        d = json.loads(r["detail"] or "{}")
    except ValueError:
        d = {}
    return {"name": r["dept"] or "", "type": d.get("type") or "실", "parent": r["unit_id"], "parent_path": (org.info(c, r["unit_id"]) or {}).get("path", []),
            "note": d.get("note") or ""}


def new_request(c, uid, kind, unit_id):
    r = org.unit(c, unit_id)
    if not r or not r["active"]:
        raise ValueError("목록에 있는 소속을 고르세요 (목록에 없으면 '조직 추가 요청')")
    if c.execute("SELECT 1 FROM requests WHERE uid=? AND kind=? AND status='pending'", (uid, kind)).fetchone():
        raise ValueError("이미 대기 중인 요청이 있습니다 — 취소한 뒤 다시 요청하세요")
    c.execute("INSERT INTO requests(kind,uid,dept,status,ts,unit_id) VALUES(?,?,?,?,?,?)", (kind, uid, r["name"], "pending", now(), r["id"]))


def org_request(c, uid, req):
    """'목록에 없음 → 조직 추가 요청'(관리자에게). 승인되면 그 단위를 만들고 요청한 사람을 소속으로"""
    name = str(req.get("name") or "").strip()
    if not name or len(name) > 60 or org.NAME_BAD.search(name):
        raise ValueError("조직 이름을 확인하세요(1~60자)")
    parent = org.unit(c, str(req.get("parent") or ""))
    if not parent or not parent["active"]:
        raise ValueError("어느 조직 아래인지 고르세요")
    t = req.get("type") or "실"
    if t not in org.TYPES:
        raise ValueError("종류를 고르세요")
    if org.by_name(c, name):
        raise ValueError("같은 이름의 조직이 이미 있습니다 — 검색해서 고르세요")
    if c.execute("SELECT 1 FROM requests WHERE uid=? AND kind='org_add' AND status='pending'", (uid,)).fetchone():
        raise ValueError("이미 대기 중인 조직 추가 요청이 있습니다")
    c.execute("INSERT INTO requests(kind,uid,dept,status,ts,unit_id,detail) VALUES('org_add',?,?,'pending',?,?,?)",
              (uid, name, now(), parent["id"], json.dumps({"type": t, "note": str(req.get("note") or "")[:200]}, ensure_ascii=False)))


def can_decide(c, actor, r):
    if is_admin(actor):
        return True
    return r["kind"] == "dept_join" and r["uid"] != actor and actor in org.approvers(c, r["unit_id"], r["uid"])[1]


def decide(c, actor, rid, ok, note=""):
    r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] != "pending":
        raise ValueError("이미 처리됐거나 없는 요청입니다")
    if not can_decide(c, actor, r):
        raise PermissionError("이 요청을 처리할 권한이 없습니다")
    c.execute("UPDATE requests SET status=?, approver=?, note=?, decided_ts=?, notified=0 WHERE id=?", ("approved" if ok else "rejected", actor, note[:200], now(), rid))
    if ok and r["kind"] == "dept_join":
        u = org.unit(c, r["unit_id"])
        if not u or not u["active"]:
            raise ValueError("없어졌거나 비활성인 단위입니다 — 반려하세요")
        join(c, actor, r["uid"], u["id"])
    if ok and r["kind"] == "dept_head":
        u = org.unit(c, r["unit_id"])
        if not u or not u["active"]:
            raise ValueError("없어졌거나 비활성인 단위입니다 — 반려하세요")
        if (c.execute("SELECT unit_id FROM users WHERE id=?", (r["uid"],)).fetchone() or [None])[0] != u["id"]:
            raise ValueError("그 단위 소속이 아닙니다 — 반려하세요")
        org.grant_head(c, u["id"], r["uid"])
    if ok and r["kind"] == "org_add":
        p = _proposal(c, r)
        new = org.create(c, actor, p["name"], p["type"], p["parent"], f"사용자 요청 #{rid}" + (f" — {p['note']}" if p["note"] else ""))
        c.execute("UPDATE requests SET unit_id=? WHERE id=?", (new, rid))
        c.execute("UPDATE requests SET status='cancelled', decided_ts=?, note='조직 추가 요청 승인으로 대신함' WHERE uid=? AND kind='dept_join' AND status='pending'", (now(), r["uid"]))
        join(c, actor, r["uid"], new)
    audit(c, actor, ("approve" if ok else "reject") + ":" + r["kind"], r["uid"], f"{r['dept']} #{rid} {note[:200]}".strip())
    if ok and r["kind"] == "withdraw":   # 탈퇴 승인 = 비활성화(바로 지우지 않음). 익명화는 관리자가 따로 또는 기한 뒤 정리 작업
        deactivate(c, actor, r["uid"], f"본인 탈퇴 요청 #{rid}")


def join(c, actor, uid, unit_id):
    """소속 바꾸기 — 다른 단위의 장 권한은 회수"""
    org.set_member(c, uid, unit_id)
    for h in org.head_units(c, uid):
        if h != unit_id:
            org.revoke_head(c, h, uid); audit(c, actor, "head_revoke", uid, f"{org.label(c, h, h)} (소속 이동)")


# ── 삭제 대신: 비활성화 → 익명화 · 탈퇴 요청 · 부서 보관 ─────────────────────
# 비활성화: 로그인 불가(disabled=1 — portal.py 도 막힘)·세션 끊김·부서장 회수·대기 요청 취소. 관리자가 '다시 활성화'로 되돌릴 수 있음.
# 익명화(되돌릴 수 없음, 비활성 계정만): 이름·부서·PIN 해시·테마·접속 시각을 지우고 화면에는 deleted-<무작위> 로만 보임.
#   아이디(users.id)는 내부 키로 남김 — 감사 기록·요청·작업 기록·도구 데이터(_by, WORKSPACE/u/<id>)의 '만든 사람' 연결이 그대로이고,
#   같은 아이디로 다시 가입할 수 없어(행이 남음) 남이 그 데이터를 물려받지 못함.
# 정리 작업: PORTAL_ANON_AUTO=1 일 때만, 비활성화한 지 PORTAL_ANON_AFTER_DAYS(기본 365)일 지난 계정을 익명화(하루 한 번, 기본 꺼짐)
ANON_NAME = "탈퇴한 사용자"


def _user(c, uid):
    r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not r:
        raise ValueError("없는 계정")
    return r


def _drop_ties(c, actor, uid, why):
    """세션·로그인 실패 기록·부서장 권한·대기 요청 정리(비활성화·익명화 공통)"""
    sessions.drop_user_sessions(c, uid)
    c.execute("DELETE FROM sess_short WHERE token NOT IN (SELECT token FROM sessions)")
    for d in my_heads(c, uid):
        c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, uid)); audit(c, actor, "head_revoke", uid, f"{d} ({why})")
    c.execute("UPDATE requests SET status='cancelled', decided_ts=?, note=? WHERE uid=? AND status='pending'", (now(), why, uid))


def deactivate(c, actor, uid, reason=""):
    r = _user(c, uid)
    if uid in OWNERS:
        raise ValueError("관리자 계정은 비활성화할 수 없습니다")
    if r["anonymized"]:
        raise ValueError("이미 익명화된 계정입니다")
    if r["deactivated"]:
        raise ValueError("이미 비활성 계정입니다")
    reason = str(reason or "").strip()[:200]
    c.execute("UPDATE users SET disabled=1, deactivated=?, deact_reason=? WHERE id=?", (now(), reason, uid))
    _drop_ties(c, actor, uid, "계정 비활성화")
    audit(c, actor, "deactivate", uid, reason)


def reactivate(c, actor, uid):
    r = _user(c, uid)
    if r["anonymized"]:
        raise ValueError("익명화된 계정은 되살릴 수 없습니다")
    if not r["deactivated"]:
        raise ValueError("비활성 계정이 아닙니다")
    c.execute("UPDATE users SET disabled=0, deactivated=NULL, deact_reason=NULL WHERE id=?", (uid,))
    audit(c, actor, "reactivate", uid)


def anonymize(c, actor, uid, reason):
    r = _user(c, uid)
    reason = str(reason or "").strip()[:200]
    if not reason:
        raise ValueError("익명화 이유를 적어 주세요")
    if r["anonymized"]:
        raise ValueError("이미 익명화된 계정입니다")
    if not r["deactivated"]:
        raise ValueError("먼저 비활성화한 계정만 익명화할 수 있습니다")
    if uid in OWNERS:
        raise ValueError("관리자 계정은 익명화할 수 없습니다")
    while True:
        aid = "deleted-" + secrets.token_hex(5)
        if not c.execute("SELECT 1 FROM users WHERE anon_id=? OR id=?", (aid, aid)).fetchone():
            break
    junk = secrets.token_bytes(16)   # PIN 해시를 무작위로 — 어떤 PIN 으로도 로그인 안 됨
    c.execute("UPDATE users SET name=?, dept='', unit_id=NULL, salt=?, hash=?, iters=?, theme=NULL, seen=NULL, disabled=1, deact_reason=NULL, anonymized=?, anon_id=? WHERE id=?",
              (ANON_NAME, junk, secrets.token_bytes(32), ITERS, now(), aid, uid))
    c.execute("DELETE FROM login_fails WHERE uid=?", (uid,))
    _drop_ties(c, actor, uid, "계정 익명화")
    audit(c, actor, "anonymize", uid, f"{aid} {reason}")


def display_ids(c):
    """화면에 보일 아이디: 익명화된 계정은 deleted-…"""
    return {r[0]: r[1] for r in c.execute("SELECT id, anon_id FROM users WHERE anonymized IS NOT NULL AND anon_id IS NOT NULL")}


def anon_sweep(days=None, actor="system"):
    """비활성화한 지 days 일 지난 계정 익명화 — 정리 작업(PORTAL_ANON_AUTO=1 일 때만 app 이 하루 한 번 부름). 익명화한 아이디 목록"""
    days = ANON_AFTER_DAYS if days is None else days
    cut = (timeutil.utcnow() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
    done = []
    with db() as c:
        for r in c.execute("SELECT id FROM users WHERE deactivated IS NOT NULL AND deactivated<=? AND anonymized IS NULL", (cut,)).fetchall():
            if r[0] in OWNERS:
                continue
            anonymize(c, actor, r[0], f"비활성 {days}일 지남(자동 정리)")
            done.append(r[0])
    return done


def anon_loop():
    while True:
        try:
            n = anon_sweep()
            if n:
                print(f"anon_sweep: {len(n)}개 계정 익명화(비활성 {ANON_AFTER_DAYS}일 지남)", flush=True)
        except Exception as e:
            print("anon_sweep:", type(e).__name__, e, flush=True)
        time.sleep(86400)


def withdraw_request(c, uid, reason=""):
    if is_admin(uid):
        raise ValueError("관리자 계정은 탈퇴 요청할 수 없습니다")
    r = _user(c, uid)
    if c.execute("SELECT 1 FROM requests WHERE uid=? AND kind='withdraw' AND status='pending'", (uid,)).fetchone():
        raise ValueError("이미 대기 중인 탈퇴 요청이 있습니다 — 취소는 '내 요청'에서")
    reason = str(reason or "").strip()[:200]
    c.execute("INSERT INTO requests(kind,uid,dept,status,note,ts) VALUES(?,?,?,?,?,?)", ("withdraw", uid, r["dept"] or "", "pending", reason, now()))
    audit(c, uid, "withdraw_request", uid, reason)


def dept_archived(c, name):
    r = c.execute("SELECT archived FROM depts WHERE name=?", (name,)).fetchone()
    return bool(r and r[0])


def archive_dept(c, actor, name):
    r = c.execute("SELECT archived FROM depts WHERE name=?", (name,)).fetchone()
    if not r:
        raise ValueError("없는 부서")
    if r[0]:
        raise ValueError("이미 보관된 부서입니다")
    mem = [x[0] for x in c.execute("SELECT name FROM users WHERE dept=? AND deactivated IS NULL AND anonymized IS NULL ORDER BY name", (name,))]
    pend = c.execute("SELECT COUNT(*) FROM requests WHERE dept=? AND status='pending'", (name,)).fetchone()[0]
    why = ([f"소속 인원 {len(mem)}명({', '.join(mem[:5])}{' …' if len(mem) > 5 else ''})"] if mem else []) + ([f"대기 요청 {pend}건"] if pend else [])
    if why:
        raise ValueError(" · ".join(why) + " 이 있어 보관할 수 없습니다 — 다른 부서로 옮기거나 요청을 처리한 뒤 다시 하세요")
    for h in heads_of(c, name):
        c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (name, h)); audit(c, actor, "head_revoke", h, f"{name} (부서 보관)")
    c.execute("UPDATE depts SET archived=?, hidden=1 WHERE name=?", (now(), name))


TUT_ID = re.compile(r"[a-z0-9-]{1,40}")


def tut_seen(raw):
    """users.tut_seen(JSON) → {따라 하기 id: 본 판}. 깨진 값은 빈 것으로"""
    try:
        v = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {k: n for k, n in v.items() if isinstance(k, str) and TUT_ID.fullmatch(k) and isinstance(n, int)} if isinstance(v, dict) else {}


def me(c, uid, uname):
    is_owner = is_admin(uid)
    u = c.execute("SELECT dept, onboarded, tour_seen, tut_seen FROM users WHERE id=?", (uid,)).fetchone()
    pend = c.execute("SELECT dept, unit_id FROM requests WHERE uid=? AND kind='dept_join' AND status='pending'", (uid,)).fetchone()
    unit = c.execute("SELECT unit_id FROM users WHERE id=?", (uid,)).fetchone()
    n = sum(can_decide(c, uid, r) for r in c.execute("SELECT * FROM requests WHERE status='pending'"))
    return {"id": uid, "name": uname, "owner": is_owner, "agent_user": uid, "role": role(c, uid),
            "dept": u["dept"] if u else "", "dept_pending": pend["dept"] if pend else "", "heads": my_heads(c, uid), "approvals": n,
            # 계층 소속 — dept·dept_pending·heads 는 예전 도구 호환(단위 이름). path 는 원장·부원장을 뺀 위→아래 이름
            "unit": org.info(c, unit[0]) if unit else None, "unit_pending": org.info(c, pend["unit_id"]) if pend else None,
            "head_units": [org.info(c, h) for h in org.head_units(c, uid)],
            "verified": is_verified(c, uid),   # 커뮤니티 댓글·자유게시판 쓰기 가능(소속 승인됨·대기 요청 없음, 관리자)
            "account": bool(u), "onboarded": bool(u["onboarded"]) if u else True,
            "tour_seen": (u["tour_seen"] or 0) if u else config.TOUR_VERSION, "tour_version": config.TOUR_VERSION,
            "tutorials": tut_seen(u["tut_seen"]) if u else None,
            # 전에 연 적 있는 도구(events open) — 처음 여는 도구에서만 따라 하기를 저절로(이미 쓰던 도구는 안 띄움)
            "used": [r[0] for r in c.execute("SELECT DISTINCT dir FROM events WHERE user=? AND kind='open'", (uid,))] if u else [],
            "developer": bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='developers'").fetchone() and c.execute("SELECT 1 FROM developers WHERE uid=?", (uid,)).fetchone())}


# ── 계정 설정·승인·관리 API (ctx: app.Ctx — send·uid·uname·is_owner·theme) ─────
def manage_get(ctx, p):
    with db() as c:
        if p == "/api/account":
            return ctx.send({"me": me(c, ctx.uid, ctx.uname), "depts": visible_depts(c), "units": org.public(c), "requests": req_rows(c, "uid=?", (ctx.uid,))[:20]})
        if p == "/api/account/theme":
            return ctx.send({"theme": ctx.theme(), "themes": sorted(config.THEMES)})
        if p == "/api/account/notices":   # 예전 화면 호환: GET 도 읽음 처리. 새 화면은 POST(같은 응답) 또는 GET ?peek=1(읽음 처리 안 함)
            return notices(ctx, c, mark="peek=1" not in ctx.raw.partition("?")[2].split("&"))
        if p == "/api/approvals":
            heads = org.head_units(c, ctx.uid)
            if not (ctx.is_owner or heads):
                return ctx.send({"error": "소속 장·관리자만 볼 수 있습니다"}, code=403)
            pend = [r for r in req_rows(c, "status='pending'") if can_decide(c, ctx.uid, r)]
            cut = timeutil.kst_day_start_utc(29)   # 최근 30일(오늘 포함, KST 달력) — 저장값은 UTC
            members, units = {}, []   # 장인 단위마다 그 단위와 하위 단위 소속(키는 예전 화면 호환으로 단위 이름)
            for h in heads:
                sub = sorted(org.descendants(c, h))
                rows = [dict(r) | {"unit": org.label(c, r["unit_id"])} for r in c.execute(
                    "SELECT u.id, u.name, u.seen, u.unit_id, (SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open') total, "
                    "(SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open' AND e.ts>=?) recent, "
                    "EXISTS(SELECT 1 FROM dept_heads h WHERE h.unit_id=u.unit_id AND h.uid=u.id) head FROM users u "
                    f"WHERE u.unit_id IN ({','.join('?' * len(sub))}) AND NOT u.disabled ORDER BY u.name", (cut, *sub))]
                i = org.info(c, h)
                members[i["name"]] = rows
                units.append(i | {"members": rows})
            done = [r for r in req_rows(c, "status IN ('approved','rejected') AND approver=?", (ctx.uid,))][:20]
            return ctx.send({"pending": pend, "members": members, "units": units, "done": done})
        if p == "/api/admin/org":   # 조직 관리 — 전체 단위(비활성 포함)·장·소속 수·최근 가져오기
            if not ctx.is_owner:
                return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
            return ctx.send(org.admin_view(c))
        if p == "/api/admin":
            if not ctx.is_owner:
                return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
            heads = {}
            for r in c.execute("SELECT dept, uid FROM dept_heads"):
                heads.setdefault(r["uid"], []).append(r["dept"])
            dis = display_ids(c)   # 익명화된 계정은 아이디도 deleted-… 로만 내보냄(감사 기록의 누가·대상 포함)
            devs = {r[0] for r in c.execute("SELECT uid FROM developers")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='developers'").fetchone() else set()
            users = [dict(r) | {"id": dis.get(r["id"], r["id"]), "developer": r["id"] in devs, "role": "관리자" if r["id"] in OWNERS else "부서장" if r["id"] in heads else "일반", "heads": heads.get(r["id"], []),
                                "unit": org.info(c, r["unit_id"])}
                     for r in c.execute("SELECT id, name, dept, unit_id, ts, seen, disabled, deactivated, deact_reason, anonymized FROM users ORDER BY ts")]
            depts = [dict(r) | {"members": c.execute("SELECT COUNT(*) FROM users WHERE dept=?", (r["name"],)).fetchone()[0], "heads": heads_of(c, r["name"])}
                     for r in c.execute("SELECT name, hidden, archived FROM depts ORDER BY hidden, name")]
            aud = [dict(r) | {"actor": dis.get(r["actor"], r["actor"]), "target": dis.get(r["target"], r["target"])}
                   for r in c.execute("SELECT * FROM audit ORDER BY rowid DESC LIMIT 60")]
            return ctx.send({"users": users, "depts": depts, "pending": req_rows(c, "status='pending'"), "owners": sorted(OWNERS),
                             "audit": aud, "anon_after_days": config.ANON_AFTER_DAYS, "anon_auto": config.ANON_AUTO,
                             "auth_backend": auth.backend().info()})   # 계정 저장소(관리자만 봄): local = 임시(포털 내부 DB)
    return ctx.send({"error": "not found"}, code=404)


def notices(ctx, c, mark=True):
    """승인·반려 알림(아직 안 본 것) + 내가 처리할 승인 수. mark 면 본 것으로 표시"""
    rows = req_rows(c, "uid=? AND status IN ('approved','rejected') AND notified=0", (ctx.uid,))
    if mark:
        c.execute("UPDATE requests SET notified=1 WHERE uid=? AND status IN ('approved','rejected')", (ctx.uid,))
    return ctx.send({"notices": rows, "approvals": me(c, ctx.uid, ctx.uname)["approvals"]})


def manage_post(ctx, c, p, req):
    uid = ctx.uid
    if p == "/api/account/notices":   # 알림 받고 읽음 처리(상태를 바꾸므로 POST 가 정식)
        return notices(ctx, c)
    acct = c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
    if p == "/api/account/theme":
        t = str(req.get("theme", ""))
        if t not in config.THEMES:
            raise ValueError("없는 테마")
        if acct:
            c.execute("UPDATE users SET theme=? WHERE id=?", (t, uid))
        return ctx.send({"ok": True, "theme": t}, headers=[("Set-Cookie", f"agtheme={t}; Path=/; Max-Age=31536000; SameSite={config.COOKIE_SAMESITE}" + ("; Secure" if config.COOKIE_SECURE else ""))])
    if p.startswith("/api/account/"):
        if not acct:
            raise ValueError("로컬 접속은 계정이 없습니다 — 계정으로 로그인하세요")
        if p == "/api/account/name":
            nm = str(req.get("name", "")).strip()[:40]
            if not nm:
                raise ValueError("이름을 적어 주세요")
            c.execute("UPDATE users SET name=? WHERE id=?", (nm, uid))
            sessions.invalidate_user(uid)   # 세션 확인 캐시에 든 이름도 바로 바뀌게
        elif p in ("/api/account/unit", "/api/account/dept"):   # 소속 변경 요청 {unit_id} (예전 {dept: 이름}도)
            d = unit_arg(c, req)
            if not d:
                raise ValueError("소속을 고르세요")
            if d == c.execute("SELECT unit_id FROM users WHERE id=?", (uid,)).fetchone()[0]:
                raise ValueError("이미 그 소속입니다")
            new_request(c, uid, "dept_join", d)
        elif p == "/api/account/org-request":   # 목록에 없는 조직 추가 요청 {name, parent, type?, note?} — 관리자에게
            org_request(c, uid, req)
        elif p == "/api/account/head":
            d = c.execute("SELECT unit_id FROM users WHERE id=?", (uid,)).fetchone()[0]
            if not d:
                raise ValueError("소속이 승인된 뒤에 요청할 수 있습니다")
            if uid in org.heads(c, d):
                raise ValueError("이미 소속 장입니다")
            new_request(c, uid, "dept_head", d)
        elif p == "/api/account/onboarded":   # done=true: 둘러보기 마침·건너뜀·'새 기능 둘러보기' 안내 닫음(이 판은 본 것으로) / false: 다음 로그인 때 다시 자동으로
            if req.get("done", True):
                c.execute("UPDATE users SET onboarded=1, tour_seen=MAX(COALESCE(tour_seen, 0), ?) WHERE id=?", (config.TOUR_VERSION, uid))
            else:
                c.execute("UPDATE users SET onboarded=0 WHERE id=?", (uid,))
        elif p == "/api/account/tutorial":   # 따라 하기 본 것 기록 {id, version} / 지우기 {reset: true}(계정 설정 '사용법 진행 기록 초기화')
            c.execute("UPDATE users SET tut_seen=tut_seen WHERE id=?", (uid,))   # 먼저 쓰기 잠금 — 동시에 온 기록이 읽고-고치고-쓰는 사이에 서로 지우지 않게
            seen = {} if req.get("reset") is True else tut_seen(c.execute("SELECT tut_seen FROM users WHERE id=?", (uid,)).fetchone()[0])
            if req.get("reset") is not True:
                tid, ver = str(req.get("id") or ""), req.get("version", 1)
                if not TUT_ID.fullmatch(tid) or not isinstance(ver, int) or isinstance(ver, bool) or not 1 <= ver <= 1000:
                    raise ValueError("id 는 영문 소문자·숫자·- 1~40자, version 은 1~1000 정수")
                if tid not in seen and len(seen) >= 200:
                    raise ValueError("따라 하기 기록이 너무 많습니다")
                seen[tid] = max(ver, seen.get(tid, 0))
            c.execute("UPDATE users SET tut_seen=? WHERE id=?", (json.dumps(seen, separators=(",", ":")) if seen else None, uid))
            return ctx.send({"ok": True, "tutorials": seen})
        elif p == "/api/account/withdraw":   # 탈퇴 요청 — 관리자 승인 목록으로(바로 지우지 않음). 승인되면 비활성화
            withdraw_request(c, uid, req.get("reason", ""))
        elif p == "/api/account/cancel":
            if not c.execute("UPDATE requests SET status='cancelled', decided_ts=? WHERE id=? AND uid=? AND status='pending'", (now(), int(req.get("id") or 0), uid)).rowcount:
                raise ValueError("취소할 수 있는 요청이 아닙니다")
        else:
            return ctx.send({"error": "not found"}, code=404)
        return ctx.send({"ok": True})
    if p == "/api/approvals/decide":
        decide(c, uid, int(req.get("id") or 0), bool(req.get("ok")), str(req.get("note") or "").strip())
        return ctx.send({"ok": True})
    if not p.startswith("/api/admin/"):
        return ctx.send({"error": "not found"}, code=404)
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    if p == "/api/admin/dept":
        op, name, new = req.get("op"), str(req.get("name", "")).strip()[:40], str(req.get("new", "")).strip()[:40]
        if op in ("add", "rename"):   # 예전 부서 표 — 소속은 이제 계층 조직(org_units)이 정함
            raise ValueError("부서는 이제 조직 관리(/api/admin/org)에서 더하고 고칩니다")
        if op == "add":
            if not name or re.search(r"[<>\"'&]", name):
                raise ValueError("부서 이름을 확인하세요")
            if dept_archived(c, name):
                raise ValueError("보관된 부서입니다 — '보관' 탭에서 되살리세요")
            c.execute("INSERT INTO depts(name, ts) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET hidden=0", (name, now()))
        elif op == "rename":
            if not new or re.search(r"[<>\"'&]", new) or c.execute("SELECT 1 FROM depts WHERE name=?", (new,)).fetchone():
                raise ValueError("새 이름이 비었거나 이미 있습니다")
            if not c.execute("UPDATE depts SET name=? WHERE name=?", (new, name)).rowcount:
                raise ValueError("없는 부서")
            for t in ("users", "dept_heads", "requests"):
                c.execute(f"UPDATE {t} SET dept=? WHERE dept=?", (new, name))
        elif op in ("hide", "show"):
            if op == "show" and dept_archived(c, name):
                raise ValueError("보관된 부서입니다 — '되살리기'로")
            c.execute("UPDATE depts SET hidden=? WHERE name=?", (op == "hide", name))
        elif op == "archive":   # 보관: 소속 인원·대기 요청이 있으면 막음. 가입 목록·선택지에서 빠짐(hidden=1), 되살리기 가능
            archive_dept(c, uid, name)
        elif op == "restore":
            if not c.execute("UPDATE depts SET archived=NULL, hidden=0 WHERE name=? AND archived IS NOT NULL", (name,)).rowcount:
                raise ValueError("보관된 부서가 아닙니다")
        else:
            raise ValueError("op")
        audit(c, uid, "dept_" + op, name, new)
    elif p == "/api/admin/org":   # 단위 더하기·고치기·옮기기·비활성·되살리기
        return ctx.send({"ok": True} | org.admin_post(c, uid, req))
    elif p == "/api/admin/member":   # 관리자가 소속을 바로 정함 {uid, unit_id|null} — 대기 중인 소속 요청은 취소
        u = str(req.get("uid", ""))
        if not c.execute("SELECT 1 FROM users WHERE id=? AND anonymized IS NULL", (u,)).fetchone():
            raise ValueError("없는 계정")
        d = unit_arg(c, req)
        join(c, uid, u, d)
        c.execute("UPDATE requests SET status='cancelled', decided_ts=?, note='관리자가 소속을 정함' WHERE uid=? AND kind='dept_join' AND status='pending'", (now(), u))
        audit(c, uid, "member_set", u, org.label(c, d, "소속 없음"))
    elif p == "/api/admin/head":   # {op: grant|revoke, uid, unit_id} (예전 {dept: 이름}도)
        u = str(req.get("uid", ""))
        d = req.get("unit_id") or org.by_name(c, str(req.get("dept", "")))
        if not org.unit(c, d):
            raise ValueError("없는 단위")
        if req.get("op") == "grant":
            if not org.unit(c, d)["active"]:
                raise ValueError("비활성 단위입니다")
            if not c.execute("SELECT 1 FROM users WHERE id=? AND unit_id=?", (u, d)).fetchone():
                raise ValueError("그 단위 소속(승인된) 계정만 장이 될 수 있습니다")
            org.grant_head(c, d, u)
        elif req.get("op") == "revoke":
            org.revoke_head(c, d, u)
        else:
            raise ValueError("op")
        audit(c, uid, "head_" + req["op"], u, org.label(c, d, d))
    elif p in ("/api/admin/reset", "/api/admin/disable"):
        u = str(req.get("uid", ""))
        if not c.execute("SELECT 1 FROM users WHERE id=?", (u,)).fetchone():
            raise ValueError("없는 계정")
        if p.endswith("reset"):
            if c.execute("SELECT anonymized FROM users WHERE id=?", (u,)).fetchone()[0]:
                raise ValueError("익명화된 계정입니다")
            b = auth.backend()
            if not b.supports_password_change:
                raise auth.Unsupported(b.guide)
            # 새 PIN 을 응답으로 주므로 관리자 본인 PIN 을 한 번 더 — 같은 출처 도구의 XSS 가 관리자 화면에서 이 API 를 불러 계정을 가져가지 못하게.
            # 로컬 토큰(스크립트)은 계정·PIN 이 없음
            if not ctx.local_owner() and (sessions.limited(c, uid, ctx.client or "") or not b.verify(c, uid, str(req.get("my_pin", "")))):
                sessions.add_fail(uid, ctx.client or "")
                raise ValueError("내 PIN 이 틀립니다 — PIN 초기화는 관리자 PIN 을 한 번 더 확인합니다 (10분에 5번까지)")
            pin = f"{secrets.randbelow(10**6):06d}"
            b.set_password(c, u, pin); sessions.drop_user_sessions(c, u); audit(c, uid, "pin_reset", u)
            return ctx.send({"ok": True, "pin": pin})
        off = bool(req.get("disabled"))
        if off and u in OWNERS:
            raise ValueError("관리자 계정은 정지할 수 없습니다")
        st = c.execute("SELECT deactivated, anonymized FROM users WHERE id=?", (u,)).fetchone()
        if st["anonymized"] or st["deactivated"]:
            raise ValueError("비활성·익명화된 계정입니다 — 정지·해제 대신 '다시 활성화'(비활성만)")
        c.execute("UPDATE users SET disabled=? WHERE id=?", (off, u))
        if off:
            sessions.drop_user_sessions(c, u)
        audit(c, uid, "disable" if off else "enable", u)
    elif p == "/api/admin/account":   # 비활성화 → (되돌릴 수 없는) 익명화, 다시 활성화. 익명화는 이유 + confirm(아이디 그대로) 필수
        op, u = req.get("op"), str(req.get("uid", ""))
        if op == "deactivate":
            deactivate(c, uid, u, req.get("reason", ""))
        elif op == "reactivate":
            reactivate(c, uid, u)
        elif op == "anonymize":
            if req.get("confirm") != u:
                raise ValueError("되돌릴 수 없습니다 — 확인란에 아이디를 그대로 적어 주세요")
            anonymize(c, uid, u, req.get("reason", ""))
        else:
            raise ValueError("op")
    else:
        return ctx.send({"error": "not found"}, code=404)
    return ctx.send({"ok": True})
