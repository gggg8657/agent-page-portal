"""도구 목록(tools.json)·시작·중지·등록·켜짐 확인(캐시)·혼합형 host(다른 컨테이너)·GPU 현황·홈 목록 데이터(사용 횟수·순위·게시글)."""
import asyncio, datetime, json, os, re, socket, subprocess, threading, time, urllib.request

from . import config
from .config import BASE

# ── tools.json ──────────────────────────────────────────────────────────
_TOOLS = {"m": None, "v": []}
_TOOLS_LOCK = threading.Lock()


def tools():
    """tools.json — 바뀌었을 때만 다시 읽음(수정 시각 비교). 손으로 고치거나 다른 프로세스가 추가해도 새로고침이면 반영"""
    m = os.stat(config.TOOLS_FILE).st_mtime_ns
    with _TOOLS_LOCK:
        if _TOOLS["m"] != m:
            _TOOLS.update(m=m, v=json.load(open(config.TOOLS_FILE, encoding="utf-8")))
        return [dict(t) for t in _TOOLS["v"]]   # 복사본 — 호출한 쪽이 고쳐도 캐시는 그대로


def tool(d):
    return next((t for t in tools() if t["dir"] == d), None)


# 혼합형(도구가 다른 컨테이너·서버에): tools.json 항목의 "host"(예: "tools-gpu", "10.0.0.5"). 있으면 상태 확인·프록시는 http://<host>:<port>,
# 시작·중지는 하지 않음(그쪽이 늘 켜 둠). 값은 관리자만 고치는 tools.json 에서만 오고(/api/tool_add 는 host 를 받지 않음),
# 호스트명/IPv4 글자만 허용 — 스킴·경로·포트·@·공백·IPv6 는 거절(틀리면 꺼진 도구로 보이고 프록시하지 않음)
HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")


def is_remote(t):
    return bool(t.get("host"))


def tool_host(t):
    """도구에 접속할 주소. host 없으면 127.0.0.1, 형식이 틀리면 None(접속하지 않음)"""
    h = t.get("host")
    if not h:
        return "127.0.0.1"
    return h if isinstance(h, str) and HOST_RE.fullmatch(h) else None


def tool_addr(t):
    return (tool_host(t), t["port"])


# ── 켜짐 확인: 5초 캐시 — 탭 수백 개가 /api/tools 를 폴링해도 포트 확인은 5초에 한 번(동시에 와도 한 번) ──
_ALIVE = {"t": 0.0, "v": {}}
_ALIVE_LOCK = asyncio.Lock()


def alive_reset():
    """켜짐 캐시 비움 — 시작·중지 뒤 다음 /api/tools 가 바로 다시 확인"""
    _ALIVE["t"] = 0.0


async def _alive(addr):
    host, port = addr
    if not host:   # tools.json 의 host 형식이 틀림 — 접속하지 않음
        return False
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), 0.5)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def alive_map(addrs):
    """(host, port) → 켜짐. host 는 tool_addr(없으면 127.0.0.1, 형식 틀리면 None)"""
    async with _ALIVE_LOCK:
        if time.monotonic() - _ALIVE["t"] < config.ALIVE_TTL and set(addrs) <= set(_ALIVE["v"]):
            return _ALIVE["v"]
        addrs = sorted(set(addrs), key=lambda a: (a[0] or "", a[1]))
        res = await asyncio.gather(*(_alive(a) for a in addrs))
        _ALIVE.update(t=time.monotonic(), v=dict(zip(addrs, res)))
        return _ALIVE["v"]


def alive_sync(addr):
    if not addr[0]:
        return False
    try:
        with socket.create_connection(addr, timeout=0.5):
            return True
    except OSError:
        return False


