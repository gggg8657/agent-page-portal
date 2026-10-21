"""도구 켜기·끄기(관리자) — tools.json 은 그대로 두고 포털 DB 의 tool_state 표에 '꺼짐'만 적는다(없으면 켜짐).

꺼진 도구는 관리자 화면 '에이전트 관리' 표에만 보이고, 사용자 쪽 어디에도 나오지 않는다(관리자의 사용자 화면도 같음 — 단순·일관):
  /api/tools·랜딩 분류 · 즐겨찾기·최근 사용(/api/lists — 저장된 항목은 지우지 않아 다시 켜면 돌아옴) · 내 작업 목록(/api/workspace)
  · 흐름 어댑터 목록(이미 저장한 흐름은 실행 때 그 단계에서 '관리자가 이 에이전트를 잠시 껐습니다') · 아이디어 추천 후보 · 리뷰·요청 대상
  · 파동이 펫 추천(DATA/tools-disabled.json 을 읽음) · 화면의 검색·Ctrl+K·따라 하기는 /api/tools 를 따름.
/t/<dir>/… 는 일반 계정에 403 code=tool_disabled, 관리자는 그대로 열어 시험할 수 있다. 사용자 데이터는 지우지 않는다.
바꿀 때마다 감사 기록(tool_disable·tool_enable, 내용 = 이유).

숨기기(visible=0, 끄기와 따로): 도구는 그대로 돌고 링크(/tool/<dir>·/t/<dir>/)로는 누구나 열리지만, 목록에서만 빠진다
  — 에이전트 탐색·홈·광장·분류·검색·Ctrl+K·match/ask·아이디어 후보·흐름 어댑터·파동이 추천(tools-disabled.json 의 hidden).
  /api/tools 에는 hidden=true 로 남겨 화면이 즐겨찾기·최근 사용·도구 화면(/tool/<dir>)에서 찾게 한다(목록은 화면이 hidden 을 거름).
  '숨김' = tools.json 의 hidden(파일에서만 바꿈) ∪ 관리자 숨김 — is_hidden(t). 감사 tool_hide·tool_show."""
import json, os, threading, time

from . import config
from .db import db, now

MSG = "관리자가 이 에이전트를 잠시 껐습니다"
CODE = "tool_disabled"
TTL = 2.0   # 초 — 요청마다 DB 를 읽지 않게(바꾸면 바로 비움)
_C = {"t": 0.0, "v": frozenset(), "h": frozenset()}
_LOCK = threading.Lock()


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS tool_state(dir TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, by TEXT, ts TEXT, reason TEXT)")
    have = {r[1] for r in c.execute("PRAGMA table_info(tool_state)")}
    for col, ty in (("visible", "INTEGER NOT NULL DEFAULT 1"), ("hide_by", "TEXT"), ("hide_ts", "TEXT"), ("hide_reason", "TEXT")):   # 숨기기(2026-10-21) — 예전 DB 에 붙임
        if col not in have:
            c.execute(f"ALTER TABLE tool_state ADD COLUMN {col} {ty}")


def reset():
    with _LOCK:
        _C["t"] = 0.0


def _load():
    with _LOCK:
        if time.monotonic() - _C["t"] < TTL:
            return _C
    try:
        with db() as c:
            rs = c.execute("SELECT dir, enabled, visible FROM tool_state").fetchall()
        v, h = frozenset(r[0] for r in rs if not r[1]), frozenset(r[0] for r in rs if not r[2])
    except Exception:   # 표가 아직 없음(시험·예전 DB) — 잠깐의 DB 오류면 직전 값(빈 값이면 꺼진 도구가 그동안 열림)
        v, h = _C["v"], _C["h"]
    with _LOCK:
        _C.update(t=time.monotonic(), v=v, h=h)
        return _C


def off():
    """꺼진 도구 dir 집합"""
    return _load()["v"]


def admin_hidden():
    """관리자가 숨긴 도구 dir 집합(tools.json hidden 은 빠짐)"""
    return _load()["h"]


def is_off(d):
    return bool(d) and d in off()


def is_hidden(t):
    """목록에서 뺄 도구인지(링크로는 열림) — tools.json hidden(SHOW_HIDDEN 이면 보임) 또는 관리자가 숨김"""
    return (bool(t.get("hidden")) and not config.SHOW_HIDDEN) or t.get("dir") in admin_hidden()


def unlisted(t):
    """목록(카드·검색·추천·후보)에서 뺄 도구 — 숨김 또는 꺼짐"""
    return is_hidden(t) or is_off(t.get("dir"))


def user_hidden(t):
    """일반 계정이 열 수 없는 도구 — tools.json hidden(SHOW_HIDDEN 이면 보임) 또는 관리자가 끔. 관리자 숨김은 링크로 열리므로 아님"""
    return (bool(t.get("hidden")) and not config.SHOW_HIDDEN) or is_off(t.get("dir"))


def rows(c):
    return {r["dir"]: dict(r) for r in c.execute("SELECT dir, enabled, by, ts, reason, visible, hide_by, hide_ts, hide_reason FROM tool_state")}


def state_file():
    return os.environ.get("PORTAL_TOOLS_DISABLED_FILE") or os.path.join(os.path.dirname(config.DB_FILE), "tools-disabled.json")


def write_file():
    """다른 프로세스(파동이 펫 등)가 로그인 없이 읽는 꺼진·숨긴 도구 목록 — dir 만(이유·누가 없음)"""
    p = state_file()
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"disabled": sorted(off()), "hidden": sorted(admin_hidden()), "ts": now()}, f, ensure_ascii=False)
        os.replace(tmp, p)
    except OSError as e:
        print("toolstate: 목록 파일 못 씀", type(e).__name__, flush=True)


def set_state(c, actor, d, enabled, reason=""):
    """켜기·끄기 + 감사. 바뀐 게 없으면 False"""
    reason = str(reason or "").strip()[:200]
    cur = c.execute("SELECT enabled FROM tool_state WHERE dir=?", (d,)).fetchone()
    was = True if cur is None else bool(cur[0])
    if was == bool(enabled):
        return False
    c.execute("INSERT INTO tool_state(dir, enabled, by, ts, reason) VALUES(?,?,?,?,?) ON CONFLICT(dir) DO UPDATE SET "
              "enabled=excluded.enabled, by=excluded.by, ts=excluded.ts, reason=excluded.reason", (d, int(bool(enabled)), actor, now(), reason))
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, "tool_enable" if enabled else "tool_disable", d, reason))
    return True


def set_visible(c, actor, d, visible, reason=""):
    """숨기기·보이기 + 감사(켜짐·꺼짐과 따로). 바뀐 게 없으면 False"""
    reason = str(reason or "").strip()[:200]
    cur = c.execute("SELECT visible FROM tool_state WHERE dir=?", (d,)).fetchone()
    was = True if cur is None else bool(cur[0])
    if was == bool(visible):
        return False
    c.execute("INSERT INTO tool_state(dir, visible, hide_by, hide_ts, hide_reason) VALUES(?,?,?,?,?) ON CONFLICT(dir) DO UPDATE SET "
              "visible=excluded.visible, hide_by=excluded.hide_by, hide_ts=excluded.hide_ts, hide_reason=excluded.hide_reason", (d, int(bool(visible)), actor, now(), reason))
    c.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (now(), actor, "tool_show" if visible else "tool_hide", d, reason))
    return True
