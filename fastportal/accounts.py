"""부서·부서장·요청/승인·관리·감사·비활성화/익명화·탈퇴 요청·부서 보관 + 계정 설정·승인·관리 API(/api/account·/api/approvals·/api/admin).
포털 쪽 사용자 정보(users 표의 이름·부서·테마·비활성 등)는 계정 저장소가 무엇이든 여기서 다룬다 — 사용자 키는 아이디."""
import datetime, json, re, secrets, time

from . import auth, config, sessions
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
KIND = {"dept_join": "부서 소속", "dept_head": "부서장 권한", "withdraw": "탈퇴 요청"}


def is_admin(uid):
    return uid == "owner" or uid in OWNERS


def audit(c, actor, action, target, detail=""):
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))


def heads_of(c, dept):
    return [r[0] for r in c.execute("SELECT uid FROM dept_heads WHERE dept=?", (dept,))]


def my_heads(c, uid):
    return [r[0] for r in c.execute("SELECT dept FROM dept_heads WHERE uid=? ORDER BY dept", (uid,))]


def role(c, uid):
    return "관리자" if is_admin(uid) else "부서장" if my_heads(c, uid) else "일반"


def visible_depts(c):
    return [r[0] for r in c.execute("SELECT name FROM depts WHERE hidden=0 ORDER BY name")]


def names(c):
    return {r[0]: r[1] for r in c.execute("SELECT id, name FROM users")}


def req_rows(c, where, args=()):
    nm = names(c) | {"owner": "관리자(로컬)"}
    dis = display_ids(c)   # 익명화된 계정의 아이디는 deleted-… 로만
    return [dict(r) | {"uid": dis.get(r["uid"], r["uid"]), "approver": dis.get(r["approver"], r["approver"]), "uname": nm.get(r["uid"], r["uid"]), "approver_name": nm.get(r["approver"] or "", r["approver"] or ""), "label": KIND.get(r["kind"], r["kind"]),
                       "route": ("관리자" if r["kind"] != "dept_join" or not heads_of(c, r["dept"]) else "부서장 " + ", ".join(nm.get(h, h) for h in heads_of(c, r["dept"])))}
            for r in c.execute(f"SELECT * FROM requests WHERE {where} ORDER BY id DESC LIMIT 200", args)]


def new_request(c, uid, kind, dept):
    if dept not in visible_depts(c):
        raise ValueError("목록에 있는 부서를 고르세요 (목록에 없으면 관리자에게 요청)")
    if c.execute("SELECT 1 FROM requests WHERE uid=? AND kind=? AND status='pending'", (uid, kind)).fetchone():
        raise ValueError("이미 대기 중인 요청이 있습니다 — 취소한 뒤 다시 요청하세요")
    c.execute("INSERT INTO requests(kind,uid,dept,status,ts) VALUES(?,?,?,?,?)", (kind, uid, dept, "pending", now()))


def can_decide(c, actor, r):
    if is_admin(actor):
        return True
    return r["kind"] == "dept_join" and r["uid"] != actor and actor in heads_of(c, r["dept"])


