"""활성 화면 체류 구간 기록 및 관리자 집계.

화면에서 보내는 계정 식별자는 사용하지 않고 로그인 ctx.uid 만 저장한다. 중복 전송은
(계정, 방문 UUID, 순번) 기본키로 제거한다. 각 기록은 짧은 초 단위 구간이며 최근 180일만 보관한다.
"""
import datetime, re, threading, urllib.parse

from . import accounts, db, timeutil, tools

SCREENS = {
    "home": "홈", "all": "전체 에이전트", "fav": "즐겨찾기", "recent": "최근 사용",
    "work": "내 작업", "account": "계정 설정", "approvals": "승인", "admin": "관리", "tool": "도구",
}
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE = None


def record(ctx, c, req):
    global _LAST_PRUNE
    visit = str(req.get("visit", ""))
    seq, seconds = req.get("seq"), req.get("seconds")
    screen, tool_dir = str(req.get("screen", "")), str(req.get("tool", ""))
    if not UUID.fullmatch(visit) or not isinstance(seq, int) or isinstance(seq, bool) or not 0 <= seq <= 100000:
        raise ValueError("방문 id 또는 순번 형식이 틀립니다")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= 30:
        raise ValueError("체류 구간은 1~30초여야 합니다")
    if screen not in SCREENS:
        raise ValueError("화면 값이 틀립니다")
    if screen == "tool":
        if not tools.tool(tool_dir):
            raise ValueError("도구 값이 틀립니다")
    elif tool_dir:
        raise ValueError("도구 화면만 도구 값이 필요합니다")
    now = timeutil.utcnow()   # 저장은 UTC(ts·day 모두 — 예전 행과 같은 기준). 집계는 report() 에서 KST 로
    ts, day = now.isoformat(timespec="seconds"), now.date().isoformat()
    c.execute("INSERT OR IGNORE INTO dwell_segments(uid,visit,seq,screen,tool,seconds,ts,day) VALUES(?,?,?,?,?,?,?,?)",
              (ctx.uid, visit.lower(), seq, screen, tool_dir, seconds, ts, day))
    # 요청마다 실행하지 않도록 프로세스별 하루 한 번 오래된 행을 정리한다.
    with _PRUNE_LOCK:
        if _LAST_PRUNE != day:
            cutoff = (now.date() - datetime.timedelta(days=180)).isoformat()
            c.execute("DELETE FROM dwell_segments WHERE day < ?", (cutoff,))
            _LAST_PRUNE = day
    return ctx.send({"ok": True})


