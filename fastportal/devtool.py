"""승인된 개발자 업로드 도구의 격리 실행(컨테이너) — tools.json 항목에 "runtime": "container" 가 있는 도구는 tools.start·stop 이 여기로 보냄.
호스트 도구(setup.sh·app.py 를 포털 계정으로 실행)는 그대로. 승인 흐름(이미지 빌드 → 기동 → /healthz → 등록)은 devsubmit.py.

  이미지: dongjukim-tool-<dir>:<제출 id> = FROM 바탕 이미지(BASE_IMAGE, sdk/devtool/Dockerfile) + /app 에 도구 폴더 + requirements.txt 설치.
          설치는 바퀴(wheel)만(--only-binary) — 빌드 때 제출 코드(setup.py)를 실행하지 않음. 옵션 줄(-i·--extra-index-url·-e·URL)은 거절.
          PORTAL_PIP_INDEX_URL 이 있으면 그 내부 저장소에서만 받음. requirements.txt 가 없으면 빌드에 네트워크도 안 씀.
  컨테이너: dongjukim-tool-<dir> — 내부 전용 망 NET(인터넷·호스트로 나가는 길 없음, 컨테이너끼리도 막음 enable_icc=false),
          주소는 포트에서 정함(ip_for — 포털이 http://<ip>:<port> 로 붙음, 호스트에 포트를 열지 않음), GPU 없음,
          CPU·메모리·프로세스 수 상한, 읽기 전용 루트 + /tmp(tmpfs), 권한 모두 뺌, no-new-privileges, 포털 계정 uid 로 실행.
          데이터는 _data/<dir> 만 /data 로 붙임(WORKSPACE=/data — 사용자별 u/<id> 그대로). 비밀값은 --env-file(0600, 바로 지움)로만:
          도구 전용 서명 키 PORTAL_TOOL_KEY(jobs.key_for — 공용 키가 아님, 다른 도구 앞 서명을 못 만듦). 작업 알림 토큰은 안 넘김(포털에 못 닿음).
  LLM·임베딩: 컨테이너 안 entry.py 가 127.0.0.1:11440·11436 을 열고, 포털이 컨테이너 :19742 로 역방향 연결을 몇 개 열어 둠(tunnel_loop).
          받은 연결은 LLM_ADDR·EMB_ADDR 한 곳씩에만 잇는다 — 컨테이너가 목적지를 고를 수 없음. 도구당 동시 연결 BUSY_MAX.
환경변수: DEVCHECK_DOCKER_HOST(tcp://127.0.0.1:2375) DEVCHECK_IMAGE(바탕 이미지) DEVTOOL_NET DEVTOOL_SUBNET(172.29.243.0/24)
          DEVTOOL_CPUS(2) DEVTOOL_MEM(2g) DEVTOOL_PIDS(256) DEVTOOL_BUILD_TIMEOUT(900) DEVTOOL_LLM_ADDR(127.0.0.1:11440) DEVTOOL_EMB_ADDR(127.0.0.1:11436)"""
import asyncio, ipaddress, json, os, re, shutil, subprocess, tempfile, time, urllib.request

from . import config

BASE_TAG = "v1"   # 바탕 이미지 판 — scripts/build-devcheck-image.sh 가 이 값을 읽음
DOCKER_HOST = os.environ.get("DEVCHECK_DOCKER_HOST", "tcp://127.0.0.1:2375")
BASE_IMAGE = os.environ.get("DEVCHECK_IMAGE", f"dongjukim-devcheck:{BASE_TAG}")
NET = os.environ.get("DEVTOOL_NET", "dongjukim-devtools-net")
SUBNET = ipaddress.ip_network(os.environ.get("DEVTOOL_SUBNET", "172.29.243.0/24"))
CPUS, MEM, PIDS = os.environ.get("DEVTOOL_CPUS", "2"), os.environ.get("DEVTOOL_MEM", "2g"), os.environ.get("DEVTOOL_PIDS", "256")
BUILD_TIMEOUT = int(os.environ.get("DEVTOOL_BUILD_TIMEOUT", "900"))
TUNNEL_PORT = 19742
DIR_RE = re.compile(r"[a-z][a-z0-9-]{1,38}-local")
SDK_AUTH = os.path.join(config.ROOT, "sdk", "portal_auth.py")
# 도구로 넘기는 LLM·임베딩 설정(이름만 — 값은 포털 환경변수, 주소는 컨테이너 안 중계로 바꿈)
PASS_ENV = ("LLM_MODEL", "MODEL", "VISION_MODEL", "NUM_CTX", "EMBED_MODEL")


