#!/bin/bash
# FastAPI 포털(fastportal, uvicorn 1워커) 기동 — 기본 :8720(시험용). 운영 :8700 은 start-portal.sh 가 이 스크립트를 PORT=8700 BACKUP=1 로 부름.
# 같은 _data/portal.db·HTML·tools.json 을 쓴다(여러 개가 같은 DB 를 써도 됨).
#   scripts/start-fastportal.sh            # 시작(이미 떠 있으면 그 프로세스만 끄고 다시)
#   scripts/start-fastportal.sh stop       # 끄기
# env: PORT(8720) FASTPORTAL_VENV(~/.local/fastportal-venv) BACKUP(기본 0 — 운영 :8700 만 1) FASTPORTAL_LOG(로그 파일 이름) — 나머지는 portal-env.sh
# 도구를 이 포털에서 켜면 LLM·연결 환경변수를 물려받는다 — scripts/portal-env.sh 한 곳에서 읽으므로 start-portal.sh 와 늘 같음.
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
TOP="$PWD"
PORT="${PORT:-8720}"
VENV="${FASTPORTAL_VENV:-$HOME/.local/fastportal-venv}"
PIDF="$TOP/agent-page-portal/fastportal/run-$PORT.pid"   # fastportal/.gitignore 로 커밋 안 됨
LOG="$TOP/agent-page-portal/${FASTPORTAL_LOG:-fastportal-$PORT.log}"   # 운영(:8700)은 start-portal.sh 가 portal.log 로

stop_old() {   # pid 파일의 그 프로세스만(명령줄에 fastportal 이 있는지 확인) — pkill/pgrep -f 로 남의 프로세스를 죽이지 않게
  [ -f "$PIDF" ] || return 0
  local pid; pid="$(cat "$PIDF")"
  if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'fastportal'; then
    kill "$pid"; for _ in 1 2 3 4 5 6 7 8 9 10; do [ -d "/proc/$pid" ] || break; sleep 0.5; done
    [ -d "/proc/$pid" ] && kill -9 "$pid"
    echo "fastportal pid $pid 종료"
  fi
  rm -f "$PIDF"
}

stop_old
[ "$1" = "stop" ] && exit 0
[ -x "$VENV/bin/python" ] || { echo "venv 없음: $VENV — README 'FastAPI 포털' 의 설치를 먼저"; exit 1; }

. "$TOP/agent-page-portal/scripts/portal-env.sh"   # LLM·임베딩·음성 연결·GPU 후보 — start-portal.sh 와 같은 파일(도구가 물려받음)
export PORT BACKUP="${BACKUP:-0}"

cd agent-page-portal
nohup "$VENV/bin/python" -m fastportal > "$LOG" 2>&1 &
echo $! > "$PIDF"
echo "fastportal pid $! → http://localhost:$PORT  (로그 $LOG)"
