"""계정별 작업 보관함. 로그인 uid로 DB를 분리하고 기존 도구의 이력을 서명된 읽기 요청으로 가져온다.
큰 파일은 도구 저장소에 그대로 두며, 작업 내용의 JSON 사본은 portal.db에 저장한다.
"""
import concurrent.futures, hashlib, json, re, secrets, urllib.error, urllib.parse, urllib.request

from . import config, jobs, tools, toolstate
from .db import db, now

MAX_PAYLOAD = 2 * 1024 * 1024
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
# 목록 경로, 상세 경로. 사용자 헤더·서명은 포털만 만든다.
SOURCES = {
    **{d + "-local": ("/api/runs", "/api/runs/{id}") for d in ("humanize-kr", "kordoc", "writer", "meeting", "diagram", "sketch", "sql")},
    **{d + "-local": ("/api/history", "/api/history/{id}") for d in ("mail", "review", "chart", "shield")},
    "tts-local": ("/api/runs", None), "image-local": ("/api/runs", None), "avatar-local": ("/api/runs", None),
    "award-local": ("/api/history", None), "digitizer-local": ("/api/history", "/api/project/{id}"),
    "protein-local": ("/api/jobs", "/api/job/{id}"), "battery-local": ("/api/jobs", "/api/jobs/{id}"),
    "agent-cad-local": ("/api/projects", "/api/projects/{id}"), "coach-local": ("/api/docs", "/api/docs/{id}"),
    "notebook-local": ("/api/notebooks", "/api/nb/{id}"),
    "agent-builder-local": ("/api/agents?tab=mine", "/api/agents/{id}"),
}


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS work_items(
      id TEXT PRIMARY KEY, uid TEXT NOT NULL, tool TEXT NOT NULL, source TEXT NOT NULL,
      source_id TEXT, source_user TEXT, title TEXT NOT NULL, payload TEXT NOT NULL,
      link TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
      starred INTEGER NOT NULL DEFAULT 0, archived TEXT, revision INTEGER NOT NULL DEFAULT 1,
      detailed INTEGER NOT NULL DEFAULT 0,
      UNIQUE(uid,tool,source,source_id));
    CREATE INDEX IF NOT EXISTS work_items_user ON work_items(uid,archived,updated);
    CREATE TABLE IF NOT EXISTS work_versions(
      item TEXT NOT NULL, uid TEXT NOT NULL, revision INTEGER NOT NULL,
      payload TEXT NOT NULL, ts TEXT NOT NULL, PRIMARY KEY(item,revision));
    CREATE TABLE IF NOT EXISTS work_sync(uid TEXT, tool TEXT, checked TEXT, ok INTEGER,
      count INTEGER, PRIMARY KEY(uid,tool));
    """)
    if "detailed" not in {r[1] for r in c.execute("PRAGMA table_info(work_items)")}:
        c.execute("ALTER TABLE work_items ADD COLUMN detailed INTEGER NOT NULL DEFAULT 0")


def _tool(d):
    t = tools.tool(d)
    if not t or (t.get("hidden") and not config.SHOW_HIDDEN):
        raise ValueError("목록에 없는 도구입니다")
    if toolstate.is_off(t["dir"]):
        raise ValueError(toolstate.MSG)
    return t


def _json(value):
    if not isinstance(value, dict):
        raise ValueError("작업 내용은 JSON 객체여야 합니다")
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > MAX_PAYLOAD:
        raise ValueError("작업 내용은 2MB 이하로 저장하세요. 큰 파일은 도구에 보관합니다")
    return raw


def _read(t, path, user):
    host = tools.tool_host(t)
    if not host:
        raise ValueError("도구 주소 설정을 확인하세요")
    host = f"[{host}]" if ":" in host else host
    req = urllib.request.Request(f"http://{host}:{int(t['port'])}{path}", headers={**jobs.tool_headers(user), "Accept": "application/json"})
    with jobs.TOOL_OPENER.open(req, timeout=5) as r:
        if "application/json" not in r.headers.get("Content-Type", ""):
            raise ValueError("도구가 JSON 이력을 반환하지 않습니다")
        raw = r.read(MAX_PAYLOAD + 1)
    if len(raw) > MAX_PAYLOAD:
        raise ValueError("이력 응답이 너무 큽니다")
    return json.loads(raw)


def _rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for k in ("runs", "jobs", "history", "projects", "docs", "notebooks", "agents", "items"):
            if isinstance(value.get(k), list):
                return value[k]
    raise ValueError("도구 이력 형식을 확인하세요")


def _public(r, detail=False):
    d = dict(r)
    out = {k: d[k] for k in ("id", "tool", "source", "title", "link", "created", "updated", "starred", "archived", "revision")}
    out["starred"] = bool(out["starred"])
    out["legacy_shared"] = d.get("source_user") == "owner" and d["uid"] != "owner"
    if detail:
        out["payload"] = json.loads(d["payload"])
    return out


def listing(uid, query):
    tool = query.get("tool", [""])[0]
    q = query.get("q", [""])[0].strip()[:200]
    view = query.get("view", ["active"])[0]
    if view not in ("active", "trash", "starred"):
        raise ValueError("view는 active|trash|starred")
    limit = max(1, min(100, int(query.get("limit", ["50"])[0])))
    offset = max(0, int(query.get("offset", ["0"])[0]))
    where, args = ["uid=?", "archived IS " + ("NOT NULL" if view == "trash" else "NULL")], [uid]
    if tool:
        where.append("tool=?"); args.append(tool)
    off = sorted(toolstate.off())
    if off:   # 관리자가 끈 도구의 작업은 목록에서 뺌(지우지 않음 — 다시 켜면 보임)
        where.append("tool NOT IN (" + ",".join("?" * len(off)) + ")"); args.extend(off)
    if view == "starred":
        where.append("starred=1")
    if q:
        where.append("instr(lower(title), lower(?))>0"); args.append(q)
    sql = " AND ".join(where)
    with db() as c:
        total = c.execute("SELECT count(*) FROM work_items WHERE " + sql, args).fetchone()[0]
        rows = c.execute("SELECT * FROM work_items WHERE " + sql + " ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        sync = [dict(r) for r in c.execute("SELECT tool,checked,ok,count FROM work_sync WHERE uid=?", (uid,))]
    return {"items": [_public(r) for r in rows], "total": total, "limit": limit, "offset": offset, "sync": sync}


def get(uid, item_id, revision=None):
    with db() as c:
        row = c.execute("SELECT * FROM work_items WHERE uid=? AND id=?", (uid, item_id)).fetchone()
        if not row:
            raise FileNotFoundError("작업을 찾을 수 없습니다")
        out = _public(row, True)
        out["versions"] = [dict(v) for v in c.execute("SELECT revision,ts FROM work_versions WHERE uid=? AND item=? ORDER BY revision DESC", (uid, item_id))]
        if revision is not None:
            v = c.execute("SELECT payload FROM work_versions WHERE uid=? AND item=? AND revision=?", (uid, item_id, int(revision))).fetchone()
            if not v:
                raise FileNotFoundError("저장한 버전을 찾을 수 없습니다")
            out["payload"] = json.loads(v[0]); out["selected_revision"] = int(revision)
    return out


def save(uid, req):
    tool = req.get("tool")
    if not isinstance(tool, str):
        raise ValueError("tool을 지정하세요")
    _tool(tool)
    title = req.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ValueError("제목은 1~200자")
    raw, ts = _json(req.get("payload")), now()
    item_id = req.get("id")
    if item_id and not (isinstance(item_id, str) and ID_RE.fullmatch(item_id)):   # 목록·숫자 등은 sqlite 가 500 을 냄
        raise ValueError("작업 id 형식이 틀립니다")
    with db() as c:
        if item_id:
            old = c.execute("SELECT * FROM work_items WHERE uid=? AND id=?", (uid, item_id)).fetchone()
            if not old:
                raise FileNotFoundError("작업을 찾을 수 없습니다")
            if old["tool"] != tool or old["source"] != "saved":
                raise ValueError("도구의 원본 이력은 덮어쓸 수 없습니다. 새 작업으로 저장하세요")
            if req.get("revision") != old["revision"]:
                raise Conflict("다른 창에서 먼저 저장했습니다. 최신 작업을 다시 불러오세요")
            revision = old["revision"] + 1
            cur = c.execute("UPDATE work_items SET title=?,payload=?,updated=?,revision=? WHERE uid=? AND id=? AND revision=?",
                            (title.strip(), raw, ts, revision, uid, item_id, old["revision"]))
            if cur.rowcount != 1:
                raise Conflict("다른 창에서 먼저 저장했습니다")
        else:
            item_id, revision = secrets.token_hex(16), 1
            c.execute("INSERT INTO work_items(id,uid,tool,source,title,payload,link,created,updated) VALUES(?,?,?,'saved',?,?,?,?,?)",
                      (item_id, uid, tool, title.strip(), raw, f"/tool/{tool}", ts, ts))
        c.execute("INSERT INTO work_versions VALUES(?,?,?,?,?)", (item_id, uid, revision, raw, ts))
    return get(uid, item_id)


class Conflict(ValueError):
    pass


def modify(uid, req):
    item_id, action = req.get("id"), req.get("action")
    if not (isinstance(item_id, str) and ID_RE.fullmatch(item_id)):
        raise ValueError("작업 id 형식이 틀립니다")
    with db() as c:
        r = c.execute("SELECT * FROM work_items WHERE uid=? AND id=?", (uid, item_id)).fetchone()
        if not r:
            raise FileNotFoundError("작업을 찾을 수 없습니다")
        if action == "star":
            if not isinstance(req.get("starred"), bool):
                raise ValueError("starred는 true 또는 false")
            c.execute("UPDATE work_items SET starred=? WHERE uid=? AND id=?", (int(req["starred"]), uid, item_id))
        elif action in ("archive", "restore"):
            c.execute("UPDATE work_items SET archived=? WHERE uid=? AND id=?", (now() if action == "archive" else None, uid, item_id))
        elif action == "rename":
            title = req.get("title")
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
                raise ValueError("제목은 1~200자")
            c.execute("UPDATE work_items SET title=? WHERE uid=? AND id=?", (title.strip(), uid, item_id))
        else:
            raise ValueError("action은 star|archive|restore|rename")
    return get(uid, item_id)


def sync(uid, user, tool=None):
    chosen = [_tool(tool)] if tool else [t for t in tools.tools() if t["dir"] in SOURCES and not t.get("hidden") and not toolstate.is_off(t["dir"])]
    report = []
    def fetch(t):
        try:
            rows = _rows(_read(t, SOURCES[t["dir"]][0], user))
            return t, rows, None
        except (OSError, ValueError, TypeError):
            return t, [], "이력을 읽지 못했습니다. 도구 실행 상태를 확인하세요"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(fetch, chosen))
    with db() as c:
        for t, rows, error in values:
            d, count = t["dir"], 0
            for r in rows if not error else []:
                if not isinstance(r, dict):
                    continue
                rid = r.get("run_id") or r.get("id") or r.get("pid")
                if not isinstance(rid, str) or not ID_RE.fullmatch(rid):
                    continue
                ident = hashlib.sha256((uid + "\0" + d + "\0" + user + "\0" + rid).encode()).hexdigest()[:32]
                title = str(r.get("title") or r.get("name") or r.get("question") or r.get("head") or r.get("file") or r.get("text") or t["name"])[:200]
                ts = str(r.get("updated") or r.get("ts") or r.get("created") or r.get("saved") or now())
                try:
                    raw = _json(r)
                except ValueError:
                    continue
                link = entry(d, rid)
                # 다시 동기화해도 이름·즐겨찾기·휴지통과 이미 읽은 상세 사본은 보존한다.
                c.execute("INSERT INTO work_items(id,uid,tool,source,source_id,source_user,title,payload,link,created,updated) VALUES(?,?,?,'imported',?,?,?,?,?,?,?) "
                          "ON CONFLICT(uid,tool,source,source_id) DO UPDATE SET updated=excluded.updated, "
                          "payload=CASE WHEN work_items.detailed=1 THEN work_items.payload ELSE excluded.payload END",
                          (ident, uid, d, rid, user, title, raw, link, ts, ts))
                count += 1
            c.execute("INSERT OR REPLACE INTO work_sync VALUES(?,?,?,?,?)", (uid, d, now(), int(error is None), count))
            report.append({"tool": d, "ok": error is None, "count": count, "error": error})
    return {"sources": report, "count": sum(r["count"] for r in report)}


def entry(tool, rid):
    if tool == "award-local":
        return f"/t/{tool}/api/issued/{rid}.pdf"
    if tool in ("protein-local", "review-local", "agent-cad-local"):
        return f"/tool/{tool}#{rid}"
    if tool == "shield-local":
        return f"/tool/{tool}#h={rid}"
    if tool == "battery-local":
        return f"/tool/{tool}?job={rid}"
    if tool == "agent-builder-local":
        return f"/tool/{tool}/edit/{rid}"
    return f"/tool/{tool}?run={rid}"


def refresh_detail(uid, item_id):
    item = get(uid, item_id)
    with db() as c:
        r = c.execute("SELECT source_id,source_user FROM work_items WHERE uid=? AND id=?", (uid, item_id)).fetchone()
    detail = SOURCES.get(item["tool"], (None, None))[1]
    if item["source"] == "imported" and detail and not item["archived"]:
        try:
            raw = _json(_read(_tool(item["tool"]), detail.format(id=urllib.parse.quote(r[0], safe="")), r[1]))
            with db() as c:
                c.execute("UPDATE work_items SET payload=?,detailed=1 WHERE uid=? AND id=?", (raw, uid, item_id))
            item = get(uid, item_id)
        except (OSError, ValueError, TypeError):
            item["offline"] = True
    return item


def handle_get(ctx):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    try:
        if ctx.p == "/api/workspace":
            return ctx.send(listing(ctx.uid, q))
        m = re.fullmatch(r"/api/workspace/([0-9a-f]{32})", ctx.p)
        if m:
            revision = q.get("revision", [None])[0]
            return ctx.send(get(ctx.uid, m[1], revision) if revision is not None else refresh_detail(ctx.uid, m[1]))
        return ctx.send({"error": "not found"}, code=404)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e)}, code=404)
    except (ValueError, TypeError):
        return ctx.send({"error": "작업 조회 조건을 확인하세요"}, code=400)


def handle_post(ctx, req):
    try:
        if ctx.p == "/api/workspace/save":
            return ctx.send(save(ctx.uid, req))
        if ctx.p == "/api/workspace/modify":
            return ctx.send(modify(ctx.uid, req))
        if ctx.p == "/api/workspace/sync":
            tool = req.get("tool")
            if tool is not None and (not isinstance(tool, str) or tool not in SOURCES):
                raise ValueError("이력 연동을 지원하는 도구를 지정하세요")
            return ctx.send(sync(ctx.uid, ctx.agent_user, tool))
        return ctx.send({"error": "not found"}, code=404)
    except Conflict as e:
        return ctx.send({"error": str(e), "code": "revision_conflict"}, code=409)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e)}, code=404)
    except (ValueError, TypeError) as e:
        return ctx.send({"error": str(e) if isinstance(e, ValueError) else "작업 저장 형식을 확인하세요"}, code=400)
