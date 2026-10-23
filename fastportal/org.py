"""조직(계층 소속) — org_units 표·가져오기·경로·조상/하위·장(長)·승인 경로.
원장 → 본부/연구소/사업단/센터 → 부 → 실. 소속은 어느 단계든(부장은 부, 연구소 직속은 연구소에) users.unit_id 로.
장은 자기가 속한 단위를 대표(실장 → 실, 부장 → 부) — dept_heads(unit_id). 소속 변경 요청은 고른 단위의 장, 없으면 상위 단위의 장, … 끝내 관리자.

예전 코드·도구 호환: users.dept·dept_heads.dept·requests.dept 에는 단위 이름을 같이 적는다(이름은 전체에서 겹치지 않게 관리).
가져오기(python -m fastportal.org import <json> …)는 몇 번 돌려도 같은 결과 — 바뀐 것만 감사 기록(audit)과 org_imports 에 남기고,
목록에서 사라진 단위는 소속·장·대기 요청·하위 단위가 걸려 있으면 지우지 않고 비활성(active=0)으로 둔다.
org_units 가 비어 있으면 처음 기동 때 org_data/ 의 공개 조직도 + 공개 조직도에 없는 단위(local-units.json)를 넣고 예전 부서 이름을 단위로 옮긴다."""
import hashlib, json, os, re, secrets, sys

from .db import db, now

TYPES = ("원장", "본부", "부원장", "연구소", "사업단", "단", "센터", "부", "실", "팀", "기타")
SKIP = {"원장", "부원장"}   # 경로 표시·첫 단계 고르기에서 건너뛰는 자리(그 하위가 맨 위 단계로 보임)
DATA = os.path.join(os.path.dirname(__file__), "org_data")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")
NAME_BAD = re.compile(r"[<>\"'&]")


def init(c):
    """표·열 만들기 + (비어 있으면) 기본 조직 넣기 + 예전 부서 이름 → 단위. db.init_db 가 부름"""
    c.executescript("""
    CREATE TABLE IF NOT EXISTS org_units(id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, parent TEXT, sort INTEGER DEFAULT 0,
                                         branch TEXT, active INTEGER DEFAULT 1, note TEXT, src TEXT DEFAULT 'import', ts TEXT);
    CREATE INDEX IF NOT EXISTS org_units_parent ON org_units(parent);
    CREATE TABLE IF NOT EXISTS org_imports(ts TEXT, actor TEXT, src TEXT, sha TEXT, units INTEGER, added INTEGER, updated INTEGER,
                                           deactivated INTEGER, deleted INTEGER, detail TEXT);""")
    for t, col in (("users", "unit_id"), ("dept_heads", "unit_id"), ("requests", "unit_id"), ("requests", "detail")):
        if col not in {r[1] for r in c.execute(f"PRAGMA table_info({t})")}:
            c.execute(f"ALTER TABLE {t} ADD COLUMN {col} TEXT")
    if not c.execute("SELECT 1 FROM org_units").fetchone():
        f = os.path.join(DATA, "kaeri-org.json")
        if os.path.exists(f):
            import_units(c, load(f), "system", "org_data/kaeri-org.json")
            add_local(c, load(os.path.join(DATA, "local-units.json")), "system")
    migrate_legacy(c)


def load(path):
    with open(path, encoding="utf-8") as f:
        v = json.load(f)
    if not isinstance(v, list):
        raise ValueError(f"{path}: 단위 목록(JSON 배열)이어야 합니다")
    return v


def _clean(u):
    """JSON 한 단위 → 표의 한 줄(검사)"""
    if not isinstance(u, dict) or not ID_RE.fullmatch(str(u.get("id") or "")):
        raise ValueError(f"id 가 이상한 단위: {u!r:.80}")
    name = str(u.get("name") or "").strip()
    if not name or len(name) > 60 or NAME_BAD.search(name):
        raise ValueError(f"{u['id']}: 이름 확인")
    t = u.get("type") if u.get("type") in TYPES else "기타"
    p = u.get("parent")
    return {"id": u["id"], "name": name, "type": t, "parent": str(p) if p else None, "sort": int(u.get("order") or 0),
            "branch": str(u["branch"])[:20] if u.get("branch") else None, "note": str(u["note"])[:200] if u.get("note") else None}


