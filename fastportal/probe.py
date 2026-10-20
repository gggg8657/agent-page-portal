"""엿보기(F12·스캐너) 탐지·잠시 차단 — 접속지(IP)+계정별로 최근 WINDOW 초 동안 거절 응답(401·403·404·405·CSRF 거절)을 셈.
  - 서로 다른 주소(메서드+경로, 쿼리 뺌)가 PORTAL_PROBE_MAX(기본 30) 개 이상이거나, 거절이 모두 MAX×5(기본 150) 번 이상이면 차단
    (같은 주소를 되풀이하는 화면 폴링 404 는 '다른 주소' 수를 늘리지 않음 — 1초 폴링 60번/분 < 150)
  - 차단: 모든 요청에 429 + Retry-After(code=probe_blocked). 처음 PORTAL_PROBE_BLOCK_S(기본 60초), 24시간 안에 다시 걸리면 2배씩(최대 1시간)
  - 차단마다 감사 기록(audit: action=probe_block, 누가·IP·건수·주소 예시) + 관리자 알림함(작업 알림 'portal-security')
  - 빼는 것: 이 서버 안 + 로컬 주인 토큰(X-Local-Owner) 요청, 상태 확인(/api/version · /healthz · /t/<도구>/healthz),
    브라우저가 알아서 부르는 주소(/favicon.ico · /robots.txt · /apple-touch-icon* · /.well-known/*)
  - 메모리에만 둠 — 포털을 다시 켜면 셈·차단이 모두 풀림(감사 기록은 DB 에 남음)
env: PORTAL_PROBE(1 켬·0 끔) PORTAL_PROBE_MAX(30) PORTAL_PROBE_WINDOW(60초) PORTAL_PROBE_BLOCK_S(60초) PORTAL_PROBE_BLOCK_MAX_S(3600초)"""
import os, re, threading, time
from collections import deque

ENABLED = os.environ.get("PORTAL_PROBE", "1") != "0"
MAX = int(os.environ.get("PORTAL_PROBE_MAX") or 30)
WINDOW = float(os.environ.get("PORTAL_PROBE_WINDOW") or 60)
BLOCK_S = float(os.environ.get("PORTAL_PROBE_BLOCK_S") or 60)
BLOCK_MAX_S = float(os.environ.get("PORTAL_PROBE_BLOCK_MAX_S") or 3600)
TOTAL_FACTOR = 5
STRIKE_RESET_S = 24 * 3600
COUNTED = {401, 403, 404, 405}
EXEMPT = re.compile(r"/(favicon\.ico|robots\.txt|apple-touch-icon[\w.-]*\.png|\.well-known/.*|healthz|api/version|t/[a-z0-9-]+/healthz)")

_LOCK = threading.Lock()
_EV = {}        # (ip, 계정) → deque[(시각, 메서드 경로)]
_BLOCK = {}     # (ip, 계정) → 풀리는 시각
_STRIKE = {}    # (ip, 계정) → (차단 횟수, 마지막 차단 시각)
_IPS = {}       # ip → 차단 중인 키 수(빠른 확인용)


def exempt_path(p):
    return bool(EXEMPT.fullmatch(p))


def _unblock(k):
    _BLOCK.pop(k, None)
    n = _IPS.get(k[0], 0) - 1
    if n > 0:
        _IPS[k[0]] = n
    else:
        _IPS.pop(k[0], None)


def ip_has_block(ip, now=None):
    """이 IP 에 차단된 키가 있는지(계정 확인 전 싼 확인)"""
    if not ENABLED or ip not in _IPS:
        return False
    now = now or time.time()
    with _LOCK:
        for k in [k for k in _BLOCK if k[0] == ip and _BLOCK[k] <= now]:
            _unblock(k)
        return ip in _IPS


def blocked(ip, user, now=None):
    """차단 중이면 남은 초(올림), 아니면 0"""
    if not ENABLED:
        return 0
    now = now or time.time()
    k = (ip or "", user or "")
    with _LOCK:
        until = _BLOCK.get(k)
        if until is None:
            return 0
        if until <= now:
            _unblock(k)
            return 0
        return int(until - now + 0.999)


def record(ip, user, method, path, now=None):
    """거절 응답 하나 → 차단을 새로 걸었으면 (차단 초, 거절 수, 다른 주소 수, 주소 예시), 아니면 None"""
    if not ENABLED:
        return None
    now = now or time.time()
    k = (ip or "", user or "")
    with _LOCK:
        if k in _BLOCK:
            if _BLOCK[k] > now:
                return None
            _unblock(k)
        q = _EV.setdefault(k, deque())
        q.append((now, f"{method} {path[:200]}"))
        while q and q[0][0] < now - WINDOW:
            q.popleft()
        if len(q) > MAX * TOTAL_FACTOR * 2:   # 메모리 상한
            q.popleft()
        distinct = {p for _, p in q}
        if len(distinct) < MAX and len(q) < MAX * TOTAL_FACTOR:
            if len(_EV) > 20000:   # 오래된 키 정리(메모리 상한)
                for kk in [kk for kk, qq in _EV.items() if not qq or qq[-1][0] < now - WINDOW][:5000]:
                    _EV.pop(kk, None)
            return None
        n, last = _STRIKE.get(k, (0, 0))
        n = 1 if now - last > STRIKE_RESET_S else n + 1
        _STRIKE[k] = (n, now)
        sec = min(BLOCK_MAX_S, BLOCK_S * (2 ** (n - 1)))
        _BLOCK[k] = now + sec
        _IPS[k[0]] = _IPS.get(k[0], 0) + 1
        sample = sorted(distinct)[:8]
        total = len(q)
        _EV.pop(k, None)
        return int(sec), total, len(distinct), sample


def clear(ip=None, user=None):
    """차단·셈 지우기(시험·관리용). 인자 없으면 전부"""
    with _LOCK:
        for d in (_EV, _BLOCK, _STRIKE):
            for k in [k for k in d if (ip is None or k[0] == ip) and (user is None or k[1] == user)]:
                d.pop(k, None)
        _IPS.clear()
        for k in _BLOCK:
            _IPS[k[0]] = _IPS.get(k[0], 0) + 1


def snapshot():
    """지금 차단 목록(관리자 화면용) — [{ip, user, seconds}]"""
    now = time.time()
    with _LOCK:
        return [{"ip": k[0], "user": k[1], "seconds": int(v - now + 0.999)} for k, v in _BLOCK.items() if v > now]


def is_counted(status, body_code=None):
    return status in COUNTED or body_code == "csrf"
