"""모두의 에이전트 (C) 개발자 업로드 — 개발자 권한자가 도구 폴더(zip)를 올리면 형식 검사 → 격리 컨테이너 자가검증 → 관리자 승인 → 포털 카드 등록.

  권한: '개발자' 역할(developers 표) — 관리자만 부여·회수(감사 기록). 업로드 API 는 개발자만, 승인·거절은 관리자만. 개발자는 자기 제출만 봄.
  업로드: POST /api/dev/submit (본문 = zip 바이트, 상한 ZIP_MAX) — zip slip·절대 경로·심볼릭 링크·장치 파일 거절, 파일 수·풀린 크기 상한.
          _data/dev-submissions/<id>/src/<dir> 에 풀어 둠(폴더 0700).
  형식 검사(서버): <이름>-local 폴더 하나, app.py 또는 setup.sh, README.md, agent.json(title·desc·group·usage), 이름 충돌 없음,
          BIND 0.0.0.0 고정 금지, PORT 환경변수 사용, portal_auth 권장(경고), 위험 패턴(shell=True·eval/exec·pickle.load·os.system·외부 URL) 경고.
  격리 자가검증: Docker(DEVCHECK_DOCKER_HOST, 기본 tcp://127.0.0.1:2375) — 이름 dongjukim-devcheck-<id>, GPU 없음, --network none,
          CPU·메모리·프로세스 수 상한, 읽기 전용 루트 + /tmp(tmpfs), 권한 모두 뺌, 시간 제한. 안에서: 문법 검사(compile), selftest.py, 서버 띄워 /healthz.
          이미지(DEVCHECK_IMAGE)·Docker 가 없으면 '건너뜀'으로 표시하고 관리자에게 알림.
  승인: ~/agent-page/<dir> 로 설치 + tools.json 등록(tools.add_tool) + guides/<dir>.md + 포털이 시작(서명 모드 PORTAL_TOOL_KEY). 모두 감사 기록."""
import io, json, os, re, secrets, shutil, socket, stat, subprocess, threading, time, zipfile

from . import accounts, config, jobs, tools
from .db import db, now

ZIP_MAX = int(os.environ.get("DEVSUBMIT_ZIP_MAX", str(50 * 1024 * 1024)))
FILES_MAX = 2000
UNZIP_MAX = 200 * 1024 * 1024
FILE_MAX = 30 * 1024 * 1024
SUB_DIR = os.path.join(config.DATA, "dev-submissions")
DOCKER_HOST = os.environ.get("DEVCHECK_DOCKER_HOST", "tcp://127.0.0.1:2375")
IMAGE = os.environ.get("DEVCHECK_IMAGE", "dongjukim/agent-portal:v2")
IMAGE_PY = os.environ.get("DEVCHECK_PYTHON", "/opt/env/bin/python3")
CHECK_TIMEOUT = int(os.environ.get("DEVCHECK_TIMEOUT", "180"))
PORT_RANGE = (8810, 8899)
NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,38}-local")
ID_RE = re.compile(r"[A-Za-z0-9_-]{6,40}")
JOB_TOOL = "devsubmit"
CHECK_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devcheck_runner.py")
SDK_AUTH = os.path.join(config.ROOT, "sdk", "portal_auth.py")
STATES = ("checking", "sandbox", "review", "failed", "approved", "rejected")