def _check_tree(rows, kept=None):
    """부모 참조·순환·이름 겹침 검사(rows: id → 줄). kept: 목록에서 빠져도 비활성으로 남을 수 있는 단위(상위로만 셈)"""
    names, kept = {}, kept or {}
    rows_all = kept | rows
    for r in rows.values():
        if r["parent"] and r["parent"] not in rows_all:
            raise ValueError(f"{r['id']}: 없는 상위 {r['parent']}")
        if r["name"] in names:
            raise ValueError(f"이름이 겹침: {r['name']} ({names[r['name']]}, {r['id']})")
        names[r["name"]] = r["id"]
        seen, x = set(), r
        while x["parent"] and x["parent"] in rows_all:
            if x["id"] in seen:
                raise ValueError(f"순환: {r['id']}")
            seen.add(x["id"])
            x = rows_all[x["parent"]]


def referenced(c, uid_):
    """이 단위에 걸린 것(소속·장·대기 요청·살아 있는 하위) 수"""
    return (c.execute("SELECT COUNT(*) FROM users WHERE unit_id=?", (uid_,)).fetchone()[0]
            + c.execute("SELECT COUNT(*) FROM dept_heads WHERE unit_id=?", (uid_,)).fetchone()[0]
            + c.execute("SELECT COUNT(*) FROM requests WHERE unit_id=? AND status='pending'", (uid_,)).fetchone()[0]
            + c.execute("SELECT COUNT(*) FROM org_units WHERE parent=?", (uid_,)).fetchone()[0])


