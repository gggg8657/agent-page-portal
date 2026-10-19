"""계정별 즐겨찾기·최근 사용 — 예전엔 브라우저 localStorage 에만 있어서 캐시를 지우거나 주소·PC 가 바뀌면 사라졌다.
GET /api/lists → {"fav": [...], "recent": [...]} · POST /api/lists {"kind": "fav"|"recent", "items": [도구 dir, ...]} (목록 통째로 바꿈)"""
import json

from . import tools
from .db import now

KINDS = {"fav": 100, "recent": 10}   # 종류 → 최대 개수


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS user_lists(uid TEXT, kind TEXT, items TEXT, ts TEXT, PRIMARY KEY(uid, kind))")


def get(c, uid):
    out = {k: [] for k in KINDS}
    for kind, items in c.execute("SELECT kind, items FROM user_lists WHERE uid=?", (uid,)):
        if kind in out:
            out[kind] = json.loads(items)
    return out


def put(c, uid, req):
    kind, items = req.get("kind"), req.get("items")
    if kind not in KINDS:
        raise ValueError("kind 는 fav 또는 recent")
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
