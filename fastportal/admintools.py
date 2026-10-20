"""관리 화면 '에이전트 관리' — 도구 켜기·끄기(toolstate)·공유 빌더 에이전트 끄기·켜기(빌더 block). 관리자만.

GET  /api/admin/tools   → {tools: [{dir,name,title,group,port,remote,installed,up,hidden,enabled,by,ts,reason,usage_30d,users_30d,active_jobs}]}
POST /api/admin/tool    {dir, enabled, reason?, stop?, confirm?} — 끄면서 stop=true 면 프로세스도 멈춤(진행 중 작업이 있으면 confirm=true 일 때만, 아니면 409 jobs_running)
GET  /api/admin/agents  → {agents: [...], builder: true|false}   — 빌더 GET /api/agents?tab=admin(관리자 서명)
POST /api/admin/agent   {id, enabled, reason?} — 빌더 POST /api/agents/<id>/block {off, reason}
감사: tool_disable·tool_enable(toolstate) · tool_stop · agent_disable·agent_enable(내용 = 이유)"""
import datetime, json, os, time, urllib.error, urllib.request

from . import accounts, community, config, flows, jobs, timeutil, tools, toolstate
from .db import db


def _forbidden(ctx):
    return ctx.send({"error": "관리자만 할 수 있습니다", "code": "forbidden"}, code=403)


def active_jobs(c, d):
    """그 도구에서 진행 중인 일: 작업 알림(queued·running, 6시간 안에 갱신) + 그 도구 어댑터를 쓰는 실행 중 흐름"""
    n = c.execute("SELECT COUNT(*) FROM jobs WHERE tool=? AND status IN ('queued','running') AND COALESCE(updated, started, 0) > ?",
                  (d, time.time() - 6 * 3600)).fetchone()[0]
    aids = [k for k, a in flows.ADAPTERS.items() if a["tool"] == d]
    if aids:
        for (steps,) in c.execute("SELECT steps FROM flow_runs WHERE status='running'"):
            if any(f'"{k}"' in (steps or "") for k in aids):
                n += 1
    return n


def tools_get(ctx, up):
    if not ctx.is_owner:
        return _forbidden(ctx)
    cut = timeutil.kst_day_start_utc(30)   # 최근 30일(KST 달력) — 저장값은 UTC
    with db() as c:
        st = toolstate.rows(c)
        use = {r[0]: (r[1] or 0, r[2] or 0) for r in c.execute(
            "SELECT dir, SUM(kind='open' AND ts>=?), COUNT(DISTINCT CASE WHEN kind='open' AND ts>=? THEN user END) FROM events GROUP BY dir", (cut, cut))}
        out = []
        for t in tools.tools():
            s = st.get(t["dir"]) or {}
            out.append({"dir": t["dir"], "name": t.get("name") or t["dir"], "title": t.get("title") or "", "group": t.get("group") or "기타",
                        "port": t.get("port"), "remote": tools.is_remote(t), "hidden": bool(t.get("hidden")),
                        "installed": tools.is_remote(t) or os.path.isdir(os.path.join(config.BASE, t["dir"])),
                        "up": bool(up.get(tools.tool_addr(t))), "enabled": bool(s.get("enabled", 1)),
                        "by": s.get("by"), "ts": s.get("ts"), "reason": s.get("reason") or "",
                        "usage_30d": use.get(t["dir"], (0, 0))[0], "users_30d": use.get(t["dir"], (0, 0))[1], "active_jobs": active_jobs(c, t["dir"])})
    return ctx.send({"tools": out})