def import_units(c, units, actor, src):
    """공개 조직도 가져오기(몇 번 돌려도 같음). src='import' 단위만 정리 대상 — 관리자·로컬로 더한 단위는 건드리지 않음"""
    rows = {}
    for u in units:
        r = _clean(u)
        if r["id"] in rows:
            raise ValueError(f"id 가 겹침: {r['id']}")
        rows[r["id"]] = r
    have = {r["id"]: dict(r) for r in c.execute("SELECT * FROM org_units")}
    manual = {k: v for k, v in have.items() if v["src"] != "import"}
    _check_tree(rows | {k: v for k, v in manual.items() if k not in rows}, have)
    ts, log = now(), {"added": [], "updated": [], "deactivated": [], "deleted": [], "reactivated": []}
    for r in rows.values():
        old = have.get(r["id"])
        if not old:
            c.execute("INSERT INTO org_units(id,name,type,parent,sort,branch,active,note,src,ts) VALUES(?,?,?,?,?,?,1,?,'import',?)",
                      (r["id"], r["name"], r["type"], r["parent"], r["sort"], r["branch"], r["note"], ts))
            log["added"].append(r["id"])
            continue
        diff = {k: (old[k], r[k]) for k in ("name", "type", "parent", "sort", "branch", "note") if old[k] != r[k]}
        if old["src"] != "import" or not old["active"]:
            diff["src/active"] = ((old["src"], old["active"]), ("import", 1))
            if not old["active"]:
                log["reactivated"].append(r["id"])
        if diff:
            c.execute("UPDATE org_units SET name=?, type=?, parent=?, sort=?, branch=?, note=?, src='import', active=1, ts=? WHERE id=?",
                      (r["name"], r["type"], r["parent"], r["sort"], r["branch"], r["note"], ts, r["id"]))
            log["updated"].append(r["id"])
            _sync_names(c, r["id"])
            audit(c, actor, "org_update", r["id"], json.dumps(diff, ensure_ascii=False)[:500])
    gone = [k for k, v in have.items() if v["src"] == "import" and k not in rows]
    for k in sorted(gone, key=lambda k: -len(ancestors(c, k))):   # 깊은 것부터 — 하위를 먼저 지워야 상위가 '안 걸림'이 됨
        if referenced(c, k):
            if have[k]["active"]:
                c.execute("UPDATE org_units SET active=0, ts=? WHERE id=?", (ts, k))
                log["deactivated"].append(k)
                audit(c, actor, "org_deactivate", k, f"{have[k]['name']} (가져온 목록에 없음 — 걸린 것이 있어 비활성)")
        else:
            c.execute("DELETE FROM org_units WHERE id=?", (k,))
            log["deleted"].append(k)
            audit(c, actor, "org_delete", k, f"{have[k]['name']} (가져온 목록에 없음)")
    if log["added"]:
        audit(c, actor, "org_add", f"{len(log['added'])}개", src)
    sha = hashlib.sha256(json.dumps(units, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    c.execute("INSERT INTO org_imports VALUES(?,?,?,?,?,?,?,?,?,?)",
              (ts, actor, src[:200], sha, len(rows), len(log["added"]), len(log["updated"]), len(log["deactivated"]), len(log["deleted"]),
               json.dumps({k: v[:50] for k, v in log.items() if v}, ensure_ascii=False)))
    return {k: len(v) for k, v in log.items()} | {"units": len(rows), "sha": sha}


def add_local(c, units, actor):
    """공개 조직도에 없는 단위(없을 때만 넣음, src='local' — 가져오기가 정리하지 않음)"""
    for u in units:
        r = _clean(u)
        if c.execute("SELECT 1 FROM org_units WHERE id=? OR name=?", (r["id"], r["name"])).fetchone():
            continue
        if r["parent"] and not c.execute("SELECT 1 FROM org_units WHERE id=?", (r["parent"],)).fetchone():
            r["parent"] = root(c)
        c.execute("INSERT INTO org_units(id,name,type,parent,sort,branch,active,note,src,ts) VALUES(?,?,?,?,?,?,1,?,'local',?)",
                  (r["id"], r["name"], r["type"], r["parent"], r["sort"], r["branch"], r["note"], now()))
        audit(c, actor, "org_add", r["id"], f"{r['name']} ({r['note'] or '로컬'})")


def migrate_legacy(c):
    """예전 부서 이름(users.dept·dept_heads.dept·대기 요청) → 단위 id. 이름이 같은 단위가 없으면 최상위 아래 '기타'로 만듦(공개 조직도에 없음).
    이미 옮긴 줄은 그대로(몇 번 불려도 같음)"""
    if not c.execute("SELECT 1 FROM org_units").fetchone():
        return
    need = {r[0] for r in c.execute("SELECT DISTINCT dept FROM users WHERE unit_id IS NULL AND COALESCE(dept,'')<>''")}
    need |= {r[0] for r in c.execute("SELECT DISTINCT dept FROM dept_heads WHERE unit_id IS NULL")}
    need |= {r[0] for r in c.execute("SELECT DISTINCT dept FROM requests WHERE unit_id IS NULL AND status='pending' AND kind IN ('dept_join','dept_head') AND COALESCE(dept,'')<>''")}
    for name in sorted(need):
        u = by_name(c, name)
        if not u:
            u = "x-" + hashlib.sha1(name.encode()).hexdigest()[:10]
            c.execute("INSERT OR IGNORE INTO org_units(id,name,type,parent,sort,active,note,src,ts) VALUES(?,?,?,?,999,1,?,'local',?)",
                      (u, name.strip()[:60], "기타", root(c), "공개 조직도에 없음 (예전 부서)", now()))
            audit(c, "system", "org_add", u, f"{name} (예전 부서)")
        c.execute("UPDATE users SET unit_id=? WHERE unit_id IS NULL AND dept=?", (u, name))
        c.execute("UPDATE dept_heads SET unit_id=? WHERE unit_id IS NULL AND dept=?", (u, name))
        c.execute("UPDATE requests SET unit_id=? WHERE unit_id IS NULL AND status='pending' AND kind IN ('dept_join','dept_head') AND dept=?", (u, name))


def audit(c, actor, action, target, detail=""):
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, action, target, detail))


