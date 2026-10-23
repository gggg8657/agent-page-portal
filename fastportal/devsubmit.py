"""모두의 에이전트 (C) 개발자 업로드 — 개발자 권한자가 도구 폴더(zip)를 올리면 형식 검사 → 격리 컨테이너 자가검증 → 관리자 승인 → 포털 카드 등록.

  권한: '개발자' 역할(developers 표) — 관리자만 부여·회수(감사 기록). 업로드 API 는 개발자만, 승인·거절은 관리자만. 개발자는 자기 제출만 봄.
  업로드: POST /api/dev/submit (본문 = zip 바이트, 상한 ZIP_MAX) — zip slip·절대 경로·심볼릭 링크·장치 파일 거절, 파일 수·풀린 크기 상한.
          _data/dev-submissions/<id>/src/<dir> 에 풀어 둠(폴더 0700).
  형식 검사(서버): <이름>-local 폴더 하나, app.py 또는 setup.sh, README.md, agent.json(title·desc·group·usage), 이름 충돌 없음,
          BIND 0.0.0.0 고정 금지, PORT 환경변수 사용, portal_auth 권장(경고), 위험 패턴(shell=True·eval/exec·pickle.load·os.system·외부 URL) 경고.
          폐쇄망 pip 목록(pkgindex.scan_tree): requirements*.txt·pyproject.toml·setup.sh 의 pip install — 목록에 없음·없는 버전·git/URL/경로 설치는 경고.
  격리 자가검증: Docker(DEVCHECK_DOCKER_HOST, 기본 tcp://127.0.0.1:2375) — 이름 dongjukim-devcheck-<id>, GPU 없음, --network none,
          CPU·메모리·프로세스 수 상한, 읽기 전용 루트 + /tmp(tmpfs), 권한 모두 뺌, 시간 제한. 안에서: 문법 검사(compile), selftest.py, 서버 띄워 /healthz.
          이미지(DEVCHECK_IMAGE = 바탕 이미지 dongjukim-devcheck:<판>, scripts/build-devcheck-image.sh)·Docker 가 없으면 '건너뜀'으로 표시하고 관리자에게 알림.
  승인 잠금: PORTAL_DEV_APPROVE=0(기본) 이면 승인(ok=true)은 409. 거절·회수는 그대로. 푸는 법은 start-portal.sh 에 PORTAL_DEV_APPROVE=1.
  본문 전 권한 확인: app.py 가 precheck() 로 로그인·개발자·검사 중 제출을 먼저 보고, 아니면 본문(zip)을 읽지 않고 돌려보냄.
  승인(격리 실행, devtool.py — 호스트에서 setup.sh·app.py 를 실행하지 않음): 상태 installing → 뒤에서
          이미지 빌드(dongjukim-tool-<dir>:<id>) → 컨테이너 기동(dongjukim-tool-<dir>, 도구 전용 서명 키) → /healthz → tools.json 등록("runtime": "container")
          → guides/<dir>.md. 단계마다 감사 기록. 실패하면 거꾸로 되돌리고(등록·사용법·컨테이너·이미지) 상태를 review 로, 이유·단계·로그를 install 에.
          폐쇄망 pip 목록 경고(설치 불가·버전 없음)가 있으면 관리자 확인(confirm_pkgs) 없이는 승인 안 됨.
  회수: POST /api/admin/dev/revoke — 컨테이너·이미지·tools.json 줄·승인 때 만든 사용법을 지움(사용자 데이터 _data/<dir> 는 남김). 감사 기록.
  개발자 신청: 일반 계정이 POST /api/dev/apply {reason} → 관리자 알림함 + 신청 목록(GET /api/admin/dev/applications) → 부여·보류(POST 같은 주소).
  샘플·형식 안내: GET /api/dev/sample.zip(sdk/samples/hello-local + portal_auth.py + FORMAT.md) · GET /api/dev/format(형식 안내 마크다운)."""
import datetime, io, json, os, re, secrets, shutil, socket, stat, subprocess, threading, time, zipfile

from . import accounts, config, devtool, jobs, pkgindex, timeutil, tools
from .db import db, now, own_row