# ── 시작·중지·등록 (portal.py 와 같은 환경변수·BIND·GPU) ──────────────────
def start(t, extra_env=None):
    if is_remote(t):
        return   # 다른 컨테이너의 도구 — 그쪽이 띄움
    cwd = os.path.join(BASE, t["dir"])
    env = {**os.environ, "PORT": str(t["port"]), "WORKSPACE": os.path.join(config.DATA, t["dir"]), "BIND": config.TOOL_BIND, **(extra_env or {})}   # + 작업 알림(PORTAL_JOBS_*, jobs.tool_env)
    env.pop("PORTAL_JOBS_SECRET", None)   # 포털 비밀값은 도구로 안 넘김(도구는 자기 토큰만)
    if t.get("gpus"):
        env["CUDA_VISIBLE_DEVICES"] = str(t["gpus"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        cmd = ["bash", "setup.sh"]
    else:
        py = os.path.join(cwd, "venv", "bin", "python")
        cmd = [py if os.path.exists(py) else config.TOOL_PY, "app.py"]
    env.pop("VIRTUAL_ENV", None)   # 새 포털은 자기 venv 에서 돈다 — 도구가 그 venv 를 물려받지 않게
    subprocess.Popen(cmd, cwd=cwd, env=env, stdout=open(os.path.join(cwd, "server.log"), "ab"), stderr=subprocess.STDOUT, start_new_session=True)


def port_pids(port):
    inodes = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f).read().splitlines()[1:]:
                c = line.split()
                if c[3] == "0A" and int(c[1].rsplit(":", 1)[1], 16) == port: inodes.add(c[9])
        except OSError:
            pass
    pids = set()
    for p in os.listdir("/proc"):
        if not p.isdigit() or int(p) == os.getpid(): continue
        try:
            for fd in os.listdir(f"/proc/{p}/fd"):
                if os.readlink(f"/proc/{p}/fd/{fd}")[8:-1] in inodes: pids.add(int(p)); break
        except OSError:
            pass
    return pids


def stop(t):
    if is_remote(t):
        return
    cwd = os.path.join(BASE, t["dir"])
    if os.path.exists(os.path.join(cwd, "setup.sh")):
        subprocess.run(["bash", "setup.sh", "stop"], cwd=cwd, capture_output=True)
    for pid in port_pids(t["port"]):
        try:
            os.kill(pid, 15)
        except OSError:
            pass


_ADD_LOCK = threading.Lock()


def add_tool(t):
    d = t.get("dir", "")
    if not d.replace("-", "").replace("_", "").isalnum():
        raise ValueError("폴더 이름은 영문·숫자·-_ 만")
    with _ADD_LOCK:
        ts = tools()
        if tool(d):
            raise ValueError("이미 있는 도구")
        port = int(t.get("port") or 0)
        if not (1024 < port < 65536) or any(x["port"] == port for x in ts):
            raise ValueError("포트가 비어 있지 않거나 범위 밖")
        t = {k: re.sub(r"[<>\"'`\\]", "", str(v))[:200] for k, v in t.items()}
        row = {"dir": d, "port": port, "name": t.get("name") or d, "title": t.get("title") or d, "desc": t.get("desc", ""), "group": t.get("group") or "기타",
               "tags": [x for x in (t.get("tags") or "").replace("#", "").split() if x], "dept": t.get("dept", ""), "author": t.get("author", ""),
               "added": datetime.date.today().isoformat()}
        row["tags"] = ["#" + x for x in row["tags"]]
        ts.append(row)
        json.dump(ts, open(config.TOOLS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return row


def landing_groups():
    order, g = [], {}
    for t in tools():
        if t.get("hidden"):
            continue
        k = t.get("group") or "기타"
        if k not in g:
            order.append(k); g[k] = []
        g[k].append(t.get("name") or t["dir"])
    return sorted(((k, g[k]) for k in order), key=lambda kv: -len(kv[1]))


def guide(d):
    p = os.path.join(config.ROOT, "guides", f"{d}.md")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else f"## {d}\n\n사용법이 아직 없습니다. `portal/guides/{d}.md` 를 만들면 여기에 뜹니다."


# ── 홈 목록 데이터(/api/tools): 사용 횟수·이용 순위·게시글 ─────────────────
def usage(c):
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    out = {}
    for r in c.execute("SELECT dir, SUM(kind='open') total, SUM(kind='open' AND ts>=?) recent, "
                       "COUNT(DISTINCT CASE WHEN kind='open' THEN user END) users, SUM(kind='like') likes FROM events GROUP BY dir", (cut,)):
        out[r["dir"]] = {k: r[k] or 0 for k in ("recent", "total", "users", "likes")}
    return out


def user_rank(c):
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    return [{"user": r["name"], "n": r["n"]} for r in
            c.execute("SELECT u.name, COUNT(*) n FROM events e JOIN users u ON u.id=e.user WHERE e.kind='open' AND e.ts>=? GROUP BY e.user ORDER BY n DESC LIMIT 5", (cut,))]


def posts(c, user):
    return [dict(r) | {"tags": json.loads(r["tags"] or "[]"), "liked": bool(r["liked"])} for r in
            c.execute("SELECT p.*, (SELECT COUNT(*) FROM post_likes WHERE post=p.id) likes, "
                      "(SELECT COUNT(*) FROM post_likes WHERE post=p.id AND user=?) liked FROM posts p ORDER BY id DESC LIMIT 50", (user,))]


def tool_list(c, up, uid):
    """/api/tools 의 tools 목록(켜짐·설치·사용 횟수 포함)"""
    u = usage(c)
    return [{**t, "up": up.get(tool_addr(t), False), "installed": is_remote(t) or os.path.isdir(os.path.join(BASE, t["dir"])),
             "url": f"/t/{t['dir']}/", "usage": u.get(t["dir"], {"recent": 0, "total": 0, "users": 0, "likes": 0})}
            for t in tools() if config.SHOW_HIDDEN or not t.get("hidden")]


# ── GPU 현황 (5초 캐시, 동시에 여러 탭이 불러도 nvidia-smi 는 한 번) ─────────
_GPU = {"t": 0, "v": None}
_GPU_LOCK = threading.Lock()


def _smi(q, kind):
    r = subprocess.run(["nvidia-smi", f"--query-{kind}={q}", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    return [[x.strip() for x in l.split(",")] for l in r.stdout.splitlines() if l.strip()]


def _owner_of(pid):
    nm = {t["dir"]: t.get("name") or t["dir"] for t in tools()}
    for _ in range(6):
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            return None
        if "ollama" in cwd:
            return "LLM (Ollama)"
        if "vllm" in cwd:   # ~/.local/vllm/start.sh 가 cd 해서 띄움
            return "LLM (vLLM)"
        if cwd.startswith(BASE + os.sep):
            d = cwd[len(BASE) + 1:].split(os.sep)[0]
            if d in nm:
                return nm[d]
        try:
            pid = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
        if pid <= 1:
            return None


def _local_gpu_procs():
    out = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            g = {int(m) for m in re.findall(r"/dev/nvidia(\d+)", open(f"/proc/{p}/maps").read())}
            if g:
                out.append((int(open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()[19]), int(p), g))
        except (OSError, ValueError, IndexError):
            pass
    return sorted(out)


def llm_queue():
    """vLLM /metrics(Prometheus 글자)에서 LLM 대기열: 처리 중·대기 요청 수, KV 캐시 사용률(0~1). LLM_API=openai 일 때만, 못 읽으면 ok=False."""
    if os.environ.get("LLM_API", "ollama") != "openai":
        return None
    base = (os.environ.get("LLM_BASE_URL") or "").rstrip("/")
    base = base[:-3] if base.endswith("/v1") else base
    out = {"ok": False, "model": os.environ.get("LLM_MODEL") or None, "running": None, "waiting": None, "kv_cache": None}
    try:
        with urllib.request.urlopen(base + "/metrics", timeout=2) as r:
            text = r.read().decode(errors="replace")
    except (OSError, ValueError):
        return out
    m = {}
    for line in text.splitlines():
        if line.startswith("vllm:") and "{" in line:
            k, _, v = line.partition("{")
            try:
                m[k] = m.get(k, 0.0) + float(v.rsplit("}", 1)[1])   # 엔진(데이터 병렬)이 여럿이면 더함
            except (ValueError, IndexError):
                pass
    kv = m.get("vllm:kv_cache_usage_perc", m.get("vllm:gpu_cache_usage_perc"))   # 이름은 perc 지만 값은 0~1
    if "vllm:num_requests_running" in m:
        out.update(ok=True, running=int(m["vllm:num_requests_running"]), waiting=int(m.get("vllm:num_requests_waiting", 0)),
                   kv_cache=round(kv, 4) if kv is not None else None)
    return out


def gpu_status():
    with _GPU_LOCK:
        if time.time() - _GPU["t"] < 5 and _GPU["v"]:
            return _GPU["v"]
        try:
            gpus = [{"index": int(i), "uuid": u, "name": n, "used": int(mu), "total": int(mt), "util": int(ut) if ut.isdigit() else None, "procs": []}
                    for i, u, n, mu, mt, ut in _smi("index,uuid,name,memory.used,memory.total,utilization.gpu", "gpu")]
            apps = _smi("gpu_uuid,pid,used_memory", "compute-apps")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {"gpus": [], "error": f"nvidia-smi 실패: {type(e).__name__}"}
        by_uuid = {g["uuid"]: g for g in gpus}
        host = {}
        for u, pid, mem in apps:
            if u in by_uuid and pid.isdigit():
                host.setdefault(int(pid), {})[by_uuid[u]["index"]] = int(mem) if mem.isdigit() else 0
        local, li, who = _local_gpu_procs(), 0, {}
        for hp in sorted(host):
            for j in range(li, len(local)):
                if set(host[hp]) <= local[j][2]:
                    who[hp], li = _owner_of(local[j][1]), j + 1
                    break
        for hp, per in host.items():
            for gi, mem in per.items():
                g = next(g for g in gpus if g["index"] == gi)
                name = who.get(hp) or "기타 (이 서버 밖·알 수 없음)"
                hit = next((p for p in g["procs"] if p["tool"] == name), None)
                if hit:
                    hit["mem"] += mem
                else:
                    g["procs"].append({"tool": name, "mem": mem})
        for g in gpus:
            g.pop("uuid"); g["procs"].sort(key=lambda p: -p["mem"])
        _GPU.update(t=time.time(), v={"gpus": gpus, "t": int(time.time()), "llm": llm_queue()})
        return _GPU["v"]