# ── 조회 ─────────────────────────────────────────────────────────────────
def root(c):
    r = c.execute("SELECT id FROM org_units WHERE parent IS NULL ORDER BY sort, id LIMIT 1").fetchone()
    return r[0] if r else None


def by_name(c, name):
    """이름 → 단위 id(살아 있는 것 우선, 하나일 때만)"""
    rows = c.execute("SELECT id FROM org_units WHERE name=? ORDER BY active DESC", ((name or "").strip(),)).fetchall()
    return rows[0][0] if rows else None


def unit(c, uid_):
    return c.execute("SELECT * FROM org_units WHERE id=?", (uid_,)).fetchone() if uid_ else None


def ancestors(c, uid_):
    """[자기, 상위, …, 최상위] id"""
    out = []
    while uid_ and uid_ not in out:
        out.append(uid_)
        r = c.execute("SELECT parent FROM org_units WHERE id=?", (uid_,)).fetchone()
        uid_ = r[0] if r else None
    return out


def descendants(c, uid_):
    """자기 포함 하위 전부 id"""
    out, todo = {uid_}, [uid_]
    while todo:
        for r in c.execute("SELECT id FROM org_units WHERE parent=?", (todo.pop(),)):
            if r[0] not in out:
                out.add(r[0]); todo.append(r[0])
    return out


def info(c, uid_):
    """화면용 {id, name, type, path(이름 — 원장·부원장 건너뜀), ids(path 와 같은 순서), active} 또는 None"""
    r = unit(c, uid_)
    if not r:
        return None
    chain = [unit(c, x) for x in reversed(ancestors(c, uid_))]
    shown = [x for x in chain if x["type"] not in SKIP] or chain[-1:]
    return {"id": r["id"], "name": r["name"], "type": r["type"], "path": [x["name"] for x in shown], "ids": [x["id"] for x in shown],
            "active": bool(r["active"])}


def label(c, uid_, fallback=""):
    i = info(c, uid_)
    return " › ".join(i["path"]) if i else fallback


def public(c):
    """고를 수 있는 단위(살아 있는 것) — 로그인 전에도(가입 화면). 이름·종류·상위·순서·분원만"""
    return [{k: r[k] for k in ("id", "name", "type", "parent", "sort", "branch")}
            for r in c.execute("SELECT * FROM org_units WHERE active=1 ORDER BY sort, name")]


# ── 소속·장·승인 경로 ───────────────────────────────────────────────────
def set_member(c, uid, unit_id):
    """소속 바꾸기(None = 소속 없음). 예전 열 users.dept 에는 단위 이름"""
    r = unit(c, unit_id)
    c.execute("UPDATE users SET unit_id=?, dept=? WHERE id=?", (r["id"] if r else None, r["name"] if r else "", uid))


def heads(c, unit_id):
    return [r[0] for r in c.execute("SELECT uid FROM dept_heads WHERE unit_id=? ORDER BY ts", (unit_id,))]


def head_units(c, uid):
    return [r[0] for r in c.execute("SELECT unit_id FROM dept_heads WHERE uid=? AND unit_id IS NOT NULL ORDER BY dept", (uid,))]


def grant_head(c, unit_id, uid):
    r = unit(c, unit_id)
    c.execute("INSERT OR IGNORE INTO dept_heads(dept, uid, ts, unit_id) VALUES(?,?,?,?)", (r["name"], uid, now(), unit_id))


def revoke_head(c, unit_id, uid):
    c.execute("DELETE FROM dept_heads WHERE unit_id=? AND uid=?", (unit_id, uid))


def approvers(c, unit_id, exclude=None):
    """(장이 있는 단위 id, 그 장들) — 고른 단위부터 위로 올라가며 첫 장(요청한 본인 빼고). 없으면 (None, []) = 관리자"""
    for u in ancestors(c, unit_id):
        hs = [h for h in heads(c, u) if h != exclude]
        if hs:
            return u, hs
    return None, []