def decide(c, actor, rid, ok, note=""):
    r = c.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not r or r["status"] != "pending":
        raise ValueError("이미 처리됐거나 없는 요청입니다")
    if not can_decide(c, actor, r):
        raise PermissionError("이 요청을 처리할 권한이 없습니다")
    c.execute("UPDATE requests SET status=?, approver=?, note=?, decided_ts=?, notified=0 WHERE id=?", ("approved" if ok else "rejected", actor, note[:200], now(), rid))
    if ok and r["kind"] == "dept_join":
        c.execute("UPDATE users SET dept=? WHERE id=?", (r["dept"], r["uid"]))
        for d in my_heads(c, r["uid"]):
            if d != r["dept"]:
                c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, r["uid"])); audit(c, actor, "head_revoke", r["uid"], f"{d} (부서 이동)")
    if ok and r["kind"] == "dept_head":
        if dept_archived(c, r["dept"]):
            raise ValueError("보관된 부서입니다 — 되살린 뒤 처리하세요")
        c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (r["dept"], r["uid"], now()))
    audit(c, actor, ("approve" if ok else "reject") + ":" + r["kind"], r["uid"], f"{r['dept']} #{rid} {note[:200]}".strip())
    if ok and r["kind"] == "withdraw":   # 탈퇴 승인 = 비활성화(바로 지우지 않음). 익명화는 관리자가 따로 또는 기한 뒤 정리 작업
        deactivate(c, actor, r["uid"], f"본인 탈퇴 요청 #{rid}")


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
    c.execute("UPDATE users SET name=?, dept='', salt=?, hash=?, iters=?, theme=NULL, seen=NULL, disabled=1, deact_reason=NULL, anonymized=?, anon_id=? WHERE id=?",
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
    cut = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
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
    pend = c.execute("SELECT dept FROM requests WHERE uid=? AND kind='dept_join' AND status='pending'", (uid,)).fetchone()
    n = sum(can_decide(c, uid, r) for r in c.execute("SELECT * FROM requests WHERE status='pending'"))
    return {"id": uid, "name": uname, "owner": is_owner, "agent_user": "owner" if is_owner else uid, "role": role(c, uid),
            "dept": u["dept"] if u else "", "dept_pending": pend["dept"] if pend else "", "heads": my_heads(c, uid), "approvals": n,
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
            return ctx.send({"me": me(c, ctx.uid, ctx.uname), "depts": visible_depts(c), "requests": req_rows(c, "uid=?", (ctx.uid,))[:20]})
        if p == "/api/account/theme":
            return ctx.send({"theme": ctx.theme(), "themes": sorted(config.THEMES)})
        if p == "/api/account/notices":   # 예전 화면 호환: GET 도 읽음 처리. 새 화면은 POST(같은 응답) 또는 GET ?peek=1(읽음 처리 안 함)
            return notices(ctx, c, mark="peek=1" not in ctx.raw.partition("?")[2].split("&"))
        if p == "/api/approvals":
            heads = my_heads(c, ctx.uid)
            if not (ctx.is_owner or heads):
                return ctx.send({"error": "부서장·관리자만 볼 수 있습니다"}, code=403)
            pend = [r for r in req_rows(c, "status='pending'") if can_decide(c, ctx.uid, r)]
            cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
            members = {d: [dict(r) for r in c.execute(
                "SELECT u.id, u.name, u.seen, (SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open') total, "
                "(SELECT COUNT(*) FROM events e WHERE e.user=u.id AND e.kind='open' AND e.ts>=?) recent, "
                "EXISTS(SELECT 1 FROM dept_heads h WHERE h.dept=u.dept AND h.uid=u.id) head FROM users u WHERE u.dept=? AND NOT u.disabled ORDER BY u.name", (cut, d))]
                for d in heads}
            done = [r for r in req_rows(c, "status IN ('approved','rejected') AND approver=?", (ctx.uid,))][:20]
            return ctx.send({"pending": pend, "members": members, "done": done})
        if p == "/api/admin":
            if not ctx.is_owner:
                return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
            heads = {}
            for r in c.execute("SELECT dept, uid FROM dept_heads"):
                heads.setdefault(r["uid"], []).append(r["dept"])
            dis = display_ids(c)   # 익명화된 계정은 아이디도 deleted-… 로만 내보냄(감사 기록의 누가·대상 포함)
            devs = {r[0] for r in c.execute("SELECT uid FROM developers")} if c.execute("SELECT 1 FROM sqlite_master WHERE name='developers'").fetchone() else set()
            users = [dict(r) | {"id": dis.get(r["id"], r["id"]), "developer": r["id"] in devs, "role": "관리자" if r["id"] in OWNERS else "부서장" if r["id"] in heads else "일반", "heads": heads.get(r["id"], [])}
                     for r in c.execute("SELECT id, name, dept, ts, seen, disabled, deactivated, deact_reason, anonymized FROM users ORDER BY ts")]
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
        elif p == "/api/account/dept":
            d = str(req.get("dept", ""))
            if d == (c.execute("SELECT dept FROM users WHERE id=?", (uid,)).fetchone()[0] or ""):
                raise ValueError("이미 그 부서입니다")
            new_request(c, uid, "dept_join", d)
        elif p == "/api/account/head":
            d = c.execute("SELECT dept FROM users WHERE id=?", (uid,)).fetchone()[0]
            if not d:
                raise ValueError("부서 소속이 승인된 뒤에 요청할 수 있습니다")
            if uid in heads_of(c, d):
                raise ValueError("이미 부서장입니다")
            new_request(c, uid, "dept_head", d)
        elif p == "/api/account/onboarded":   # done=true: 둘러보기 마침·건너뜀·'새 기능 둘러보기' 안내 닫음(이 판은 본 것으로) / false: 다음 로그인 때 다시 자동으로
            if req.get("done", True):
                c.execute("UPDATE users SET onboarded=1, tour_seen=MAX(COALESCE(tour_seen, 0), ?) WHERE id=?", (config.TOUR_VERSION, uid))
            else:
                c.execute("UPDATE users SET onboarded=0 WHERE id=?", (uid,))
        elif p == "/api/account/tutorial":   # 따라 하기 본 것 기록 {id, version} / 지우기 {reset: true}(계정 설정 '사용법 진행 기록 초기화')
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
    elif p == "/api/admin/head":
        d, u = str(req.get("dept", "")), str(req.get("uid", ""))
        if req.get("op") == "grant":
            if dept_archived(c, d):
                raise ValueError("보관된 부서입니다")
            if not c.execute("SELECT 1 FROM users WHERE id=? AND dept=?", (u, d)).fetchone():
                raise ValueError("그 부서 소속(승인된) 계정만 부서장이 될 수 있습니다")
            c.execute("INSERT OR IGNORE INTO dept_heads VALUES(?,?,?)", (d, u, now()))
        elif req.get("op") == "revoke":
            c.execute("DELETE FROM dept_heads WHERE dept=? AND uid=?", (d, u))
        else:
            raise ValueError("op")
        audit(c, uid, "head_" + req["op"], u, d)
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