def init(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS developers(uid TEXT PRIMARY KEY, granted_by TEXT, ts TEXT);
    CREATE TABLE IF NOT EXISTS dev_submissions(id TEXT PRIMARY KEY, uid TEXT NOT NULL, agent_user TEXT NOT NULL, dir TEXT, title TEXT,
        status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, size INTEGER, nfiles INTEGER,
        checks TEXT, sandbox TEXT, note TEXT, decided_by TEXT, decided_ts TEXT, port INTEGER);
    CREATE INDEX IF NOT EXISTS dev_sub_uid ON dev_submissions(uid, created);""")


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
    if "app.py" not in names and "setup.sh" not in names:
        err.append("app.py 또는 setup.sh 가 필요합니다(포털이 이것으로 띄움)")
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
        err.append("venv·node_modules 는 빼고 올려 주세요(설치는 setup.sh 가)")
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
    if "selftest.py" not in names:
        info.append("selftest.py 가 없습니다 — 있으면 격리 검증에서 실행함")
    info.append(f"파일 {len(files)}개 · {sum(s for _, s in files) // 1024}KB")
    m = {"title": str(meta.get("title") or top)[:60], "desc": str(meta.get("desc") or "")[:200], "group": str(meta.get("group") or "기타")[:20],
         "tags": str(meta.get("tags") or "")[:100] if not isinstance(meta.get("tags"), list) else " ".join(map(str, meta["tags"]))[:100],
         "name": str(meta.get("name") or meta.get("title") or top)[:30], "usage": usage[:20000], "dept": str(meta.get("dept") or "")[:40]}
    return {"errors": err, "warnings": warn[:40], "info": info, "meta": m}


# ── 격리 자가검증 ────────────────────────────────────────────────────────
def _docker(args, timeout):
    env = {**os.environ, "DOCKER_HOST": DOCKER_HOST}
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=env)


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


def _notify(user, sid, status, title, msg):
    try:
        j = jobs.upsert(user, JOB_TOOL, status, f"{sid}-{secrets.token_hex(3)}", title[:200], f"/create?tab=dev&sub={sid}", msg[:400])
        if j:
            jobs.HUB.publish("job", j, j["user"])
    except Exception as e:
        print("devsubmit: 알림 실패", type(e).__name__, e, flush=True)


def _update(sid, **kw):
    kw["updated"] = now()
    cols = ", ".join(f"{k}=?" for k in kw)
    with db() as c:
        c.execute(f"UPDATE dev_submissions SET {cols} WHERE id=?", (*[json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in kw.values()], sid))


def _pipeline(sid, agent_user, top, src):
    try:
        res = sandbox(sid, top, src)
    except Exception as e:
        print("devsubmit: 검증 오류", sid, type(e).__name__, e, flush=True)
        res = {"status": "failed", "reason": "검증 중 서버 오류", "steps": [], "log": ""}
    status = "failed" if res["status"] == "failed" else "review"
    _update(sid, sandbox=res, status=status)
    lab = {"passed": "통과", "failed": "실패", "skipped": "건너뜀"}[res["status"]]
    _notify(agent_user, sid, "done" if status == "review" else "failed", f"제출 {top}: 격리 검증 {lab}",
            "관리자 승인을 기다립니다" if status == "review" else res.get("reason", ""))
    if status == "review":
        _notify("owner", sid, "done", f"개발자 제출 승인 대기: {top}",
                f"격리 검증 {lab}" + (f" — {res.get('reason')} (관리자 확인 필요)" if res["status"] == "skipped" else ""))


def submit(ctx, data):
    """POST /api/dev/submit — 본문 zip. 개발자만"""
    with db() as c:
        if not is_developer(c, ctx.uid):
            raise PermissionError("개발자 권한이 필요합니다 — 관리자에게 요청하세요")
        if c.execute("SELECT COUNT(*) FROM dev_submissions WHERE uid=? AND status IN ('checking','sandbox')", (ctx.uid,)).fetchone()[0]:
            raise ValueError("검사 중인 제출이 있습니다 — 끝난 뒤 다시 올리세요")
    if len(data) < 22 or data[:2] != b"PK":
        raise ValueError("zip 파일을 올려 주세요")
    sid = secrets.token_urlsafe(9)
    base = os.path.join(SUB_DIR, sid)
    os.makedirs(SUB_DIR, mode=0o700, exist_ok=True)
    os.chmod(SUB_DIR, 0o700)
    os.makedirs(os.path.join(base, "src"), mode=0o700)
    try:
        top, files = _unpack(data, os.path.join(base, "src"))
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    with open(os.path.join(base, "upload.zip"), "xb") as f:
        f.write(data)
    os.chmod(os.path.join(base, "upload.zip"), 0o600)
    path = os.path.join(base, "src", top)
    with db() as c:
        c.execute("INSERT INTO dev_submissions(id, uid, agent_user, dir, title, status, created, updated, size, nfiles) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (sid, ctx.uid, ctx.agent_user, top, top, "checking", now(), now(), len(data), len(files)))
        chk = check_tree(c, sid, top, path, files)
        st = "failed" if chk["errors"] else "sandbox"
        c.execute("UPDATE dev_submissions SET checks=?, title=?, status=? WHERE id=?", (json.dumps(chk, ensure_ascii=False), chk["meta"]["title"], st, sid))
        accounts.audit(c, ctx.uid, "dev_submit", top, f"#{sid} {len(data) // 1024}KB 파일 {len(files)}개")
    if st == "sandbox":
        threading.Thread(target=_pipeline, args=(sid, ctx.agent_user, top, os.path.join(base, "src")), daemon=True, name=f"devcheck-{sid}").start()
    with db() as c:
        return _public(c.execute("SELECT * FROM dev_submissions WHERE id=?", (sid,)).fetchone(), True)


def _public(r, detail=False):
    d = {k: r[k] for k in ("id", "uid", "dir", "title", "status", "created", "updated", "size", "nfiles", "note", "decided_by", "decided_ts", "port")}
    chk = json.loads(r["checks"] or "null") or {}
    sb = json.loads(r["sandbox"] or "null")
    d["errors"], d["warnings"] = len(chk.get("errors") or []), len(chk.get("warnings") or [])
    d["sandbox_status"] = (sb or {}).get("status")
    if detail:
        d["checks"], d["sandbox"] = chk, sb
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
    if not isinstance(sid, str) or not ID_RE.fullmatch(sid):
        raise FileNotFoundError("제출을 찾을 수 없습니다")
    r = c.execute("SELECT * FROM dev_submissions WHERE id=?", (sid,)).fetchone()
    if not r:
        raise FileNotFoundError("제출을 찾을 수 없습니다")
    if ctx.is_owner:
        return r
    if admin_only or r["uid"] != ctx.uid:
        raise PermissionError("이 제출을 볼 권한이 없습니다")
    return r


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
    """POST /api/admin/dev/decide {id, ok, note} — 관리자만"""
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
            chk = json.loads(r["checks"] or "{}")
            if chk.get("errors"):
                raise ValueError("형식 검사 오류가 있는 제출은 승인할 수 없습니다")
            sb = json.loads(r["sandbox"] or "{}")
            if sb.get("status") == "skipped" and req.get("confirm_skipped") is not True:
                raise ValueError("격리 검증을 건너뛴 제출입니다 — 확인(confirm_skipped)이 필요합니다")
            d = r["dir"]
            if tools.tool(d) or os.path.exists(os.path.join(config.BASE, d)):
                raise ValueError(f"이미 같은 이름의 도구·폴더가 있습니다: {d}")
            src = os.path.join(SUB_DIR, r["id"], "src", d)
            dest = os.path.join(config.BASE, d)
            port = _free_port()
            meta = chk.get("meta") or {}
            shutil.copytree(src, dest, symlinks=False)
            try:
                for root, dirs, fs in os.walk(dest):
                    os.chmod(root, 0o755)
                    for f in fs:
                        os.chmod(os.path.join(root, f), 0o755 if f.endswith(".sh") else 0o644)
                if not os.path.exists(os.path.join(dest, "portal_auth.py")) and os.path.exists(SDK_AUTH):
                    shutil.copy2(SDK_AUTH, os.path.join(dest, "portal_auth.py"))
                row = tools.add_tool({"dir": d, "port": port, "name": meta.get("name") or d, "title": meta.get("title") or d, "desc": meta.get("desc", ""),
                                      "group": meta.get("group") or "기타", "tags": meta.get("tags", ""), "dept": meta.get("dept", ""), "author": r["uid"]})
            except Exception:
                shutil.rmtree(dest, ignore_errors=True)
                raise
            gdir = os.path.join(config.ROOT, "guides")
            os.makedirs(gdir, exist_ok=True)
            gp = os.path.join(gdir, f"{d}.md")
            if not os.path.exists(gp):
                with open(gp, "w", encoding="utf-8") as f:
                    f.write(meta.get("usage") or f"## {d}\n")
            c.execute("UPDATE dev_submissions SET status='approved', note=?, decided_by=?, decided_ts=?, updated=?, port=? WHERE id=?",
                      (note, ctx.uid, now(), now(), port, r["id"]))
            accounts.audit(c, ctx.uid, "dev_approve", d, f"#{r['id']} 포트 {port}")
            accounts.audit(c, ctx.uid, "tool_add", d, "개발자 제출 승인")
        t = tools.tool(d)
        tools.start(t, jobs.tool_env(t))
        tools.alive_reset()
        with db() as c:
            accounts.audit(c, ctx.uid, "tool_start", d)
        _notify(r["agent_user"], r["id"], "done", f"제출 {d}: 승인 — 포털에 등록됨", f"포털 카드로 등록되어 시작했습니다(포트 {port})")
        return {"ok": True, "status": "approved", "tool": row}


# ── 라우팅(app.py 가 부름) ───────────────────────────────────────────────
def handle_get(ctx):
    p = ctx.p
    try:
        with db() as c:
            dev = is_developer(c, ctx.uid)
            if p == "/api/dev/submissions":
                if not (dev or ctx.is_owner):
                    raise PermissionError("개발자 권한이 필요합니다")
                if ctx.is_owner:
                    rows = c.execute("SELECT * FROM dev_submissions ORDER BY created DESC LIMIT 200").fetchall()
                else:
                    rows = c.execute("SELECT * FROM dev_submissions WHERE uid=? ORDER BY created DESC LIMIT 100", (ctx.uid,)).fetchall()
                return ctx.send({"submissions": [_public(r) for r in rows], "developer": dev, "admin": ctx.is_owner,
                                 "limits": {"zip_max": ZIP_MAX, "files_max": FILES_MAX, "unzip_max": UNZIP_MAX}, "image": IMAGE})
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
            return ctx.send(decide(ctx, req))
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
    """재시작 때: 검증 중으로 남은 제출은 실패로(스레드가 사라짐 — 다시 올리면 됨)"""
    with db() as c:
        c.execute("UPDATE dev_submissions SET status='failed', sandbox=?, updated=? WHERE status IN ('checking','sandbox')",
                  (json.dumps({"status": "failed", "reason": "포털이 다시 시작되어 검증이 멈췄습니다 — 다시 올려 주세요", "steps": []}, ensure_ascii=False), now()))