def _sync_names(c, unit_id):
    """단위 이름이 바뀌면 예전 열(users.dept·dept_heads.dept·대기 요청)도"""
    r = unit(c, unit_id)
    if r:
        c.execute("UPDATE users SET dept=? WHERE unit_id=?", (r["name"], unit_id))
        c.execute("UPDATE dept_heads SET dept=? WHERE unit_id=?", (r["name"], unit_id))
        c.execute("UPDATE requests SET dept=? WHERE unit_id=? AND status='pending' AND kind IN ('dept_join','dept_head')", (r["name"], unit_id))


# ── 관리자 ────────────────────────────────────────────────────────────────
def admin_view(c):
    nm = {r[0]: r[1] for r in c.execute("SELECT id, name FROM users")}
    hd = {}
    for r in c.execute("SELECT unit_id, uid FROM dept_heads WHERE unit_id IS NOT NULL"):
        hd.setdefault(r[0], []).append({"uid": r[1], "name": nm.get(r[1], r[1])})
    mem = {r[0]: r[1] for r in c.execute("SELECT unit_id, COUNT(*) FROM users WHERE unit_id IS NOT NULL AND deactivated IS NULL AND anonymized IS NULL GROUP BY unit_id")}
    units = [dict(r) | {"active": bool(r["active"]), "heads": hd.get(r["id"], []), "members": mem.get(r["id"], 0)}
             for r in c.execute("SELECT id, name, type, parent, sort, branch, active, note, src, ts FROM org_units ORDER BY sort, name")]
    imp = [dict(r) | {"detail": json.loads(r["detail"] or "{}")} for r in c.execute("SELECT * FROM org_imports ORDER BY rowid DESC LIMIT 10")]
    none = c.execute("SELECT COUNT(*) FROM users WHERE unit_id IS NULL AND deactivated IS NULL AND anonymized IS NULL").fetchone()[0]
    return {"units": units, "imports": imp, "unassigned": none, "types": list(TYPES)}


def _parent_ok(c, uid_, parent):
    if not parent:
        raise ValueError("상위 단위를 고르세요")
    p = unit(c, parent)
    if not p or not p["active"]:
        raise ValueError("없거나 비활성인 상위 단위")
    if uid_ and uid_ in ancestors(c, parent):
        raise ValueError("자기 하위로는 옮길 수 없습니다")


def _name_ok(c, name, uid_=None):
    name = str(name or "").strip()
    if not name or len(name) > 60 or NAME_BAD.search(name):
        raise ValueError("이름을 확인하세요(1~60자, < > \" ' & 빼고)")
    if c.execute("SELECT 1 FROM org_units WHERE name=? AND id<>?", (name, uid_ or "")).fetchone():
        raise ValueError("같은 이름의 단위가 이미 있습니다")
    return name


def create(c, actor, name, type_, parent, note="", sort=None):
    name = _name_ok(c, name)
    if type_ not in TYPES:
        raise ValueError("종류를 고르세요")
    _parent_ok(c, None, parent)
    uid_ = "u-" + secrets.token_hex(4)
    if sort is None:
        sort = (c.execute("SELECT MAX(sort) FROM org_units WHERE parent=?", (parent,)).fetchone()[0] or 0) + 1
    c.execute("INSERT INTO org_units(id,name,type,parent,sort,active,note,src,ts) VALUES(?,?,?,?,?,1,?,'admin',?)",
              (uid_, name, type_, parent, int(sort), (note or "")[:200] or None, now()))
    audit(c, actor, "org_add", uid_, f"{name} ({type_}) ← {label(c, parent)}")
    return uid_