def tool_post(ctx, req):
    if not ctx.is_owner:
        return _forbidden(ctx)
    d, en = req.get("dir"), req.get("enabled")
    t = tools.tool(d) if isinstance(d, str) else None
    if not t:
        return ctx.send({"error": "없는 도구입니다", "code": "not_found"}, code=404)
    if not isinstance(en, bool):
        return ctx.send({"error": "enabled 는 참/거짓", "code": "bad_request"}, code=400)
    reason = req.get("reason") or ""
    if not isinstance(reason, str):
        return ctx.send({"error": "reason 은 글", "code": "bad_request"}, code=400)
    stop = bool(req.get("stop")) and not en
    stopped = False
    with db() as c:
        if stop and not tools.is_remote(t):
            n = active_jobs(c, t["dir"])
            if n and req.get("confirm") is not True:
                return ctx.send({"error": f"진행 중인 작업이 {n}건 있습니다 — 그래도 멈추려면 확인하세요", "code": "jobs_running", "active_jobs": n}, code=409)
        changed = toolstate.set_state(c, ctx.uid, t["dir"], en, reason)
        if stop and not tools.is_remote(t):
            tools.stop(t); tools.alive_reset()
            accounts.audit(c, ctx.uid, "tool_stop", t["dir"], "끄면서 멈춤")
            stopped = True
    toolstate.reset()   # 커밋한 뒤 — 다음 요청이 바로 새 상태를 봄
    toolstate.write_file()
    tools.stats_reset()
    return ctx.send({"ok": True, "dir": t["dir"], "enabled": en, "changed": changed, "stopped": stopped})


# ── 공유 빌더 에이전트 ────────────────────────────────────────────────────
def _builder_post(path, body):
    t = tools.tool(community.BUILDER)
    if not t or not tools.tool_host(t):
        return 502, None
    hd = {**jobs.tool_headers("owner"), "Accept": "application/json", "Content-Type": "application/json"}
    rq = urllib.request.Request(f"http://{tools.tool_host(t)}:{int(t['port'])}{path}", data=json.dumps(body).encode(), headers=hd, method="POST")
    try:
        with jobs.TOOL_OPENER.open(rq, timeout=10) as r:
            return r.status, json.loads(r.read(4 * 1024 * 1024) or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read(65536) or b"{}")
        except ValueError:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 502, None


AGENT_KEYS = ("id", "name", "desc", "icon", "status", "scope", "scope_label", "review", "docs", "uses_7d", "owner_name", "updated", "created", "forks", "blocked")


def agents_get(ctx):
    if not ctx.is_owner:
        return _forbidden(ctx)
    st, b = community._builder("owner", "/api/agents?tab=admin")
    if st != 200 or not isinstance(b, dict):
        return ctx.send({"agents": [], "builder": False, "error": "에이전트 빌더가 꺼져 있거나 응답하지 않습니다" if st == 502 else f"빌더 응답 {st}"})
    return ctx.send({"agents": [{k: a.get(k) for k in AGENT_KEYS} for a in b.get("agents") or [] if isinstance(a, dict)], "builder": True})


def agent_post(ctx, req):
    if not ctx.is_owner:
        return _forbidden(ctx)
    aid, en, reason = req.get("id"), req.get("enabled"), req.get("reason") or ""
    if not isinstance(aid, str) or not community.AGENT_RE.fullmatch(aid) or not isinstance(en, bool) or not isinstance(reason, str):
        return ctx.send({"error": "id(에이전트)·enabled(참/거짓)·reason(글)", "code": "bad_request"}, code=400)
    st, a = _builder_post(f"/api/agents/{aid}/block", {"off": not en, "reason": reason[:200]})
    if st == 502:
        return ctx.send({"error": "에이전트 빌더가 꺼져 있습니다", "code": "unavailable"}, code=503)
    if st != 200:
        return ctx.send({"error": (a or {}).get("error") or f"빌더 응답 {st}", "code": "not_found" if st == 404 else "bad_request"}, code=404 if st == 404 else 400)
    with db() as c:
        accounts.audit(c, ctx.uid, "agent_enable" if en else "agent_disable", aid, (str(a.get("name") or "") + (" · " + reason[:200] if reason else ""))[:300])
    community._OWNERS.pop(aid, None)
    return ctx.send({"ok": True, "id": aid, "enabled": en, "blocked": a.get("blocked")})