PKG_LIST_MAX = 8 * 1024 * 1024   # 폐쇄망 패키지 목록 파일(pip index 출력 포함) 상한
ZIP_MAX = int(os.environ.get("DEVSUBMIT_ZIP_MAX", str(50 * 1024 * 1024)))
FILES_MAX = 2000
UNZIP_MAX = 200 * 1024 * 1024
FILE_MAX = 30 * 1024 * 1024
SUB_DIR = os.path.join(config.DATA, "dev-submissions")
IMAGE = devtool.BASE_IMAGE   # 바탕 이미지 dongjukim-devcheck:<판>(DEVCHECK_IMAGE) — 승인된 도구 이미지도 이 위에 만듦
IMAGE_PY = os.environ.get("DEVCHECK_PYTHON", "/usr/local/bin/python3")
CHECK_TIMEOUT = int(os.environ.get("DEVCHECK_TIMEOUT", "180"))
PORT_RANGE = (8810, 8899)
NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,38}-local")
JOB_TOOL = "devsubmit"
CHECK_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devcheck_runner.py")
SDK_AUTH = os.path.join(config.ROOT, "sdk", "portal_auth.py")
SAMPLE_DIR = os.path.join(config.ROOT, "sdk", "samples", "hello-local")
FORMAT_DOC = os.path.join(config.ROOT, "sdk", "samples", "FORMAT.md")
HEALTH_WAIT = int(os.environ.get("DEVTOOL_HEALTH_WAIT", "60"))
PKG_BLOCK = ("폐쇄망에서 설치 불가", "버전 없음")   # 이 경고가 있으면 승인에 관리자 확인(confirm_pkgs) 필요
GET_PATHS = {"/api/dev/submissions", "/api/dev/apply", "/api/dev/sample.zip", "/api/dev/format", "/api/admin/dev/applications"}
POST_PATHS = {"/api/admin/dev/decide", "/api/admin/dev/revoke", "/api/admin/developer", "/api/admin/dev/applications", "/api/dev/apply"}


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS developers(uid TEXT PRIMARY KEY, granted_by TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS dev_submissions(id TEXT PRIMARY KEY, uid TEXT NOT NULL, agent_user TEXT NOT NULL, dir TEXT, title TEXT,
        status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, size INTEGER, nfiles INTEGER,
        checks TEXT, sandbox TEXT, note TEXT, decided_by TEXT, decided_ts TEXT, port INTEGER);
    CREATE INDEX IF NOT EXISTS dev_sub_uid ON dev_submissions(uid, created);
    CREATE TABLE IF NOT EXISTS dev_applications(uid TEXT PRIMARY KEY, reason TEXT, ts TEXT NOT NULL, status TEXT NOT NULL, decided_by TEXT, decided_ts TEXT, note TEXT);""")
    if "install" not in {r[1] for r in c.execute("PRAGMA table_info(dev_submissions)")}:   # 승인 설치 단계·실패 이유(2026-10-23) — 예전 DB 에 붙임
        c.execute("ALTER TABLE dev_submissions ADD COLUMN install TEXT")


# ── 개발자 역할 ──────────────────────────────────────────────────────────
def is_developer(c, uid):
    return bool(uid) and bool(c.execute("SELECT 1 FROM developers WHERE uid=?", (uid,)).fetchone())


def developer_ids(c):
    return {r[0] for r in c.execute("SELECT uid FROM developers")}


def set_developer(c, actor, uid, on):
    if not c.execute("SELECT 1 FROM users WHERE id=? AND NOT disabled", (uid,)).fetchone():
        raise ValueError("없거나 정지된 계정입니다")
    if on:
        c.execute("INSERT OR IGNORE INTO developers VALUES(?,?,?)", (uid, actor, now()))
        c.execute("UPDATE dev_applications SET status='granted', decided_by=?, decided_ts=? WHERE uid=? AND status='pending'", (actor, now(), uid))
    else:
        c.execute("DELETE FROM developers WHERE uid=?", (uid,))
    accounts.audit(c, actor, "developer_grant" if on else "developer_revoke", uid)


# ── zip 받기 ─────────────────────────────────────────────────────────────
def _unpack(data, dest):
    """zip 검사 후 dest 에 풀기 → (최상위 폴더 이름, 파일 목록[(경로, 크기)]). 문제가 있으면 ValueError(아무것도 남기지 않음)"""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("zip 파일이 아닙니다")
    infos = z.infolist()
    if not infos:
        raise ValueError("빈 zip 입니다")
    if len(infos) > FILES_MAX:
        raise ValueError(f"파일이 너무 많습니다(최대 {FILES_MAX}개)")
    total, tops, files = 0, set(), []
    for i in infos:
        n = i.filename
        if "\\" in n or n.startswith("/") or re.match(r"^[A-Za-z]:", n) or "\x00" in n:
            raise ValueError(f"허용하지 않는 경로: {n[:80]}")
        parts = [x for x in n.split("/") if x]
        if not parts or any(x in (".", "..") for x in parts):
            raise ValueError(f"허용하지 않는 경로(..): {n[:80]}")
        mode = (i.external_attr >> 16) & 0xFFFF
        fmt = stat.S_IFMT(mode)   # 형식 비트가 없는 zip(윈도·파이썬 writestr)은 일반 파일로 봄
        if fmt and fmt not in (stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"심볼릭 링크·특수 파일은 받지 않습니다: {n[:80]}")
        if i.flag_bits & 0x1:
            raise ValueError("암호 걸린 zip 은 받지 않습니다")
        total += i.file_size
        if i.file_size > FILE_MAX or total > UNZIP_MAX:
            raise ValueError(f"풀린 크기가 너무 큽니다(파일 하나 {FILE_MAX >> 20}MB, 합계 {UNZIP_MAX >> 20}MB)")
        tops.add(parts[0])
        if not i.is_dir():
            if len(parts) == 1:
                raise ValueError("zip 안에 도구 폴더(<이름>-local) 하나를 넣어 주세요 — 파일이 맨 위에 있습니다")
            files.append(("/".join(parts[1:]), i.file_size))
    if len(tops) != 1:
        raise ValueError("zip 안에 도구 폴더(<이름>-local) 하나만 넣어 주세요")
    top = tops.pop()
    root = os.path.realpath(dest)
    for i in infos:
        parts = [x for x in i.filename.split("/") if x]
        target = os.path.realpath(os.path.join(root, *parts))
        if not target.startswith(root + os.sep):
            raise ValueError("허용하지 않는 경로")
        if i.is_dir():
            os.makedirs(target, mode=0o700, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        with z.open(i) as src, open(target, "xb") as out:   # x: 이미 있으면(중복 항목) 실패
            n = 0
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                n += len(b)
                if n > FILE_MAX:   # 머리말 크기를 속인 경우
                    raise ValueError("풀린 크기가 머리말과 다릅니다")
                out.write(b)
        os.chmod(target, 0o600)
    return top, sorted(files)


# ── 형식 검사 ────────────────────────────────────────────────────────────
DANGER = [(re.compile(r"shell\s*=\s*True"), "subprocess shell=True"), (re.compile(r"(?<![\w.])eval\s*\("), "eval()"),
          (re.compile(r"(?<![\w.])exec\s*\("), "exec()"), (re.compile(r"pickle\.loads?\s*\("), "pickle.load"),
          (re.compile(r"os\.system\s*\("), "os.system"), (re.compile(r"yaml\.load\s*\((?![^)]*SafeLoader)"), "yaml.load(안전 로더 아님)"),
          (re.compile(r"__import__\s*\("), "__import__()")]
URL_RE = re.compile(r"https?://(?!127\.0\.0\.1|localhost|0\.0\.0\.0|\[::1\])[A-Za-z0-9.-]+")
BIND_RE = re.compile(r"""(?:["']0\.0\.0\.0["']|--host\s+0\.0\.0\.0|BIND\s*=\s*["']?0\.0\.0\.0)""")
GROUPS_EXTRA = {"기타"}


def check_tree(c, sid, top, path, files):
    """형식 검사 → {errors, warnings, info, meta}"""
    err, warn, info = [], [], []
    names = {f for f, _ in files}
    if not NAME_RE.fullmatch(top):
        err.append(f"폴더 이름은 영문 소문자로 시작하는 <이름>-local 형식이어야 합니다(지금 {top[:40]})")
    if tools.tool(top) or os.path.exists(os.path.join(config.BASE, top)):
        err.append(f"이미 있는 도구 이름입니다: {top}")
    if c.execute("SELECT 1 FROM dev_submissions WHERE dir=? AND id<>? AND status IN ('checking','sandbox','review')", (top, sid)).fetchone():
        err.append(f"같은 이름({top})의 다른 제출이 승인 대기 중입니다")
    if "app.py" not in names:
        err.append("app.py 가 필요합니다 — 승인된 도구는 격리 컨테이너에서 python app.py 로 뜹니다")
    if "setup.sh" in names:
        info.append("setup.sh 는 실행하지 않습니다 — 의존성은 requirements.txt 로(승인 때 이미지에 바퀴(wheel)만 설치)")
    if "requirements.txt" in names:
        _, bad = devtool.clean_requirements(open(os.path.join(path, "requirements.txt"), encoding="utf-8", errors="replace").read(1024 * 1024))
        if bad:
            err.append("requirements.txt 에는 이름·버전 조건만 적어 주세요(옵션·URL·경로·-r·-e 는 받지 않음): " + "; ".join(b[:60] for b in bad[:3]))
    if "README.md" not in names:
        err.append("README.md 가 필요합니다")
    meta = {}
    if "agent.json" not in names:
        err.append("agent.json(포털 등록 정보: title·desc·group·usage)이 필요합니다")
    else:
        try:
            meta = json.load(open(os.path.join(path, "agent.json"), encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError
        except (ValueError, OSError):
            err.append("agent.json 이 올바른 JSON 객체가 아닙니다"); meta = {}
    for k, lab in (("title", "제목"), ("desc", "설명"), ("group", "카테고리")):
        if meta and not str(meta.get(k) or "").strip():
            err.append(f"agent.json 에 {lab}({k})이 없습니다")
    usage = str(meta.get("usage") or "")
    if not usage.strip() and "guide.md" in names:
        usage = open(os.path.join(path, "guide.md"), encoding="utf-8", errors="replace").read()
    if meta and len(usage.strip()) < 30:
        err.append("사용법이 필요합니다 — agent.json 의 usage 또는 guide.md (30자 이상, 포털 '사용법(?)'에 그대로 뜸)")
    groups = {t.get("group") for t in tools.tools()} | GROUPS_EXTRA
    if meta.get("group") and meta["group"] not in groups:
        warn.append(f"카테고리 '{str(meta['group'])[:20]}' 는 지금 없는 분류입니다 — 승인하면 새 분류로 보임(있는 것: {', '.join(sorted(g for g in groups if g))})")
    if "venv" in {f.split("/")[0] for f in names} or any(f.startswith("node_modules/") for f in names):
        err.append("venv·node_modules 는 빼고 올려 주세요(의존성은 requirements.txt 로)")
    if any(f.split("/")[0] == ".git" for f in names):
        warn.append(".git 폴더가 들어 있습니다(설치 때 그대로 복사됨)")
    srcs = [f for f in names if f.endswith((".py", ".sh", ".js", ".mjs", ".ts", ".html"))]
    uses_port = uses_auth = False
    for f in srcs:
        try:
            text = open(os.path.join(path, f), encoding="utf-8", errors="replace").read(2 * 1024 * 1024)
        except OSError:
            continue
        if BIND_RE.search(text):
            err.append(f"{f}: 0.0.0.0 으로 고정하지 마세요 — BIND 환경변수(기본 127.0.0.1)를 쓰세요")
        if re.search(r"\bPORT\b", text):
            uses_port = True
        if "portal_auth" in text:
            uses_auth = True
        if f == "portal_auth.py" and os.path.exists(SDK_AUTH) and text == open(SDK_AUTH, encoding="utf-8").read():
            continue   # 포털 sdk 사본 그대로 — 검사 생략
        for rx, lab in DANGER:
            if f.endswith((".py",)) and rx.search(text):
                warn.append(f"{f}: 위험할 수 있는 코드 — {lab}")
        for u in sorted(set(URL_RE.findall(text)))[:3]:
            warn.append(f"{f}: 외부 주소 하드코딩 — {u[:60]} (폐쇄망에서 안 될 수 있음)")
    if not uses_port:
        err.append("PORT 환경변수로 포트를 받아야 합니다(포털이 배정)")
    if not uses_auth:
        warn.append("portal_auth 를 쓰지 않습니다 — 포털 서명 확인 없이 열림(권장: portal_auth.install, 포털 sdk/portal_auth.py)")
    elif "portal_auth.py" not in names:
        info.append("portal_auth.py 가 없어 승인 때 포털 sdk 사본을 넣습니다")
    pw, pi = pkgindex.scan_tree(c, path, names)   # 폐쇄망 pip 목록으로 설치 가능 여부(경고만)
    warn += pw; info += pi
    if "selftest.py" not in names:
        info.append("selftest.py 가 없습니다 — 있으면 격리 검증에서 실행함")
    info.append(f"파일 {len(files)}개 · {sum(s for _, s in files) // 1024}KB")
    m = {"title": str(meta.get("title") or top)[:60], "desc": str(meta.get("desc") or "")[:200], "group": str(meta.get("group") or "기타")[:20],
         "tags": str(meta.get("tags") or "")[:100] if not isinstance(meta.get("tags"), list) else " ".join(map(str, meta["tags"]))[:100],
         "name": str(meta.get("name") or meta.get("title") or top)[:30], "usage": usage[:20000], "dept": str(meta.get("dept") or "")[:40]}
    return {"errors": err, "warnings": warn[:60], "info": info, "meta": m}


# ── 격리 자가검증 ────────────────────────────────────────────────────────
def _docker(args, timeout):
    return devtool.docker(args, timeout)


def sandbox(sid, top, src):
    """격리 컨테이너에서 자가검증 → {status: passed|failed|skipped, steps, log}"""
    name = f"dongjukim-devcheck-{sid.lower().replace('_', '-')}"[:60]
    if not name.startswith("dongjukim-"):   # 컨테이너 이름 규칙 — 다른 사람 컨테이너와 섞이지 않게
        raise RuntimeError("container name")
    if not shutil.which("docker"):
        return {"status": "skipped", "reason": "docker 명령이 없습니다", "steps": [], "log": ""}
    try:
        r = _docker(["image", "inspect", "--format", "{{.Id}}", IMAGE], 20)
    except (subprocess.SubprocessError, OSError) as e:
        return {"status": "skipped", "reason": f"Docker 에 연결하지 못했습니다({type(e).__name__})", "steps": [], "log": ""}
    if r.returncode != 0:
        return {"status": "skipped", "reason": f"검증 이미지 {IMAGE} 가 없거나 Docker 에 연결하지 못했습니다", "steps": [], "log": r.stderr[-500:]}
    sdk = SDK_AUTH if os.path.exists(SDK_AUTH) else None
    args = ["run", "--rm", "--name", name, "--network", "none", "--cpus", "1", "--memory", "1g", "--memory-swap", "1g", "--pids-limit", "256",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=256m", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{src}:/src:ro", "-v", f"{CHECK_SCRIPT}:/check/run.py:ro"] + (["-v", f"{sdk}:/check/sdk/portal_auth.py:ro"] if sdk else []) + \
           ["--label", "dongjukim.devcheck=1", "--entrypoint", IMAGE_PY, IMAGE, "-I", "/check/run.py", "/src", top]
    t0 = time.time()
    try:
        r = _docker(args, CHECK_TIMEOUT)
        out, code = r.stdout, r.returncode
        log = (r.stdout + ("\n" + r.stderr if r.stderr else ""))[-20000:]
    except subprocess.TimeoutExpired:
        try:
            _docker(["rm", "-f", name], 30)
        except Exception:
            pass
        return {"status": "failed", "reason": f"시간 제한 {CHECK_TIMEOUT}초를 넘었습니다", "steps": [], "log": "", "seconds": CHECK_TIMEOUT}
    res = None
    for line in reversed(out.splitlines()):
        if line.startswith("DEVCHECK_RESULT "):
            try:
                res = json.loads(line[len("DEVCHECK_RESULT "):])
            except ValueError:
                pass
            break
    if not isinstance(res, dict):
        return {"status": "failed", "reason": f"검증 스크립트 결과가 없습니다(종료 코드 {code})", "steps": [], "log": log, "seconds": round(time.time() - t0, 1)}
    steps = res.get("steps") or []
    ok = all(s.get("ok") or s.get("skipped") for s in steps)
    return {"status": "passed" if ok else "failed", "reason": "" if ok else next((s.get("name", "") + ": " + s.get("detail", "") for s in steps if not s.get("ok") and not s.get("skipped")), ""),
            "steps": steps, "log": log, "seconds": round(time.time() - t0, 1), "image": IMAGE, "container": name}


def _notify(user, sid, status, title, msg, link=None):
    try:
        j = jobs.upsert(user, JOB_TOOL, status, f"{sid}-{secrets.token_hex(3)}", title[:200], link or f"/create?tab=make&m=dev&sub={sid}", msg[:400])
        if j:
            jobs.HUB.publish("job", j, j["user"])
    except Exception as e:
        print("devsubmit: 알림 실패", type(e).__name__, e, flush=True)


def _update(sid, only_if, **kw):
    """상태가 아직 only_if 일 때만 바꿈(그사이 관리자가 거절했으면 안 바꿈) → 바꿨는지"""
    kw["updated"] = now()
    cols = ", ".join(f"{k}=?" for k in kw)
    with db() as c:
        return c.execute(f"UPDATE dev_submissions SET {cols} WHERE id=? AND status=?",
                         (*[json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in kw.values()], sid, only_if)).rowcount > 0


def _pipeline(sid, agent_user, top, src):
    try:
        res = sandbox(sid, top, src)
    except Exception as e:
        print("devsubmit: 검증 오류", sid, type(e).__name__, e, flush=True)
        res = {"status": "failed", "reason": "검증 중 서버 오류", "steps": [], "log": ""}
    status = "failed" if res["status"] == "failed" else "review"
    if not _update(sid, "sandbox", sandbox=res, status=status):
        return   # 검증 중에 거절됨 — 결과·알림 버림
    lab = {"passed": "통과", "failed": "실패", "skipped": "건너뜀"}[res["status"]]
    _notify(agent_user, sid, "done" if status == "review" else "failed", f"제출 {top}: 격리 검증 {lab}",
            "관리자 승인을 기다립니다" if status == "review" else res.get("reason", ""))
    if status == "review":
        _notify("owner", sid, "done", f"개발자 제출 승인 대기: {top}",
                f"격리 검증 {lab}" + (f" — {res.get('reason')} (관리자 확인 필요)" if res["status"] == "skipped" else ""))


class Locked(Exception):
    """승인 잠금(PORTAL_DEV_APPROVE=0) — 409"""


LOCK_MSG = "실행 격리 준비 중 — 지금은 승인할 수 없습니다"


DAILY_MAX = 20   # 개발자당 하루 제출 수 — 제출마다 zip·푼 파일(최대 약 250MB)이 디스크에 남음(형식 오류 zip 을 계속 올리면 디스크가 참)


def _gate(c, uid):
    """올릴 수 없으면 (상태 코드, code, 문구), 되면 None — 본문 전(precheck)과 후(submit)가 같은 규칙"""
    if not is_developer(c, uid):
        return 403, "forbidden", "개발자 권한이 필요합니다 — 관리자에게 요청하세요"
    if c.execute("SELECT COUNT(*) FROM dev_submissions WHERE uid=? AND status IN ('checking','sandbox')", (uid,)).fetchone()[0]:
        return 400, "bad_request", "검사 중인 제출이 있습니다 — 끝난 뒤 다시 올리세요"
    if c.execute("SELECT COUNT(*) FROM dev_submissions WHERE uid=? AND created>=?", (uid, timeutil.kst_day_start_utc(0))).fetchone()[0] >= DAILY_MAX:   # 하루 = KST 0시부터(저장은 UTC)
        return 429, "rate_limited", f"제출은 하루 {DAILY_MAX}번까지입니다 — 내일 다시 올리세요"
    return None


def precheck(ctx):
    """POST /api/dev/submit 본문을 읽기 전에 — 막히면 응답, 통과면 None.
    (본문 50MB 를 받은 뒤에야 권한을 보면, 로그인한 아무나 큰 본문을 여러 개 동시에 보내 포털 메모리를 쓸 수 있음)"""
    with db() as c:
        g = _gate(c, ctx.uid)
    return ctx.send({"error": g[2], "code": g[1]}, code=g[0]) if g else None


def submit(ctx, data):
    """POST /api/dev/submit — 본문 zip. 개발자만"""
    with db() as c:
        g = _gate(c, ctx.uid)
    if g:
        raise (PermissionError if g[0] == 403 else ValueError)(g[2])
    if len(data) < 22 or data[:2] != b"PK":
        raise ValueError("zip 파일을 올려 주세요")
    sid = secrets.token_urlsafe(9)
    base = os.path.join(SUB_DIR, sid)
    os.makedirs(SUB_DIR, mode=0o700, exist_ok=True)
    os.chmod(SUB_DIR, 0o700)
    os.makedirs(os.path.join(base, "src"), mode=0o700)
    try:
        top, files = _unpack(data, os.path.join(base, "src"))
    except Exception as e:
        shutil.rmtree(base, ignore_errors=True)
        if isinstance(e, zipfile.BadZipFile):   # 풀다가 CRC 오류 — ValueError 가 아니라서 500 이었음
            raise ValueError("zip 파일이 깨졌습니다") from None
        raise
    with open(os.path.join(base, "upload.zip"), "xb") as f:
        f.write(data)
    os.chmod(os.path.join(base, "upload.zip"), 0o600)
    path = os.path.join(base, "src", top)
    with db() as c:   # 먼저 커밋 — 큰 zip 검사(읽기만) 동안 쓰기 잠금을 잡으면 로그인·저장이 모두 기다리다 'database is locked'
        c.execute("INSERT INTO dev_submissions(id, uid, agent_user, dir, title, status, created, updated, size, nfiles) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (sid, ctx.uid, ctx.agent_user, top, top, "checking", now(), now(), len(data), len(files)))
    try:
        with db() as c:
            chk = check_tree(c, sid, top, path, files)
            st = "failed" if chk["errors"] else "sandbox"
            if not c.execute("UPDATE dev_submissions SET checks=?, title=?, status=? WHERE id=? AND status='checking'",   # 검사 중에 관리자가 거절했으면 그대로
                             (json.dumps(chk, ensure_ascii=False), chk["meta"]["title"], st, sid)).rowcount:
                st = None
            accounts.audit(c, ctx.uid, "dev_submit", top, f"#{sid} {len(data) // 1024}KB 파일 {len(files)}개")
    except Exception:
        with db() as c:   # 'checking' 으로 남으면 그 개발자는 다시 못 올림(예전 한 트랜잭션일 때의 rollback 과 같게)
            c.execute("DELETE FROM dev_submissions WHERE id=? AND status='checking'", (sid,))
        raise
    if st == "sandbox":
        threading.Thread(target=_pipeline, args=(sid, ctx.agent_user, top, os.path.join(base, "src")), daemon=True, name=f"devcheck-{sid}").start()
    with db() as c:
        return _public(c.execute("SELECT * FROM dev_submissions WHERE id=?", (sid,)).fetchone(), True)


def _public(r, detail=False):
    d = {k: r[k] for k in ("id", "uid", "dir", "title", "status", "created", "updated", "size", "nfiles", "note", "decided_by", "decided_ts", "port")}
    chk = json.loads(r["checks"] or "null") or {}
    sb = json.loads(r["sandbox"] or "null")
    inst = json.loads(r["install"] or "null") if "install" in r.keys() else None
    d["errors"], d["warnings"] = len(chk.get("errors") or []), len(chk.get("warnings") or [])
    d["sandbox_status"] = (sb or {}).get("status")
    d["install_status"] = (inst or {}).get("status")
    d["pkg_confirm"] = any(str(w).startswith(PKG_BLOCK) for w in chk.get("warnings") or [])
    if detail:
        d["checks"], d["sandbox"], d["install"] = chk, sb, inst
        src = os.path.join(SUB_DIR, r["id"], "src", r["dir"] or "")
        fl = []
        if r["dir"] and os.path.isdir(src):
            for root, _, fs in os.walk(src):
                for f in fs:
                    p = os.path.join(root, f)
                    fl.append({"path": os.path.relpath(p, src), "size": os.path.getsize(p)})
        d["files"] = sorted(fl, key=lambda x: x["path"])[:500]
    return d


def _get(c, ctx, sid, admin_only=False):
    return own_row(c, "dev_submissions", sid, None if admin_only else ctx.uid, "제출을 찾을 수 없습니다", "이 제출을 볼 권한이 없습니다", ctx.is_owner)


def file_text(c, ctx, sid, rel):
    """제출 파일 하나 미리보기(글자, 200KB) — 관리자·제출자만"""
    r = _get(c, ctx, sid)
    src = os.path.realpath(os.path.join(SUB_DIR, r["id"], "src", r["dir"]))
    p = os.path.realpath(os.path.join(src, rel))
    if not p.startswith(src + os.sep) or not os.path.isfile(p) or os.path.islink(p):
        raise FileNotFoundError("파일을 찾을 수 없습니다")
    with open(p, "rb") as f:
        b = f.read(200 * 1024)
    if b"\x00" in b[:4096]:
        return {"path": rel, "binary": True, "text": ""}
    return {"path": rel, "binary": False, "text": b.decode("utf-8", "replace")}


# ── 승인·설치 ───────────────────────────────────────────────────────────
_INSTALL_LOCK = threading.Lock()


def _free_port():
    used = {int(t["port"]) for t in tools.tools()}
    with db() as c:
        used |= {r[0] for r in c.execute("SELECT port FROM dev_submissions WHERE port IS NOT NULL")}
    for p in range(*PORT_RANGE):
        if p in used:
            continue
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) == 0:
                continue
        return p
    raise ValueError("배정할 수 있는 포트가 없습니다")


def decide(ctx, req):
    """POST /api/admin/dev/decide {id, ok, note, confirm_skipped?, confirm_pkgs?} — 관리자만. 승인은 설치를 뒤에서 시작하고 바로 'installing' 으로 답함"""
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    note = str(req.get("note") or "").strip()[:300]
    ok = bool(req.get("ok"))
    with _INSTALL_LOCK:
        with db() as c:
            r = _get(c, ctx, req.get("id"), admin_only=True)
            if r["status"] != "review" and not (not ok and r["status"] in ("failed", "sandbox", "checking")):
                raise ValueError("승인 대기 상태가 아닙니다" if ok else "이미 처리된 제출입니다")
            if not ok:
                if not note:
                    raise ValueError("거절 사유를 적어 주세요")
                c.execute("UPDATE dev_submissions SET status='rejected', note=?, decided_by=?, decided_ts=?, updated=? WHERE id=?", (note, ctx.uid, now(), now(), r["id"]))
                accounts.audit(c, ctx.uid, "dev_reject", r["dir"] or "", f"#{r['id']} {note}")
                _notify(r["agent_user"], r["id"], "failed", f"제출 {r['dir']}: 거절", note)
                return {"ok": True, "status": "rejected"}
            if not config.DEV_APPROVE:
                raise Locked(LOCK_MSG)
            chk = json.loads(r["checks"] or "{}")
            if chk.get("errors"):
                raise ValueError("형식 검사 오류가 있는 제출은 승인할 수 없습니다")
            sb = json.loads(r["sandbox"] or "{}")
            if sb.get("status") == "skipped" and req.get("confirm_skipped") is not True:
                raise ValueError("격리 검증을 건너뛴 제출입니다 — 확인(confirm_skipped)이 필요합니다")
            if any(str(w).startswith(PKG_BLOCK) for w in chk.get("warnings") or []) and req.get("confirm_pkgs") is not True:
                raise ValueError("폐쇄망 패키지 목록에 없는 설치 항목이 있습니다 — 확인(confirm_pkgs)이 필요합니다(빌드가 실패할 수 있음)")
            d = r["dir"]
            if tools.tool(d) or os.path.exists(os.path.join(config.BASE, d)):
                raise ValueError(f"이미 같은 이름의 도구·폴더가 있습니다: {d}")
            if c.execute("SELECT 1 FROM dev_submissions WHERE status='installing'").fetchone():
                raise ValueError("다른 제출을 설치하는 중입니다 — 끝난 뒤 다시 승인하세요")
            port = _free_port()
            c.execute("UPDATE dev_submissions SET status='installing', note=?, decided_by=?, decided_ts=?, updated=?, port=?, install=? WHERE id=? AND status='review'",
                      (note, ctx.uid, now(), now(), port, json.dumps({"status": "running", "steps": []}), r["id"]))
            accounts.audit(c, ctx.uid, "dev_approve", d, f"#{r['id']} 포트 {port} — 격리 설치 시작")
    threading.Thread(target=_install, args=(r["id"], ctx.uid, r["agent_user"], d, port, chk.get("meta") or {}, r["uid"]),
                     daemon=True, name=f"devinstall-{r['id']}").start()
    return {"ok": True, "status": "installing", "port": port}


def _install(sid, actor, agent_user, d, port, meta, author):
    """승인 설치(뒤에서): 이미지 빌드 → 컨테이너 기동 → /healthz → tools.json 등록 → 사용법. 실패하면 거꾸로 되돌리고 review 로"""
    steps, made = [], {}
    t = {"dir": d, "port": port, "runtime": "container", "image": ""}

    def step(name, ok, detail=""):
        steps.append({"name": name, "ok": bool(ok), "detail": str(detail)[-1500:]})
        with db() as c:   # 화면이 진행을 보게 단계마다 적음
            c.execute("UPDATE dev_submissions SET install=?, updated=? WHERE id=? AND status='installing'",
                      (json.dumps({"status": "running", "steps": steps}, ensure_ascii=False), now(), sid))

    def audit(action, detail=""):
        with db() as c:
            accounts.audit(c, actor, action, d, f"#{sid} {detail}".strip())

    work = os.path.join(SUB_DIR, sid, "build")
    try:
        shutil.rmtree(work, ignore_errors=True)
        os.makedirs(work, mode=0o700)
        image, _ = devtool.build(d, os.path.join(SUB_DIR, sid, "src", d), work, "s-" + re.sub(r"[^a-z0-9]", "", sid.lower())[:20], config.PIP_INDEX_URL)
        made["image"] = t["image"] = image
        step("이미지 빌드", True, image); audit("dev_build", image)
        devtool.start(t, {"PORTAL_TOOL_KEY": jobs.container_key(d)} if config.TOOL_AUTH else {})
        made["container"] = True
        step("컨테이너 시작", True, f"{devtool.container_name(d)} · {devtool.ip_for(t)}:{port}"); audit("tool_start", "격리 컨테이너")
        ok, detail = devtool.wait_health(t, HEALTH_WAIT)
        if not ok:
            raise RuntimeError(f"/healthz 확인 실패 — {detail}\n{devtool.logs(d)}")
        step("/healthz", True, detail)
        tools.add_tool({"dir": d, "port": port, "name": meta.get("name") or d, "title": meta.get("title") or d, "desc": meta.get("desc", ""),
                        "group": meta.get("group") or "기타", "tags": meta.get("tags", ""), "dept": meta.get("dept", ""), "author": author,
                        "runtime": "container", "image": image})
        made["tool"] = True
        step("포털 등록", True, "tools.json runtime=container"); audit("tool_add", "개발자 제출 승인(격리 컨테이너)")
        gp = os.path.join(config.ROOT, "guides", f"{d}.md")
        os.makedirs(os.path.dirname(gp), exist_ok=True)
        if not os.path.exists(gp):
            with open(gp, "w", encoding="utf-8") as f:
                f.write(meta.get("usage") or f"## {d}\n")
            made["guide"] = True
        step("사용법", True, f"guides/{d}.md" + ("" if made.get("guide") else " (이미 있음 — 그대로)"))
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='approved', install=?, updated=? WHERE id=? AND status='installing'",
                      (json.dumps({"status": "done", "steps": steps, "image": image, "guide_created": bool(made.get("guide"))}, ensure_ascii=False), now(), sid))
        audit("dev_install_ok", image)
        tools.alive_reset(); tools.stats_reset()
        _notify(agent_user, sid, "done", f"제출 {d}: 승인 — 포털에 등록됨", "격리 컨테이너로 시작했습니다")
        _notify("owner", sid, "done", f"개발자 제출 설치 완료: {d}", f"격리 컨테이너 {devtool.container_name(d)}")
    except Exception as e:
        reason = str(e).split("\n")[0][:300] if isinstance(e, (RuntimeError, ValueError, OSError)) else f"서버 오류({type(e).__name__})"
        log = str(e)[-4000:] if isinstance(e, RuntimeError) else ""
        print("devsubmit: 설치 실패", sid, d, type(e).__name__, reason, flush=True)
        undo = _rollback(d, t, made)
        steps.append({"name": "되돌리기", "ok": True, "detail": ", ".join(undo) or "남은 것 없음"})
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review', port=NULL, install=?, updated=? WHERE id=? AND status='installing'",
                      (json.dumps({"status": "failed", "reason": reason, "steps": steps, "log": log}, ensure_ascii=False), now(), sid))
        audit("dev_install_fail", reason[:200])
        _notify("owner", sid, "failed", f"개발자 제출 설치 실패: {d}", reason)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rollback(d, t, made):
    """설치 중 만든 것만 거꾸로 지움 → 지운 것 이름 목록"""
    out = []
    for name, fn in (("guide", lambda: os.remove(os.path.join(config.ROOT, "guides", f"{d}.md"))), ("tool", lambda: tools.remove_tool(d)),
                     ("container", lambda: devtool.stop(t)), ("image", lambda: devtool.remove_image(d, t.get("image")))):
        if made.get(name):
            try:
                fn(); out.append({"guide": "사용법", "tool": "tools.json 줄", "container": "컨테이너", "image": "이미지"}[name])
            except Exception as e:
                print("devsubmit: 되돌리기 실패", d, name, type(e).__name__, flush=True)
    tools.alive_reset()
    return out


def revoke(ctx, req):
    """POST /api/admin/dev/revoke {id, note} — 관리자만. 승인된 제출을 등록 해제: 컨테이너·이미지·tools.json 줄·승인 때 만든 사용법 지움(데이터는 남김)"""
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    note = str(req.get("note") or "").strip()[:300]
    if not note:
        raise ValueError("회수 사유를 적어 주세요")
    with _INSTALL_LOCK:
        with db() as c:
            r = _get(c, ctx, req.get("id"), admin_only=True)
        if r["status"] != "approved":
            raise ValueError("승인된 제출만 회수할 수 있습니다")
        d, inst = r["dir"], json.loads(r["install"] or "{}")
        t = tools.tool(d)
        if t and not tools.is_container(t):
            raise ValueError("격리 컨테이너 도구가 아닙니다 — 관리자가 직접 정리하세요")
        t = t or {"dir": d, "port": r["port"] or 0}
        image = t.get("image") or inst.get("image")
        devtool.stop(t)
        tools.remove_tool(d)
        devtool.remove_image(d, image)
        gp = os.path.join(config.ROOT, "guides", f"{d}.md")
        if inst.get("guide_created") and os.path.exists(gp):
            os.remove(gp)
        tools.alive_reset(); tools.stats_reset()
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='revoked', note=?, updated=?, port=NULL WHERE id=?", (note, now(), r["id"]))
            accounts.audit(c, ctx.uid, "dev_revoke", d, f"#{r['id']} {note}")
            accounts.audit(c, ctx.uid, "tool_remove", d, "개발자 제출 회수(컨테이너·이미지 정리, 데이터는 남김)")
    _notify(r["agent_user"], r["id"], "canceled", f"제출 {d}: 회수됨", note)
    return {"ok": True, "status": "revoked"}


# ── 개발자 신청 ──────────────────────────────────────────────────────────
APPLY_AGAIN_S = 86400   # 보류된 뒤 다시 신청할 수 있기까지


def apply(ctx, req):
    """POST /api/dev/apply {reason} — 로그인한 일반 계정. 관리자 알림함에 올림"""
    if not ctx.uid or ctx.uid == "owner":
        raise PermissionError("로그인한 계정만 신청할 수 있습니다")
    reason = str(req.get("reason") or "").strip()[:300]
    if len(reason) < 5:
        raise ValueError("만들 도구나 하려는 일을 짧게 적어 주세요(5자 이상)")
    with db() as c:
        if is_developer(c, ctx.uid):
            raise ValueError("이미 개발자 권한이 있습니다")
        cur = c.execute("SELECT status, decided_ts FROM dev_applications WHERE uid=?", (ctx.uid,)).fetchone()
        if cur and cur["status"] == "pending":
            raise ValueError("이미 신청했습니다 — 관리자 확인을 기다려 주세요")
        if cur and cur["status"] == "dismissed" and cur["decided_ts"] and cur["decided_ts"] > (timeutil.utcnow() - datetime.timedelta(seconds=APPLY_AGAIN_S)).isoformat(timespec="seconds"):
            raise ValueError("보류된 신청은 하루 뒤 다시 할 수 있습니다")
        c.execute("INSERT INTO dev_applications(uid, reason, ts, status) VALUES(?,?,?, 'pending') ON CONFLICT(uid) DO UPDATE SET "
                  "reason=excluded.reason, ts=excluded.ts, status='pending', decided_by=NULL, decided_ts=NULL, note=NULL", (ctx.uid, reason, now()))
        accounts.audit(c, ctx.uid, "dev_apply", ctx.uid, reason[:200])
    _notify("owner", "apply-" + ctx.uid, "done", f"개발자 신청: {ctx.uid}", reason, link="/create?tab=make&m=dev&apps=1")
    return {"ok": True, "application": {"status": "pending", "reason": reason}}


def my_application(ctx):
    with db() as c:
        r = c.execute("SELECT status, reason, ts, note FROM dev_applications WHERE uid=?", (ctx.uid,)).fetchone()
        return {"developer": is_developer(c, ctx.uid), "application": dict(r) if r else None}


def applications(c):
    """관리자: 기다리는 신청 먼저, 최근 처리 50개"""
    rows = c.execute("SELECT a.*, u.name, u.dept FROM dev_applications a LEFT JOIN users u ON u.id=a.uid "
                     "ORDER BY a.status<>'pending', a.ts DESC LIMIT 100").fetchall()
    return [{k: r[k] for k in ("uid", "name", "dept", "reason", "ts", "status", "decided_by", "decided_ts", "note")} for r in rows]


def decide_application(ctx, req):
    """POST /api/admin/dev/applications {uid, op: grant|dismiss, note} — 관리자만"""
    if not ctx.is_owner:
        raise PermissionError("관리자만 할 수 있습니다")
    uid, op, note = str(req.get("uid") or ""), req.get("op"), str(req.get("note") or "").strip()[:300]
    if op not in ("grant", "dismiss"):
        raise ValueError("op 는 grant|dismiss")
    with db() as c:
        if not c.execute("SELECT 1 FROM dev_applications WHERE uid=? AND status='pending'", (uid,)).fetchone():
            raise ValueError("기다리는 신청이 없습니다")
        if op == "grant":
            set_developer(c, ctx.uid, uid, True)   # 신청도 granted 로 바뀜 + 감사 developer_grant
        else:
            c.execute("UPDATE dev_applications SET status='dismissed', decided_by=?, decided_ts=?, note=? WHERE uid=?", (ctx.uid, now(), note, uid))
            accounts.audit(c, ctx.uid, "dev_apply_dismiss", uid, note)
    if op == "grant":
        _notify(uid, "apply-" + uid, "done", "개발자 권한을 받았습니다", "모두의 에이전트 → 만들기 → 개발자 업로드에서 도구 폴더를 올릴 수 있습니다", link="/create?tab=make&m=dev")
    else:
        _notify(uid, "apply-" + uid, "canceled", "개발자 신청이 보류되었습니다", note or "관리자에게 문의하세요", link="/create?tab=make&m=dev")
    return {"ok": True}


# ── 샘플·형식 안내 ───────────────────────────────────────────────────────
def sample_zip():
    """hello-local 샘플 zip(sdk/samples/hello-local + 포털 sdk portal_auth.py + FORMAT.md) — 그대로 올려도 검사를 통과함"""
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, fs in os.walk(SAMPLE_DIR):
            dirs[:] = sorted(x for x in dirs if x not in ("__pycache__", "_workspace"))
            for f in sorted(fs):
                if f.endswith(".pyc"):
                    continue
                p = os.path.join(root, f)
                z.write(p, "hello-local/" + os.path.relpath(p, SAMPLE_DIR))
        for src, name in ((SDK_AUTH, "portal_auth.py"), (FORMAT_DOC, "FORMAT.md")):
            if os.path.exists(src):
                z.write(src, "hello-local/" + name)
    return b.getvalue()


# ── 라우팅(app.py 가 부름) ───────────────────────────────────────────────
def handle_get(ctx):
    p = ctx.p
    try:
        if p == "/api/dev/apply":   # 내 개발자 신청 상태(로그인)
            return ctx.send(my_application(ctx))
        if p == "/api/dev/sample.zip":   # 샘플 도구 zip(로그인) — 누구나 받아 형식을 볼 수 있게
            return ctx.send(sample_zip(), "application/zip", headers=[("Content-Disposition", 'attachment; filename="hello-local.zip"'), ("Cache-Control", "no-store")])
        if p == "/api/dev/format":   # 형식 안내(마크다운)
            with open(FORMAT_DOC, encoding="utf-8") as f:
                return ctx.send({"markdown": f.read()})
        with db() as c:
            if p == "/api/admin/dev/applications":
                if not ctx.is_owner:
                    raise PermissionError("관리자만 볼 수 있습니다")
                return ctx.send({"applications": applications(c)})
            dev = is_developer(c, ctx.uid)
            if p == "/api/dev/submissions":
                if not (dev or ctx.is_owner):
                    raise PermissionError("개발자 권한이 필요합니다")
                if ctx.is_owner:
                    rows = c.execute("SELECT * FROM dev_submissions ORDER BY created DESC LIMIT 200").fetchall()
                else:
                    rows = c.execute("SELECT * FROM dev_submissions WHERE uid=? ORDER BY created DESC LIMIT 100", (ctx.uid,)).fetchall()
                return ctx.send({"submissions": [_public(r) for r in rows], "developer": dev, "admin": ctx.is_owner,
                                 "limits": {"zip_max": ZIP_MAX, "files_max": FILES_MAX, "unzip_max": UNZIP_MAX}, "image": IMAGE,
                                 "approve": {"enabled": config.DEV_APPROVE, "reason": "" if config.DEV_APPROVE else LOCK_MSG}, "runtime": "container"})
            if p.startswith("/api/dev/submissions/"):
                rest = p[len("/api/dev/submissions/"):]
                sid, _, sub = rest.partition("/")
                if sub == "file":
                    import urllib.parse
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
                    return ctx.send(file_text(c, ctx, sid, q.get("path", [""])[0]))
                if sub:
                    raise FileNotFoundError("not found")
                return ctx.send(_public(_get(c, ctx, sid), True))
        return ctx.send({"error": "not found"}, code=404)
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e), "code": "not_found"}, code=404)


def pkg_get(ctx):
    """GET /api/pkg-index?q= (최대 50개, 접두·부분 일치) · /api/pkg-index/check?names=a,b — 로그인. 목록 날짜·개수 같이"""
    import urllib.parse
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    with db() as c:
        m = pkgindex.meta(c)
        if ctx.p == "/api/pkg-index":
            return ctx.send({**m, "items": pkgindex.search(c, (q.get("q") or [""])[0][:100])})
        names = [x.strip() for x in ",".join(q.get("names") or []).split(",") if x.strip()][:200]
        return ctx.send({**m, "results": pkgindex.lookup(c, names)})


def pkg_upload(ctx, body):
    """POST /api/admin/pkg-index?source=<파일 이름> (본문 = 목록 글자) — 관리자. 표를 통째로 바꾸고 감사 기록"""
    import urllib.parse
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    src = re.sub(r"[^\w.() -]", "_", (q.get("source") or ["업로드"])[0])[:80] or "업로드"
    try:
        entries = pkgindex.parse_list(body.decode("utf-8", errors="replace"))
    except Exception:
        entries = {}
    if not entries:
        return ctx.send({"error": "패키지 이름을 하나도 읽지 못했습니다 — 한 줄에 하나(이름, 이름==버전, '이름 버전,버전', pip index 출력)", "code": "bad_request"}, code=400)
    with db() as c:
        pkgindex.replace(c, entries, src)
        nv = sum(1 for v in entries.values() if v)
        accounts.audit(c, ctx.uid, "pkg_index_update", src, f"{len(entries)}개(버전 있음 {nv})")
        return ctx.send({"ok": True, **pkgindex.meta(c), "with_versions": nv})


def handle_submit(ctx, data):
    try:
        return ctx.send({"submission": submit(ctx, data)})
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except (ValueError, OSError) as e:
        return ctx.send({"error": str(e) if isinstance(e, ValueError) else "파일을 저장하지 못했습니다", "code": "bad_request"}, code=400)


def handle_post(ctx, req):
    p = ctx.p
    try:
        if p == "/api/admin/dev/decide":
            try:
                return ctx.send(decide(ctx, req))
            except Locked as e:
                return ctx.send({"error": str(e), "code": "approve_locked"}, code=409)
        if p == "/api/admin/dev/revoke":
            return ctx.send(revoke(ctx, req))
        if p == "/api/admin/dev/applications":
            return ctx.send(decide_application(ctx, req))
        if p == "/api/dev/apply":
            return ctx.send(apply(ctx, req))
        if p == "/api/admin/developer":
            if not ctx.is_owner:
                raise PermissionError("관리자만 할 수 있습니다")
            op = req.get("op")
            if op not in ("grant", "revoke"):
                raise ValueError("op 는 grant|revoke")
            with db() as c:
                set_developer(c, ctx.uid, str(req.get("uid") or ""), op == "grant")
            return ctx.send({"ok": True})
        return ctx.send({"error": "not found"}, code=404)
    except PermissionError as e:
        return ctx.send({"error": str(e), "code": "forbidden"}, code=403)
    except FileNotFoundError as e:
        return ctx.send({"error": str(e), "code": "not_found"}, code=404)
    except (ValueError, KeyError, TypeError) as e:
        return ctx.send({"error": str(e) if not isinstance(e, TypeError) else "요청 값 형식이 틀립니다", "code": "bad_request"}, code=400)


def recover():
    """재시작 때: 검증 중으로 남은 제출은 실패로(스레드가 사라짐 — 다시 올리면 됨). 설치 중이던 승인은 만든 것을 지우고 승인 대기로"""
    with db() as c:
        c.execute("UPDATE dev_submissions SET status='failed', sandbox=?, updated=? WHERE status IN ('checking','sandbox')",
                  (json.dumps({"status": "failed", "reason": "포털이 다시 시작되어 검증이 멈췄습니다 — 다시 올려 주세요", "steps": []}, ensure_ascii=False), now()))
        stuck = c.execute("SELECT id, dir, port, install FROM dev_submissions WHERE status='installing'").fetchall()
    for r in stuck:
        d = r["dir"]
        inst = json.loads(r["install"] or "{}")
        made = {"tool": bool(tools.tool(d)) and tools.is_container(tools.tool(d)), "container": True,
                "image": any(s.get("name") == "이미지 빌드" for s in inst.get("steps") or [])}
        img = next((s["detail"] for s in inst.get("steps") or [] if s.get("name") == "이미지 빌드"), "")
        try:
            _rollback(d, {"dir": d, "port": r["port"] or 0, "image": img}, made)
        except Exception as e:
            print("devsubmit: 설치 되돌리기 실패", d, type(e).__name__, flush=True)
        with db() as c:
            c.execute("UPDATE dev_submissions SET status='review', port=NULL, install=?, updated=? WHERE id=? AND status='installing'",
                      (json.dumps({"status": "failed", "reason": "포털이 다시 시작되어 설치가 멈췄습니다 — 다시 승인하세요", "steps": inst.get("steps") or []}, ensure_ascii=False), now(), r["id"]))