def admin_post(c, actor, req):
    """POST /api/admin/org {op: add|update|move|deactivate|activate, …}"""
    op, uid_ = req.get("op"), str(req.get("id") or "")
    if op == "add":
        return {"id": create(c, actor, req.get("name"), req.get("type"), str(req.get("parent") or ""), str(req.get("note") or ""))}
    r = unit(c, uid_)
    if not r:
        raise ValueError("없는 단위")
    if op == "update":
        name = _name_ok(c, req.get("name", r["name"]), uid_)
        t = req.get("type", r["type"])
        if t not in TYPES:
            raise ValueError("종류를 고르세요")
        note = str(req.get("note") if "note" in req else (r["note"] or ""))[:200] or None
        sort = int(req.get("sort", r["sort"]) or 0)
        c.execute("UPDATE org_units SET name=?, type=?, note=?, sort=?, ts=? WHERE id=?", (name, t, note, sort, now(), uid_))
        _sync_names(c, uid_)
        detail = f"{r['name']} → {name}" if name != r["name"] else name
    elif op == "move":
        parent = str(req.get("parent") or "")
        _parent_ok(c, uid_, parent)
        c.execute("UPDATE org_units SET parent=?, ts=? WHERE id=?", (parent, now(), uid_))
        detail = f"{r['name']} → {label(c, parent)}"
    elif op == "deactivate":   # 고르기 목록에서 빠짐. 소속·장은 그대로(관리자가 옮김)
        if c.execute("SELECT 1 FROM org_units WHERE parent=? AND active=1", (uid_,)).fetchone():
            raise ValueError("살아 있는 하위 단위가 있습니다 — 하위부터 비활성으로")
        if c.execute("SELECT 1 FROM requests WHERE unit_id=? AND status='pending'", (uid_,)).fetchone():
            raise ValueError("이 단위로 온 대기 요청이 있습니다 — 처리한 뒤에")
        c.execute("UPDATE org_units SET active=0, ts=? WHERE id=?", (now(), uid_))
        detail = r["name"]
    elif op == "activate":
        if r["parent"] and not (unit(c, r["parent"]) or {"active": 0})["active"]:
            raise ValueError("상위 단위가 비활성입니다 — 상위부터")
        c.execute("UPDATE org_units SET active=1, ts=? WHERE id=?", (now(), uid_))
        detail = r["name"]
    else:
        raise ValueError("op")
    audit(c, actor, "org_" + op, uid_, detail)
    return {}


# ── python -m fastportal.org import <json> [<json> …] [--local <json>] [--dry-run] ─────
def main(argv):
    import argparse
    from . import db as dbm
    p = argparse.ArgumentParser(prog="python -m fastportal.org")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="공개 조직도 JSON 가져오기(몇 번 돌려도 같음)")
    i.add_argument("json", nargs="+"); i.add_argument("--local", action="append", default=[], help="공개 조직도에 없는 단위(없을 때만 넣음)")
    i.add_argument("--dry-run", action="store_true", help="바뀔 수만 보이고 되돌림")
    sub.add_parser("status", help="단위 수·최근 가져오기")
    o = p.parse_args(argv)
    dbm.init_db()
    if o.cmd == "status":
        with db() as c:
            v = admin_view(c)
        print(f"단위 {len(v['units'])}개(살아 있음 {sum(u['active'] for u in v['units'])}) · 소속 없는 계정 {v['unassigned']}명")
        for x in v["imports"][:5]:
            print(f"  {x['ts']} {x['actor']} {x['src']} sha={x['sha']} 단위 {x['units']} +{x['added']} ~{x['updated']} 비활성 {x['deactivated']} 삭제 {x['deleted']}")
        return
    units = [u for f in o.json for u in load(f)]
    c = dbm._connect()
    try:
        out = import_units(c, units, "cli", " + ".join(os.path.basename(f) for f in o.json))
        for f in o.local:
            add_local(c, load(f), "cli")
        migrate_legacy(c)
        if o.dry_run:
            c.rollback()
        else:
            c.commit()
    finally:
        c.close()
    print(("(dry-run, 되돌림) " if o.dry_run else "") + json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
