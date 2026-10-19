"""격리 컨테이너 안에서 도는 자가검증(표준 라이브러리만) — devsubmit.sandbox 가 docker run 으로 부름. 포털 프로세스에서는 import 하지 않음.
  python3 -I run.py /src <dir>
  1) /src(읽기 전용)를 /tmp/w/<dir> 로 복사  2) 모든 .py 문법 검사(compile — .pyc 안 씀)  3) selftest.py 가 있으면 실행(60초)
  4) app.py 를 PORT=18080 BIND=127.0.0.1 서명 모드(무작위 PORTAL_TOOL_KEY)로 띄우고 /healthz 응답 확인(40초)
마지막 줄: DEVCHECK_RESULT {"steps": [{name, ok, skipped?, detail}]}"""
import json, os, secrets, shutil, subprocess, sys, time, urllib.request

src, top = sys.argv[1], sys.argv[2]
work = os.path.join("/tmp/w", top)
steps = []


def step(name, ok, detail="", skipped=False):
    steps.append({"name": name, "ok": bool(ok), "skipped": skipped, "detail": str(detail)[-1500:]})
    print(f"[{'건너뜀' if skipped else '통과' if ok else '실패'}] {name} {detail}"[:2000], flush=True)


def finish():
    print("DEVCHECK_RESULT " + json.dumps({"steps": steps}, ensure_ascii=False), flush=True)
    sys.exit(0)


try:
    shutil.copytree(os.path.join(src, top), work, symlinks=False)   # /src 아래 <dir> 폴더 하나
    if not os.path.exists(os.path.join(work, "portal_auth.py")) and os.path.exists("/check/sdk/portal_auth.py"):
        shutil.copy("/check/sdk/portal_auth.py", os.path.join(work, "portal_auth.py"))
    step("복사", True)
except Exception as e:
    step("복사", False, f"{type(e).__name__}: {e}")
    finish()

bad = []
n = 0
for root, dirs, files in os.walk(work):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
    for f in files:
        if f.endswith(".py"):
            n += 1
            p = os.path.join(root, f)
            try:
                compile(open(p, "rb").read(), p, "exec")
            except SyntaxError as e:
                bad.append(f"{os.path.relpath(p, work)}:{e.lineno} {e.msg}")
step("파이썬 문법 검사", not bad, f"{n}개 파일" + (" — " + "; ".join(bad[:5]) if bad else ""))

env = {**os.environ, "WORKSPACE": "/tmp/ws", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
os.makedirs("/tmp/ws", exist_ok=True)
if os.path.exists(os.path.join(work, "selftest.py")):
    try:
        r = subprocess.run([sys.executable, "selftest.py"], cwd=work, env=env, capture_output=True, text=True, timeout=60)
        step("selftest.py", r.returncode == 0, f"종료 코드 {r.returncode}\n{(r.stdout + r.stderr)[-1200:]}")
    except subprocess.TimeoutExpired:
        step("selftest.py", False, "60초 시간 제한")
else:
    step("selftest.py", True, "없음", skipped=True)

if not os.path.exists(os.path.join(work, "app.py")):
    step("서버 /healthz", True, "app.py 없음(setup.sh 만) — 의존성 설치가 필요한 도구는 네트워크가 막힌 격리 환경에서 띄우지 않음", skipped=True)
    finish()

port = "18080"
env2 = {**env, "PORT": port, "BIND": "127.0.0.1", "PORTAL_TOOL_KEY": secrets.token_hex(16)}
p = subprocess.Popen([sys.executable, "app.py"], cwd=work, env=env2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ok, detail = False, ""
t0 = time.time()
while time.time() - t0 < 40:
    if p.poll() is not None:
        detail = f"서버가 바로 끝남(종료 코드 {p.returncode})"
        break
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as r:
            body = r.read(4096).decode("utf-8", "replace")
            ok = r.status == 200
            detail = f"HTTP {r.status} {body[:300]}"
            try:
                if json.loads(body).get("auth") == "signed":
                    detail += " · 서명 모드 확인"
            except ValueError:
                pass
            break
    except Exception as e:
        detail = f"{type(e).__name__}"
        time.sleep(0.5)
else:
    detail = "40초 안에 /healthz 응답 없음 (" + detail + ")"
if p.poll() is None:
    p.terminate()
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        p.kill()
out = (p.stdout.read() if p.stdout else "")[-1500:]
step("서버 /healthz", ok, detail + ("\n" + out if out and not ok else ""))
finish()
