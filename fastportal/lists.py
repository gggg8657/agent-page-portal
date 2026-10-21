"""계정별 즐겨찾기·최근 사용 — 예전엔 브라우저 localStorage 에만 있어서 캐시를 지우거나 주소·PC 가 바뀌면 사라졌다.
GET /api/lists → {"fav": [...], "recent": [...], "home": "clean"|"plaza"} · POST /api/lists {"kind": "fav"|"recent", "items": [도구 dir, ...]} (목록 통째로 바꿈)
홈 화면 고르기(2026-10-20): POST /api/lists {"kind": "home", "items": ["clean"|"plaza"]} — 깔끔한 홈(기본) 또는 광장. 같은 표에 한 줄로 둔다.
홈 방문(인사 문구용): GET 의 "seen" = 지난번 홈을 연 시각(UTC ISO) | ""(처음 — 새 계정) | "?"(이 기능 전부터 쓰던 계정, 모름).
  POST {"kind": "seen"} → 지금 시각을 적고 {"seen": 지난번 값} 을 돌려줌(홈을 열 때 한 번).
화면 디자인(2026-10-21): GET 의 "design" = {design, layout, font, orbit, icons} | null(저장한 적 없음 — 화면이 이 브라우저 값을 한 번 옮길지 정함).
  POST {"kind": "design", "items": {...}} — 다섯 값을 통째로 바꿈(없는 키는 기본값, 모르는 값·키는 400). 어느 PC·브라우저에서나 같은 디자인."""
import datetime
import json

from . import timeutil, tools, toolstate
from .db import now

KINDS = {"fav": 100, "recent": 10}   # 종류 → 최대 개수
PREFS = {"home": ("clean", "plaza")}   # 설정 한 값(첫 값이 기본)
# 화면 디자인(agent-page-web src/lib/design.ts 와 같은 목록) — 키 → 고를 수 있는 값(첫 값이 기본). design "" = 구버전 계열(밝기·계열은 /api/account/theme), orbit "" = 디자인 기본값
DESIGN = {
    "design": ("", "kaeri", "hub", "bento", "editorial", "swiss", "glass", "atomic", "nordic", "executive", "slate", "warm", "triad"),
    "layout": ("a", "f", "b", "c", "d"),
    "font": ("pretendard", "noto"),
    "orbit": ("", "on", "off"),
    "icons": ("on", "off"),
}


def _design(v):
    """보낸 디자인 설정 확인 — 모르는 키·값은 ValueError, 없는 키는 기본값"""
    if not isinstance(v, dict):
        raise ValueError("design 의 items 는 {design, layout, font, orbit, icons} 객체")
    bad = [k for k in v if k not in DESIGN]
    if bad:
        raise ValueError("모르는 키: " + ", ".join(map(str, bad)))
    out = {}
    for k, allowed in DESIGN.items():
        x = v.get(k, allowed[0])
        if x not in allowed:
            raise ValueError(f"{k} 는 " + "·".join(a or '""' for a in allowed) + " 중 하나")
        out[k] = x
    return out


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS user_lists(uid TEXT, kind TEXT, items TEXT, ts TEXT, PRIMARY KEY(uid, kind))")


def get(c, uid):
    out = {k: [] for k in KINDS} | {k: v[0] for k, v in PREFS.items()} | {"seen": _seen_default(c, uid), "design": None}
    for kind, items in c.execute("SELECT kind, items FROM user_lists WHERE uid=?", (uid,)):
        if kind == "seen":
            out["seen"] = json.loads(items)
            continue
        if kind == "design":
            try:
                out["design"] = _design(json.loads(items))
            except ValueError:   # 목록이 바뀌어 옛 값이 틀리면 기본값으로(저장값은 다음 저장 때 바뀜)
                out["design"] = {k: a[0] for k, a in DESIGN.items()}
            continue
        if kind in KINDS:   # 관리자가 끈 도구는 빼고 보냄(저장된 항목은 그대로 — 다시 켜면 돌아옴, put 이 지키며 다시 씀)
            off = toolstate.off()
            out[kind] = [d for d in json.loads(items) if d not in off]
        elif kind in PREFS and (v := json.loads(items)) and v[0] in PREFS[kind]:
            out[kind] = v[0]
    return out


def _seen_default(c, uid):
    """홈 방문 기록이 없을 때 — 하루 안에 만든 계정이면 처음(""), 그 전부터 쓰던 계정·로컬 접속은 모름("?")"""
    try:
        r = c.execute("SELECT ts FROM users WHERE id=?", (uid,)).fetchone()
    except Exception:   # 표 없음(시험)
        r = None
    if not r or not r[0]:
        return "?"
    try:
        made = datetime.datetime.fromisoformat(str(r[0])[:19])
    except ValueError:
        return "?"
    return "" if timeutil.utcnow() - made < datetime.timedelta(days=1) else "?"


def put(c, uid, req):
    kind, items = req.get("kind"), req.get("items")
    if kind == "seen":   # 홈을 염 — 지난번 값을 돌려주고 지금으로
        prev = get(c, uid)["seen"]
        c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
                  (uid, "seen", json.dumps(datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")), now()))
        return {"seen": prev}
    if kind == "design":
        d = _design(items)
        c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
                  (uid, "design", json.dumps(d), now()))
        return {"design": d}
    if kind in PREFS:
        if not isinstance(items, list) or len(items) != 1 or items[0] not in PREFS[kind]:
            raise ValueError(f"{kind} 는 " + " 또는 ".join(PREFS[kind]) + " 하나")
        c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
                  (uid, kind, json.dumps(items), now()))
        return {kind: items[0]}
    if kind not in KINDS:
        raise ValueError("kind 는 fav·recent·home·seen·design 중 하나")
    if not isinstance(items, list) or not all(isinstance(d, str) for d in items):
        raise ValueError("items 는 도구 dir 문자열 목록")
    clean = []
    for d in items:   # 등록된 도구만, 중복 없이, 순서 유지
        if d not in clean and tools.tool(d):
            clean.append(d)
    clean = clean[:KINDS[kind]]
    off = toolstate.off()
    if off:   # 화면은 꺼진 도구를 모름(get 이 뺌) — 저장돼 있던 꺼진 도구 항목은 그 자리에 그대로 둠
        r = c.execute("SELECT items FROM user_lists WHERE uid=? AND kind=?", (uid, kind)).fetchone()
        old = json.loads(r[0]) if r else []
        for i, d in enumerate(old):
            if d in off and d not in clean:
                clean.insert(min(i, len(clean)), d)
        clean = clean[:KINDS[kind] + len(off)]
    c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
              (uid, kind, json.dumps(clean, ensure_ascii=False), now()))
    return {kind: [d for d in clean if d not in off]}
