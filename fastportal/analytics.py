"""사용 통계 — 화면 클릭 이벤트 기록, 관리자 집계(화면·도구·일별·이탈·깔때기), CSV 내보내기.

dwell.py 와 같은 원칙: 화면이 보낸 계정 값은 쓰지 않고 로그인 ctx.uid 만 저장, (계정, 방문 UUID, 순번) 기본키로
중복 제거, 180일 보관. 이벤트 이름은 EVENTS 화이트리스트만. 숨김 도구(tools.json hidden·관리자 숨김)는 통계에서 뺀다.
세션은 따로 기록하지 않고 계정별 체류 구간을 시간순으로 이어 30분 넘게 끊기면 새 세션으로 본다(같은 브라우저는
한 탭만 체류를 기록하므로 계정별 흐름이 곧 화면 이동 순서).
"""
import collections, csv, datetime, hashlib, hmac, io, os, re, secrets, statistics, threading, time, urllib.parse

from . import config, db, dwell, timeutil, tools, toolstate

EVENTS = {
    "card_open": "카드 열기", "tool_run": "도구 실행", "post_write": "글쓰기", "like": "좋아요",
    "tut_start": "따라 하기 시작", "tut_step": "따라 하기 단계", "tut_done": "따라 하기 완료", "tut_abort": "따라 하기 중단",
    "login_first": "로그인 다음 첫 화면",
}
TARGET = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,47}")
BATCH_MAX = 50
RATE_N, RATE_S = 300, 60          # 계정당 60초에 이벤트 300개
SESSION_GAP_S = 30 * 60
KEEP_DAYS = 180
_RATE = {}
_LOCK = threading.Lock()
_LAST_PRUNE = None


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS ui_events(uid TEXT, visit TEXT, seq INTEGER, name TEXT, screen TEXT, tool TEXT, target TEXT, step INTEGER,
                                         ts TEXT, day TEXT, PRIMARY KEY(uid, visit, seq));
    CREATE INDEX IF NOT EXISTS ui_events_day ON ui_events(day, name);""")


def _limited(uid, n):
    now = time.monotonic()
    with _LOCK:
        q = _RATE.setdefault(uid, collections.deque())
        while q and q[0] < now - RATE_S:
            q.popleft()
        if len(q) + n > RATE_N:
            return True
        q.extend([now] * n)
        if len(_RATE) > 5000:     # 오래 안 쓴 계정 정리
            for k in [k for k, v in _RATE.items() if not v or v[-1] < now - RATE_S]:
                _RATE.pop(k, None)
    return False


def record(ctx, c, req):
    """POST /api/events {visit, events: [{seq, name, screen, tool?, target?, step?}]} — 묶음 하나는 같은 방문(탭) 것"""
    global _LAST_PRUNE
    visit, items = str(req.get("visit", "")), req.get("events")
    if not dwell.UUID.fullmatch(visit) or not isinstance(items, list) or not 1 <= len(items) <= BATCH_MAX:
        raise ValueError(f"visit(UUID)와 events(1~{BATCH_MAX}개)가 필요합니다")
    rows = []
    for e in items:
        if not isinstance(e, dict):
            raise ValueError("이벤트 형식이 틀립니다")
        seq, name, screen = e.get("seq"), str(e.get("name", "")), str(e.get("screen", ""))
        tool_dir, target, step = str(e.get("tool") or ""), str(e.get("target") or ""), e.get("step")
        if not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq <= 1_000_000:
            raise ValueError("순번 형식이 틀립니다")
        if name not in EVENTS:
            raise ValueError("이벤트 이름이 틀립니다")
        if screen and screen not in dwell.SCREENS:
            raise ValueError("화면 값이 틀립니다")
        if tool_dir and not tools.tool(tool_dir):
            raise ValueError("도구 값이 틀립니다")
        if target and not TARGET.fullmatch(target):
            raise ValueError("대상 값이 틀립니다")
        if step is not None and (not isinstance(step, int) or isinstance(step, bool) or not 0 <= step <= 200):
            raise ValueError("단계 값이 틀립니다")
        rows.append((seq, name, screen, tool_dir, target, step))
    if _limited(ctx.uid, len(rows)):
        return ctx.send({"error": "이벤트가 너무 많습니다", "code": "rate_limited"}, code=429, headers=[("Retry-After", "60")])
    now = timeutil.utcnow()
    ts, day = now.isoformat(timespec="seconds"), now.date().isoformat()
    c.executemany("INSERT OR IGNORE INTO ui_events(uid,visit,seq,name,screen,tool,target,step,ts,day) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  [(ctx.uid, visit.lower(), *r, ts, day) for r in rows])
    with _LOCK:
        if _LAST_PRUNE != day:
            c.execute("DELETE FROM ui_events WHERE day < ?", ((now.date() - datetime.timedelta(days=KEEP_DAYS)).isoformat(),))
            _LAST_PRUNE = day
    return ctx.send({"ok": True})


# ── 집계 ────────────────────────────────────────────────────────────────
def _hidden():
    return {t["dir"] for t in tools.tools() if t.get("hidden")} | set(toolstate.admin_hidden())


def _key(screen, tool_dir):
    return f"tool:{tool_dir}" if screen == "tool" and tool_dir else screen


def _params(ctx):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    try:
        days = int(q.get("days", ["30"])[0])
    except ValueError:
        days = 0
    if days not in (7, 30, 90, 180):
        raise ValueError("기간은 7, 30, 90, 180일 중 하나여야 합니다")
    return days, q


def _load(days):
    """기간 안의 체류 구간·이벤트·좋아요·작업(숨김 도구 뺌). 시각은 UTC 저장 → KST 날짜로"""
    cut = timeutil.kst_day_start_utc(days - 1)
    start = timeutil.today_kst() - datetime.timedelta(days=days - 1)
    hidden = _hidden()
    with db.db() as c:
        seg = [r for r in c.execute("SELECT uid,visit,seq,screen,tool,seconds,ts FROM dwell_segments WHERE day>=? AND ts>=? "
                                    "ORDER BY uid,ts,visit,seq", (cut[:10], cut)) if r["tool"] not in hidden]
        ev = [r for r in c.execute("SELECT uid,visit,seq,name,screen,tool,target,step,ts FROM ui_events WHERE day>=? AND ts>=? "
                                   "ORDER BY ts,uid,seq", (cut[:10], cut)) if r["tool"] not in hidden]
        likes = [r for r in c.execute("SELECT dir tool,user uid,ts FROM events WHERE kind='like' AND ts>=?", (cut,)) if r["tool"] not in hidden]
        opens = [r for r in c.execute("SELECT dir tool,user uid,ts FROM events WHERE kind='open' AND ts>=?", (cut,)) if r["tool"] not in hidden]
        jobs = [r for r in c.execute("SELECT user uid,tool,status,started FROM jobs WHERE started>=?", (timeutil.kst_day_start_epoch(start),))
                if r["tool"] not in hidden]
    return start, seg, ev, likes, opens, jobs


def _kday(ts):
    d = timeutil.kst_date(ts)
    return d.isoformat() if d else ""


def _names():
    names = {k: v for k, v in dwell.SCREENS.items() if k != "tool"}
    hidden = _hidden()
    for t in tools.tools():
        if t["dir"] not in hidden:
            names[f"tool:{t['dir']}"] = t.get("name", t["dir"])
    return names


def summary(days):
    start, seg, ev, likes, opens, jobs = _load(days)
    names = _names()
    today = timeutil.today_kst()
    # 화면·도구별
    per = collections.defaultdict(lambda: {"seconds": 0, "views": collections.Counter(), "users": set(), "clicks": 0, "likes": 0, "opens": 0, "runs": 0})
    for r in seg:
        k = _key(r["screen"], r["tool"])
        p = per[k]; p["seconds"] += r["seconds"]; p["views"][(r["uid"], r["visit"])] += r["seconds"]; p["users"].add(r["uid"])
    for r in ev:
        k = _key(r["screen"], r["tool"]) if r["screen"] else (f"tool:{r['tool']}" if r["tool"] else "")
        if k:
            per[k]["clicks"] += 1
            if r["name"] == "like" and not r["tool"]:
                per[k]["likes"] += 1
            if r["name"] == "tool_run":
                per[k]["runs"] += 1
    for r in likes:
        per[f"tool:{r['tool']}"]["likes"] += 1
    for r in opens:
        per[f"tool:{r['tool']}"]["opens"] += 1
    items = []
    for k, p in per.items():
        v = list(p["views"].values())
        items.append({"key": k, "kind": "tool" if k.startswith("tool:") else "screen", "name": names.get(k, k.split(":", 1)[-1]),
                      "seconds": p["seconds"], "median": round(statistics.median(v)) if v else 0, "views": len(v),
                      "visitors": len(p["users"]), "clicks": p["clicks"], "likes": p["likes"], "opens": p["opens"], "runs": p["runs"]})
    items.sort(key=lambda x: (x["seconds"], x["clicks"]), reverse=True)
    # 일별
    daily = {(start + datetime.timedelta(days=i)).isoformat(): {"seconds": 0, "users": set(), "clicks": 0, "likes": 0}
             for i in range((today - start).days + 1)}
    for r in seg:
        d = daily.get(_kday(r["ts"]))
        if d: d["seconds"] += r["seconds"]; d["users"].add(r["uid"])
    for r in ev:
        d = daily.get(_kday(r["ts"]))
        if d:
            d["clicks"] += 1; d["users"].add(r["uid"])
            if r["name"] == "like" and not r["tool"]: d["likes"] += 1
    for r in likes:
        d = daily.get(_kday(r["ts"]))
        if d: d["likes"] += 1
    daily_out = [{"day": k, "seconds": v["seconds"], "visitors": len(v["users"]), "clicks": v["clicks"], "likes": v["likes"]} for k, v in daily.items()]
    # 이탈: 계정별 구간을 시간순으로 이어 세션 → 화면 이동·마지막 화면
    trans, exits, in_sess, sessions = collections.Counter(), collections.Counter(), collections.Counter(), 0
    by_uid = collections.defaultdict(list)
    for r in seg:
        by_uid[r["uid"]].append(r)
    for rows in by_uid.values():
        path, last_t = [], None
        def close():
            nonlocal sessions
            if path:
                sessions += 1; exits[path[-1]] += 1
                for k in set(path): in_sess[k] += 1
        for r in rows:
            t = timeutil.parse(r["ts"])
            if last_t and t and (t - last_t).total_seconds() > SESSION_GAP_S:
                close(); path = []
            last_t = t or last_t
            k = _key(r["screen"], r["tool"])
            if not path or path[-1] != k:
                if path: trans[(path[-1], k)] += 1
                path.append(k)
        close()
    exit_rows = sorted(({"key": k, "name": names.get(k, k), "sessions": n, "exits": exits[k], "rate": round(exits[k] / n, 3)}
                        for k, n in in_sess.items()), key=lambda x: (x["exits"], x["sessions"]), reverse=True)
    trans_rows = [{"from": a, "from_name": names.get(a, a), "to": b, "to_name": names.get(b, b), "n": n} for (a, b), n in trans.most_common(30)]
    # 깔때기 — 따라 하기(대상=따라 하기 id): 시작 → 단계별 도달 → 완료, 중단
    tut = collections.defaultdict(lambda: {"start": set(), "steps": collections.defaultdict(set), "done": set(), "abort": set()})
    for r in ev:
        if not r["name"].startswith("tut_") or not r["target"]:
            continue
        t, u = tut[r["target"]], r["uid"]
        if r["name"] == "tut_start": t["start"].add(u)
        elif r["name"] == "tut_step" and r["step"] is not None: t["steps"][r["step"]].add(u)
        elif r["name"] == "tut_done": t["done"].add(u)
        elif r["name"] == "tut_abort": t["abort"].add(u)
    tut_rows = []
    for tid, t in tut.items():
        n = max(t["steps"], default=-1) + 1
        tut_rows.append({"id": tid, "start": len(t["start"]), "steps": [len(t["steps"].get(i, ())) for i in range(n)],
                         "done": len(t["done"]), "abort": len(t["abort"])})
    tut_rows.sort(key=lambda x: x["start"], reverse=True)
    # 깔때기 — 로그인(활동) → 도구 열기 → 도구 실행 → 결과(끝난 작업)
    active = {r["uid"] for r in seg} | {r["uid"] for r in ev}
    opened = active & ({r["uid"] for r in opens} | {r["uid"] for r in seg if r["screen"] == "tool"} | {r["uid"] for r in ev if r["name"] == "card_open"})
    ran = opened & ({r["uid"] for r in ev if r["name"] == "tool_run"} | {r["uid"] for r in jobs})
    done = ran & {r["uid"] for r in jobs if r["status"] == "done"}
    funnel = [{"step": "로그인 후 활동", "users": len(active)}, {"step": "도구 열기", "users": len(opened)},
              {"step": "도구 실행", "users": len(ran)}, {"step": "결과 받음", "users": len(done)}]
    first = collections.Counter(_key(r["screen"], r["tool"]) for r in ev if r["name"] == "login_first")
    return {"days": days, "from": start.isoformat(), "to": today.isoformat(), "items": items, "daily": daily_out,
            "totals": {"seconds": sum(r["seconds"] for r in seg), "visitors": len(active), "clicks": len(ev),
                       "likes": sum(d["likes"] for d in daily_out), "sessions": sessions},
            "exits": exit_rows, "transitions": trans_rows, "tutorials": tut_rows, "funnel": funnel,
            "first_screens": [{"key": k, "name": names.get(k, k), "n": n} for k, n in first.most_common(10)],
            "events": EVENTS}


def report(ctx):
    """GET /api/admin/analytics?days=7|30|90|180"""
    if not ctx.is_owner:
        return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
    try:
        days, _ = _params(ctx)
    except ValueError as e:
        return ctx.send({"error": str(e)}, code=400)
    return ctx.send(summary(days))


# ── CSV ────────────────────────────────────────────────────────────────
def _secret():
    """가명 id 비밀값 — 처음 한 번 만들고 그대로(내보낼 때마다 같은 가명). 권한 600"""
    path = config.secret_file(".analytics_hmac")
    if not os.path.exists(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    with open(path, encoding="ascii") as f:
        return f.read().strip().encode()


def pseudo(uid, key=None):
    return "u_" + hmac.new(key or _secret(), str(uid).encode(), hashlib.sha256).hexdigest()[:16]


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s   # 엑셀 수식 주입 막기


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def export(ctx):
    """GET /api/admin/analytics.csv?kind=daily|raw&days= — daily: 일별×화면/도구, raw: 이벤트 원본(가명 id, 실명·아이디·IP 없음)"""
    if not ctx.is_owner:
        return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
    try:
        days, q = _params(ctx)
    except ValueError as e:
        return ctx.send({"error": str(e)}, code=400)
    kind = q.get("kind", ["daily"])[0]
    if kind not in ("daily", "raw"):
        return ctx.send({"error": "kind 는 daily 또는 raw"}, code=400)
    start, seg, ev, likes, opens, jobs = _load(days)
    names = _names()
    if kind == "daily":
        agg = collections.defaultdict(lambda: {"seconds": 0, "views": set(), "users": set(), "clicks": 0, "likes": 0, "opens": 0})
        for r in seg:
            a = agg[(_kday(r["ts"]), _key(r["screen"], r["tool"]))]
            a["seconds"] += r["seconds"]; a["views"].add((r["uid"], r["visit"])); a["users"].add(r["uid"])
        for r in ev:
            k = _key(r["screen"], r["tool"]) if r["screen"] else (f"tool:{r['tool']}" if r["tool"] else "")
            if k:
                a = agg[(_kday(r["ts"]), k)]; a["clicks"] += 1
                if r["name"] == "like" and not r["tool"]: a["likes"] += 1
        for r in likes:
            agg[(_kday(r["ts"]), f"tool:{r['tool']}")]["likes"] += 1
        for r in opens:
            agg[(_kday(r["ts"]), f"tool:{r['tool']}")]["opens"] += 1
        rows = [(d, "tool" if k.startswith("tool:") else "screen", k.split(":", 1)[-1], names.get(k, k), a["seconds"], len(a["views"]),
                 len(a["users"]), a["clicks"], a["likes"], a["opens"]) for (d, k), a in sorted(agg.items())]
        body = _csv(["day_kst", "kind", "key", "name", "active_seconds", "views", "visitors", "clicks", "likes", "tool_opens"], rows)
    else:
        key = _secret()
        rows = [(timeutil.fmt_kst(r["ts"], "%Y-%m-%d %H:%M:%S"), pseudo(r["uid"], key), "dwell", r["screen"], r["tool"], "", "", r["seconds"]) for r in seg]
        rows += [(timeutil.fmt_kst(r["ts"], "%Y-%m-%d %H:%M:%S"), pseudo(r["uid"], key), r["name"], r["screen"], r["tool"], r["target"],
                  "" if r["step"] is None else r["step"], "") for r in ev]
        rows += [(timeutil.fmt_kst(r["ts"], "%Y-%m-%d %H:%M:%S"), pseudo(r["uid"], key), "tool_" + k, "tool", r["tool"], "", "", "")
                 for k, src in (("like", likes), ("open", opens)) for r in src]
        rows.sort(key=lambda r: (r[0], r[1]))
        body = _csv(["ts_kst", "user", "event", "screen", "tool", "target", "step", "seconds"], rows)
    fname = f"analytics-{kind}-{days}d-{timeutil.today_kst().strftime('%Y%m%d')}.csv"
    return ctx.send(body, ctype="text/csv; charset=utf-8", headers=[("Content-Disposition", f'attachment; filename="{fname}"'),
                                                                   ("Cache-Control", "no-store")])