def report(ctx):
    if not ctx.is_owner:
        return ctx.send({"error": "관리자만 볼 수 있습니다"}, code=403)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    try:
        days = int(q.get("days", ["30"])[0])
    except ValueError:
        return ctx.send({"error": "기간은 7, 30, 90일 중 하나여야 합니다"}, code=400)
    if days not in (7, 30, 90):
        return ctx.send({"error": "기간은 7, 30, 90일 중 하나여야 합니다"}, code=400)
    uid, screen, tool_dir = (q.get(k, [""])[0][:100] for k in ("uid", "screen", "tool"))
    if screen and screen not in SCREENS:
        return ctx.send({"error": "화면 값이 틀립니다"}, code=400)
    if tool_dir and not tools.tool(tool_dir):
        return ctx.send({"error": "도구 값이 틀립니다"}, code=400)
    today = timeutil.today_kst()   # 기간·날짜별 집계는 KST 달력(저장된 ts 는 UTC)
    start = today - datetime.timedelta(days=days - 1)
    cut = timeutil.kst_day_start_utc(days - 1)            # KST start 0시 → UTC ISO(저장 형식)
    cut_day = cut[:10]                                     # day(UTC 날짜) 색인으로 먼저 좁힘
    uid_clause, uid_args = "", []
    if uid:
        with db.db() as c:
            row = c.execute("SELECT id FROM users WHERE id=? OR anon_id=?", (uid, uid)).fetchone()
        raw_uid = row[0] if row else uid
        uid_clause, uid_args = " AND uid=?", [raw_uid]
    screen_clause, screen_args = (" AND screen=?", [screen]) if screen else ("", [])
    tool_clause, tool_args = (" AND tool=?", [tool_dir]) if tool_dir else ("", [])
    where = "day>=? AND ts>=?" + uid_clause + screen_clause + tool_clause
    args = [cut_day, cut, *uid_args, *screen_args, *tool_args]
    tool_where = "day>=? AND ts>=?" + uid_clause + tool_clause
    tool_args_full = [cut_day, cut, *uid_args, *tool_args]
    with db.db() as c:
        raw_rows = [dict(r) for r in c.execute(
            "SELECT uid,screen,tool,SUM(seconds) seconds,COUNT(DISTINCT visit) visits,MAX(ts) last "
            "FROM dwell_segments WHERE " + where + " GROUP BY uid,screen,tool ORDER BY seconds DESC", args)]
        daily = [dict(r) for r in c.execute(
            "SELECT date(ts,'+9 hours') day,SUM(seconds) seconds FROM dwell_segments WHERE " + where + " GROUP BY 1 ORDER BY 1", args)]
        # 기존 지표와 혼동되지 않도록 도구 열기 버튼 기록(events)과 끝난 작업 실행 시간(jobs)을 별도로 집계한다.
        base = "ts>=?" + (" AND user=?" if uid_clause else "") + (" AND dir=?" if tool_clause else "")
        base_args = [cut, *uid_args, *tool_args]
        opened = c.execute("SELECT COUNT(*) FROM events WHERE kind='open' AND " + base, base_args).fetchone()[0]
        runtime = c.execute("SELECT COUNT(*),COALESCE(SUM(MAX(0,finished-started)),0) FROM jobs "
                            "WHERE finished IS NOT NULL AND started>=?" +
                            (" AND user=?" if uid_clause else "") + (" AND tool=?" if tool_clause else ""),
                            [timeutil.kst_day_start_epoch(start), *uid_args, *tool_args]).fetchone()
        tool_active = c.execute("SELECT uid,tool,SUM(seconds) seconds FROM dwell_segments WHERE " + tool_where +
                                " AND screen='tool' GROUP BY uid,tool", tool_args_full).fetchall()
        tool_opens = c.execute("SELECT user uid,dir tool,COUNT(*) n FROM events WHERE kind='open' AND " + base +
                               " GROUP BY user,dir", base_args).fetchall()
        tool_jobs = c.execute("SELECT user uid,tool,COUNT(*) n,COALESCE(SUM(MAX(0,finished-started)),0) seconds FROM jobs "
                              "WHERE finished IS NOT NULL AND started>=?" +
                              (" AND user=?" if uid_clause else "") + (" AND tool=?" if tool_clause else "") +
                              " GROUP BY user,tool", [timeutil.kst_day_start_epoch(start), *uid_args, *tool_args]).fetchall()
        display = accounts.display_ids(c)
        names = {r["id"]: (r["name"], r["dept"] or "") for r in c.execute("SELECT id,name,dept FROM users")}
    tmap = {t["dir"]: t.get("name", t["dir"]) for t in tools.tools()}
    per_tool = {}
    for r in tool_active:
        per_tool[(r["uid"], r["tool"])] = {"uid": r["uid"], "tool": r["tool"], "active_seconds": r["seconds"], "opens": 0, "jobs": 0, "job_seconds": 0}
    for r in tool_opens:
        per_tool.setdefault((r["uid"], r["tool"]), {"uid": r["uid"], "tool": r["tool"], "active_seconds": 0, "opens": 0, "jobs": 0, "job_seconds": 0})["opens"] = r["n"]
    for r in tool_jobs:
        row = per_tool.setdefault((r["uid"], r["tool"]), {"uid": r["uid"], "tool": r["tool"], "active_seconds": 0, "opens": 0, "jobs": 0, "job_seconds": 0})
        row["jobs"], row["job_seconds"] = r["n"], round(r["seconds"])
    rows = []
    for r in raw_rows:
        real = r["uid"]
        name, dept = names.get(real, ("관리자" if real == "owner" else "계정", ""))
        rows.append({"uid": display.get(real, real), "name": name, "dept": dept, "screen": r["screen"],
                     "screen_name": SCREENS[r["screen"]], "tool": r["tool"], "tool_name": tmap.get(r["tool"], ""),
                     "seconds": r["seconds"], "visits": r["visits"], "last": r["last"]})
    tool_rows = []
    for r in per_tool.values():
        name, dept = names.get(r["uid"], ("관리자" if r["uid"] == "owner" else "계정", ""))
        tool_rows.append({"uid": display.get(r["uid"], r["uid"]), "name": name, "dept": dept,
                          "tool": r["tool"], "tool_name": tmap.get(r["tool"], ""), "active_seconds": r["active_seconds"],
                          "opens": r["opens"], "jobs": r["jobs"], "job_seconds": r["job_seconds"]})
    tool_rows.sort(key=lambda r: (r["active_seconds"], r["opens"], r["job_seconds"]), reverse=True)
    active = sum(r["seconds"] for r in raw_rows)
    # 정확한 방문 수는 선택 조건 전체에서 DISTINCT 로 계산한다.
    with db.db() as c:
        visits = c.execute("SELECT COUNT(DISTINCT uid||':'||visit) FROM dwell_segments WHERE " + where, args).fetchone()[0]
    return ctx.send({"days": days, "from": start.isoformat(), "to": today.isoformat(),
                     "active_seconds": active, "visits": visits, "rows": rows, "tool_rows": tool_rows, "daily": daily,
                     "tool_opens": opened, "finished_jobs": runtime[0], "job_runtime_seconds": round(runtime[1]),
                     "screens": [{"id": k, "name": v} for k, v in SCREENS.items()],
                     "tools": [{"dir": t["dir"], "name": t.get("name", t["dir"])} for t in tools.tools()]})
