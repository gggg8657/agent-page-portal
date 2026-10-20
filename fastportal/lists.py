"""계정별 즐겨찾기·최근 사용 — 예전엔 브라우저 localStorage 에만 있어서 캐시를 지우거나 주소·PC 가 바뀌면 사라졌다.
GET /api/lists → {"fav": [...], "recent": [...], "home": "clean"|"plaza"} · POST /api/lists {"kind": "fav"|"recent", "items": [도구 dir, ...]} (목록 통째로 바꿈)
홈 화면 고르기(2026-10-20): POST /api/lists {"kind": "home", "items": ["clean"|"plaza"]} — 깔끔한 홈(기본) 또는 광장. 같은 표에 한 줄로 둔다."""
import json

from . import tools
from .db import now

KINDS = {"fav": 100, "recent": 10}   # 종류 → 최대 개수
PREFS = {"home": ("clean", "plaza")}   # 설정 한 값(첫 값이 기본)


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS user_lists(uid TEXT, kind TEXT, items TEXT, ts TEXT, PRIMARY KEY(uid, kind))")


def get(c, uid):
    out = {k: [] for k in KINDS} | {k: v[0] for k, v in PREFS.items()}
    for kind, items in c.execute("SELECT kind, items FROM user_lists WHERE uid=?", (uid,)):
        if kind in KINDS:
            out[kind] = json.loads(items)
        elif kind in PREFS and (v := json.loads(items)) and v[0] in PREFS[kind]:
            out[kind] = v[0]
    return out


def put(c, uid, req):
    kind, items = req.get("kind"), req.get("items")
    if kind in PREFS:
        if not isinstance(items, list) or len(items) != 1 or items[0] not in PREFS[kind]:
            raise ValueError(f"{kind} 는 " + " 또는 ".join(PREFS[kind]) + " 하나")
        c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
                  (uid, kind, json.dumps(items), now()))
        return {kind: items[0]}
    if kind not in KINDS:
        raise ValueError("kind 는 fav·recent·home 중 하나")
    if not isinstance(items, list) or not all(isinstance(d, str) for d in items):
        raise ValueError("items 는 도구 dir 문자열 목록")
    clean = []
    for d in items:   # 등록된 도구만, 중복 없이, 순서 유지
        if d not in clean and tools.tool(d):
            clean.append(d)
    clean = clean[:KINDS[kind]]
    c.execute("INSERT INTO user_lists(uid, kind, items, ts) VALUES(?,?,?,?) ON CONFLICT(uid, kind) DO UPDATE SET items=excluded.items, ts=excluded.ts",
              (uid, kind, json.dumps(clean, ensure_ascii=False), now()))
    return {kind: clean}
