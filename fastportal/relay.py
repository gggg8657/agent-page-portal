"""중계 막기 — 이 서버 안의 다른 OS 계정 프로그램이 포털(:8700)을 대신 불러 주는 것(서버끼리 중계)을 거절한다.

같은 서버에서 온 접속은 출발지 주소가 127.0.0.1·서버 자기 주소라 IP 로는 누구 것인지 모른다. 리눅스는 이 서버 안의 TCP 소켓을
/proc/net/tcp·tcp6 에 소켓 주인(uid)과 함께 보여 주므로, 접속한 쪽(peer ip:port)을 거기서 찾아 주인 uid 를 본다.
- 표에 없으면(다른 PC·컨테이너에서 온 접속) 그대로 받는다.
- 표에 있고 주인이 포털을 띄운 계정(또는 PORTAL_RELAY_ALLOW_UIDS)이면 받는다 — 도구·스크립트·본인 SSH 터널.
- 그 밖의 계정이면 403 code relay_blocked. 끄기: PORTAL_RELAY_GUARD=0
"""
import os
import pwd
import socket
import threading
import time

ENABLED = os.environ.get("PORTAL_RELAY_GUARD", "1") != "0"


def _uids(spec):
    out = {os.getuid()}
    for x in (spec or "").replace(" ", "").split(","):
        if not x:
            continue
        try:
            out.add(int(x) if x.isdigit() else pwd.getpwnam(x).pw_uid)
        except (KeyError, ValueError):
            print(f"relay: PORTAL_RELAY_ALLOW_UIDS 의 '{x}' 를 모름 — 무시", flush=True)
    return out


ALLOW = _uids(os.environ.get("PORTAL_RELAY_ALLOW_UIDS", ""))
_cache = {}          # (ip, port) → (uid|None, 시각)
_lock = threading.Lock()
_TTL = 60
_warned = {}         # uid → 마지막 기록 시각(한 시간에 한 번만 남김)


def _addr(hexaddr):
    """'0100007F:1F90' / IPv6 32자 → (ip 문자열, port)"""
    h, p = hexaddr.split(":")
    port = int(p, 16)
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw[::-1]), port
    words = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))   # 4바이트 단위 리틀엔디언
    ip = socket.inet_ntop(socket.AF_INET6, words)
    if ip.startswith("::ffff:") and "." in ip:
        ip = ip[7:]
    return ip, port


def parse(text, server_port):
    """/proc/net/tcp 내용 → {(local ip, local port): uid} 중 원격 포트가 server_port 인 것(포털로 향한 소켓)"""
    out = {}
    for line in text.splitlines()[1:]:
        f = line.split()
        if len(f) < 8:
            continue
        if f[3] != "01":   # ESTABLISHED 만 — TIME_WAIT 등은 주인이 없어 uid 0 으로 보임
            continue
        try:
            lip, lport = _addr(f[1])
            _, rport = _addr(f[2])
            if rport == server_port:
                out[(lip, lport)] = int(f[7])
        except ValueError:
            continue
    return out


def _lookup(ip, port, server_port):
    found = {}
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as fh:
                found.update(parse(fh.read(), server_port))
        except OSError:
            pass
    return found.get((ip, port))


def peer_uid(ip, port, server_port):
    """이 서버 안 소켓이면 주인 uid, 아니면 None(밖에서 온 접속)"""
    key = (ip, port)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[1] < _TTL:
            return hit[0]
    uid = _lookup(ip, port, server_port)
    with _lock:
        if len(_cache) > 4096:
            _cache.clear()
        _cache[key] = (uid, now)
    return uid


def check(request):
    """받으면 None, 막으면 (uid, 사용자 이름)"""
    if not ENABLED or not request.client:
        return None
    srv = request.scope.get("server") or (None, None)
    if not srv[1]:
        return None
    uid = peer_uid(request.client.host, request.client.port, srv[1])
    if uid is None or uid in ALLOW:
        return None
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        name = str(uid)
    return uid, name


def should_log(uid):
    now = time.time()
    with _lock:
        if now - _warned.get(uid, 0) < 3600:
            return False
        _warned[uid] = now
        return True