def _addr(v, default):
    h, _, p = (v or default).rpartition(":")
    return h, int(p)


TARGETS = {"llm": _addr(os.environ.get("DEVTOOL_LLM_ADDR"), "127.0.0.1:11440"), "emb": _addr(os.environ.get("DEVTOOL_EMB_ADDR"), "127.0.0.1:11436")}
IDLE_N = {"llm": 4, "emb": 2}   # 미리 열어 둘 빈 연결 수
BUSY_MAX = 8                    # 도구 하나·종류 하나의 동시 연결 상한(빈 것 포함)


def docker(args, timeout=60, **kw):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, "DOCKER_HOST": DOCKER_HOST}, **kw)


def is_container(t):
    return (t or {}).get("runtime") == "container"


def _dir(d):
    if not isinstance(d, str) or not DIR_RE.fullmatch(d):   # 컨테이너·이미지 이름에 그대로 들어감 — 다른 사람 자원과 섞이지 않게
        raise ValueError(f"도구 이름 형식이 틀립니다: {str(d)[:40]}")
    return d


def container_name(d):
    return f"dongjukim-tool-{_dir(d)}"


def image_repo(d):
    return f"dongjukim-tool-{_dir(d)}"


def ip_for(t):
    """포트로 정하는 컨테이너 주소(포트가 tools.json 에서 겹치지 않으니 주소도 안 겹침). 망이 /24 보다 작거나 형식이 틀리면 None"""
    try:
        port = int(t["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if SUBNET.num_addresses < 256:
        return None
    return str(SUBNET[2 + port % 250])


# ── 망 ──────────────────────────────────────────────────────────────────
def ensure_net():
    """내부 전용 망이 없으면 만들고, 있으면 설정(internal·컨테이너끼리 막음·주소 대역)을 확인. 다르면 RuntimeError(띄우지 않음)"""
    fmt = '{{.Internal}} {{index .Options "com.docker.network.bridge.enable_icc"}} {{range .IPAM.Config}}{{.Subnet}}{{end}}'
    r = docker(["network", "inspect", "--format", fmt, NET], 20)
    if r.returncode != 0:
        c = docker(["network", "create", "--internal", "--subnet", str(SUBNET), "-o", "com.docker.network.bridge.enable_icc=false",
                    "--label", "dongjukim.devtool=net", NET], 30)
        if c.returncode != 0:
            raise RuntimeError(f"격리 망 {NET} 을 만들지 못했습니다: {c.stderr.strip()[-200:]}")
        r = docker(["network", "inspect", "--format", fmt, NET], 20)
    if r.stdout.split() != ["true", "false", str(SUBNET)]:
        raise RuntimeError(f"격리 망 {NET} 설정이 다릅니다(internal·enable_icc=false·{SUBNET} 이어야 함): {r.stdout.strip()[:120]}")


# ── 이미지 ──────────────────────────────────────────────────────────────
_SPEC = r"(?:===?|~=|!=|<=?|>=?)\s*[A-Za-z0-9.*+!_-]+"
REQ_LINE = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._ -]*\])?\s*(?:{_SPEC}\s*(?:,\s*{_SPEC}\s*)*)?(?:;[^#]*)?")


def clean_requirements(text):
    """requirements.txt → (설치할 줄, 받지 않는 줄). 이름·버전 조건·환경 표시만 받음(옵션·URL·경로·-r·-e 는 거절)"""
    ok, bad = [], []
    for raw in text.splitlines():
        line = re.split(r"(?:^|\s)#", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        (ok if REQ_LINE.fullmatch(line) else bad).append(line)
    return ok, bad


def build(d, src, ctx_dir, tag, index_url=""):
    """도구 이미지 만들기 → (이미지 이름, 로그 끝). 실패면 RuntimeError(로그 끝 포함). ctx_dir 은 비어 있는 새 폴더(호출한 쪽이 지움)"""
    image = f"{image_repo(d)}:{tag}"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", tag):
        raise ValueError("이미지 태그 형식이 틀립니다")
    app = os.path.join(ctx_dir, "app")
    shutil.copytree(src, app, symlinks=False)
    if not os.path.exists(os.path.join(app, "portal_auth.py")) and os.path.exists(SDK_AUTH):
        shutil.copy2(SDK_AUTH, os.path.join(app, "portal_auth.py"))
    for root, dirs, fs in os.walk(app):
        os.chmod(root, 0o755)
        for f in fs:
            os.chmod(os.path.join(root, f), 0o644)
    reqs = []
    rp = os.path.join(app, "requirements.txt")
    if os.path.exists(rp):
        reqs, bad = clean_requirements(open(rp, encoding="utf-8", errors="replace").read())
        if bad:
            raise RuntimeError("requirements.txt 의 이 줄은 받지 않습니다(이름·버전 조건만): " + "; ".join(b[:60] for b in bad[:5]))
    with open(os.path.join(ctx_dir, "req.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(reqs) + "\n")
    pip = ""
    if reqs:
        idx = f"--index-url {index_url} " if index_url else ""
        pip = f"RUN pip install --no-cache-dir --no-compile --only-binary=:all: {idx}-r /tmp/req.txt\n"
    with open(os.path.join(ctx_dir, "Dockerfile"), "w", encoding="utf-8") as f:
        f.write(f"FROM {BASE_IMAGE}\nUSER root\nCOPY req.txt /tmp/req.txt\n{pip}COPY app/ /app/\nUSER 10001:10001\n")
    args = ["build", "--pull=false", "--label", f"dongjukim.devtool={d}", "-t", image]
    if not reqs:
        args += ["--network", "none"]
    r = docker(args + [ctx_dir], BUILD_TIMEOUT, cwd=ctx_dir)
    log = (r.stdout + "\n" + r.stderr)[-6000:]
    if r.returncode != 0:
        raise RuntimeError("이미지 빌드 실패:\n" + log[-1500:])
    return image, log


def image_ok(image):
    return docker(["image", "inspect", "--format", "{{.Id}}", image], 20).returncode == 0


def remove_image(d, image):
    """그 도구의 이미지만 지움(이름이 dongjukim-tool-<dir>: 로 시작할 때)"""
    if image and image.startswith(image_repo(d) + ":"):
        docker(["rmi", "-f", image], 120)


# ── 시작·중지·상태 ──────────────────────────────────────────────────────
def tool_env(t):
    """컨테이너 안 도구 환경변수(비밀값 빼고). LLM·임베딩은 컨테이너 안 중계 주소"""
    env = {"PORT": str(int(t["port"])), "BIND": "0.0.0.0", "WORKSPACE": "/data", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
           "TOOL_VERSION": str(t.get("image", "")).rsplit(":", 1)[-1],
           "LLM_API": "openai", "LLM_BASE_URL": "http://127.0.0.1:11440/v1", "EMBED_API": "ollama", "EMBED_BASE_URL": "http://127.0.0.1:11436"}
    env.update({k: os.environ[k] for k in PASS_ENV if os.environ.get(k)})
    return env


def start(t, extra_env=None):
    """컨테이너를 (다시) 띄움. 이미지·망이 없거나 틀리면 RuntimeError. extra_env 에서는 PORTAL_TOOL_KEY 만 씀"""
    d = t["dir"]
    name, image, ip = container_name(d), t.get("image") or "", ip_for(t)
    if not image.startswith(image_repo(d) + ":"):
        raise RuntimeError(f"{d}: tools.json 의 image 가 {image_repo(d)}:<태그> 형식이 아닙니다")
    if not ip:
        raise RuntimeError(f"{d}: 컨테이너 주소를 정하지 못했습니다(DEVTOOL_SUBNET)")
    if not image_ok(image):
        raise RuntimeError(f"{d}: 이미지 {image} 가 없습니다")
    ensure_net()
    ws = os.path.join(config.DATA, d)
    os.makedirs(ws, mode=0o700, exist_ok=True)
    docker(["rm", "-f", name], 60)
    env = tool_env(t)
    key = (extra_env or {}).get("PORTAL_TOOL_KEY")
    if key:
        env["PORTAL_TOOL_KEY"] = key
    fd, envf = tempfile.mkstemp(prefix=".devtool-env-", dir=config.SECRETS_DIR)   # 0600 — 명령줄(ps)에 비밀값이 안 보이게
    try:
        with os.fdopen(fd, "w") as f:
            f.write("".join(f"{k}={v}\n" for k, v in env.items() if "\n" not in str(v)))
        r = docker(["run", "-d", "--name", name, "--network", NET, "--ip", ip, "--user", f"{os.getuid()}:{os.getgid()}",
                    "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--init", "--restart", "on-failure:3", "--cpus", CPUS, "--memory", MEM, "--memory-swap", MEM, "--pids-limit", PIDS,
                    "--log-opt", "max-size=10m", "--log-opt", "max-file=2", "--label", f"dongjukim.devtool={d}",
                    "--env-file", envf, "-v", f"{ws}:/data:rw", image], 120)
    finally:
        os.remove(envf)
    if r.returncode != 0:
        raise RuntimeError(f"{d}: 컨테이너 시작 실패: {r.stderr.strip()[-300:]}")


def stop(t):
    name = container_name(t["dir"])
    docker(["stop", "-t", "10", name], 40)
    docker(["rm", "-f", name], 40)


def state(d):
    """컨테이너 상태 글자(running·exited·restarting …) — 없으면 '없음'"""
    r = docker(["container", "inspect", "--format", "{{.State.Status}}", container_name(d)], 20)
    return r.stdout.strip() if r.returncode == 0 else "없음"


def logs(d, n=60):
    r = docker(["logs", "--tail", str(n), container_name(d)], 20)
    return (r.stdout + r.stderr)[-4000:]


def wait_health(t, timeout=60):
    """http://<ip>:<port>/healthz 가 200 + ok 가 될 때까지 → (성공, 마지막 내용)"""
    url, last, t0 = f"http://{ip_for(t)}:{int(t['port'])}/healthz", "", time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                body = r.read(4096).decode("utf-8", "replace")
                try:
                    if r.status == 200 and json.loads(body).get("ok"):
                        return True, body[:300]
                except ValueError:
                    pass
                last = f"HTTP {r.status} {body[:200]}"
        except Exception as e:
            last = type(e).__name__
        if state(t["dir"]) in ("exited", "dead", "없음"):
            return False, f"컨테이너가 멈췄습니다({last})"
        time.sleep(1)
    return False, f"{timeout}초 안에 /healthz 응답 없음({last})"


# ── LLM·임베딩 역방향 연결(포털 이벤트 루프에서) ─────────────────────────
STATS = {}   # (dir, 종류) → {"idle": 빈 연결(연결 시도 포함), "busy": 쓰는 중}


async def _pump(r, w):
    try:
        while True:
            b = await r.read(65536)
            if not b:
                break
            w.write(b)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def _tunnel(d, ip, kind):
    st = STATS[(d, kind)]
    busy, w, tw = False, None, None
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection(ip, TUNNEL_PORT), 3)
        w.write(kind.encode() + b"\n")
        await w.drain()
        first = await r.read(65536)   # 도구가 이 연결을 쓸 때까지 기다림
        if not first:
            return
        st["idle"] -= 1; st["busy"] += 1; busy = True
        _fill(d, ip, kind)   # 빈 연결 하나를 바로 채움
        tr, tw = await asyncio.wait_for(asyncio.open_connection(*TARGETS[kind]), 5)
        tw.write(first)
        await tw.drain()
        await asyncio.gather(_pump(r, tw), _pump(tr, w))
    except (OSError, asyncio.TimeoutError):
        if not busy:
            await asyncio.sleep(3)   # 컨테이너가 꺼짐·다시 뜨는 중 — 잠깐 쉬고(빈 자리를 잡은 채) 다시
    finally:
        for x in (w, tw):
            if x:
                try:
                    x.close()
                except Exception:
                    pass
        if busy:
            st["busy"] -= 1
        else:
            st["idle"] -= 1


def _fill(d, ip, kind):
    st = STATS.setdefault((d, kind), {"idle": 0, "busy": 0})
    while st["idle"] < IDLE_N[kind] and st["idle"] + st["busy"] < BUSY_MAX:
        st["idle"] += 1
        asyncio.get_running_loop().create_task(_tunnel(d, ip, kind))


async def tunnel_loop():
    """tools.json 의 컨테이너 도구마다 빈 역방향 연결을 채워 둠(2초마다). 등록이 빠진 도구는 더 채우지 않음(남은 연결은 컨테이너와 함께 닫힘)"""
    from . import tools
    while True:
        try:
            for t in tools.tools():
                ip = ip_for(t) if is_container(t) else None
                if ip and DIR_RE.fullmatch(str(t.get("dir"))):
                    for kind in TARGETS:
                        _fill(t["dir"], ip, kind)
        except Exception as e:
            print("devtool: 역방향 연결 오류", type(e).__name__, e, flush=True)
        await asyncio.sleep(2)
