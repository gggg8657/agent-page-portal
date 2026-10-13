#!/bin/bash
# 새 포털(FastAPI·uvicorn, 비동기) 기동 — 기본 :8720. 운영 :8700(portal.py)과 같은 _data/portal.db·HTML·tools.json 을 쓴다.
#   scripts/start-fastportal.sh            # 시작(이미 떠 있으면 그 프로세스만 끄고 다시)
#   scripts/start-fastportal.sh stop       # 끄기
# env: PORT(8720) FASTPORTAL_VENV(~/.local/fastportal-venv) BACKUP(기본 0 — 운영 포털이 백업을 돌림) — 나머지는 start-portal.sh 와 같음
# 도구를 이 포털에서 켜면 아래 LLM·연결 환경변수를 물려받는다 — start-portal.sh 와 같은 값이어야 함(vLLM 전환 등으로 그쪽을 바꾸면 여기도; 운영 전환 전 확인).
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
TOP="$PWD"
PORT="${PORT:-8720}"
VENV="${FASTPORTAL_VENV:-$HOME/.local/fastportal-venv}"
PIDF="$TOP/agent-page-portal/fastportal/run-$PORT.pid"   # fastportal/.gitignore 로 커밋 안 됨
LOG="$TOP/agent-page-portal/fastportal-$PORT.log"

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

OLLAMA_PORT="${OLLAMA_PORT:-11436}"
export CUDA_VISIBLE_DEVICES="${AGENT_GPUS:-0,1,2,3}" CUDA_DEVICE_ORDER=PCI_BUS_ID PATH="$HOME/.local/bin:$PATH"
export LLM_API="${LLM_API:-ollama}" LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:$OLLAMA_PORT}" LLM_MODEL="${LLM_MODEL:-gemma4:31b}" MODEL="${LLM_MODEL:-gemma4:31b}" VISION_MODEL="${LLM_MODEL:-gemma4:31b}" NUM_CTX="${NUM_CTX:-32768}" AGENT_DATA="${AGENT_DATA:-$TOP/_data}"
export TTS_BASE_URL=http://127.0.0.1:8771/v1 STT_BASE_URL=http://127.0.0.1:8767/v1 AVATAR_URL=http://127.0.0.1:8777/api/run
export TTS_VOICE=KR TTS_MODEL=melo TTS_DEVICE=cuda DEVICE=cuda WHISPER_DEVICE=cuda
export PORT BACKUP="${BACKUP:-0}"

cd agent-page-portal
nohup "$VENV/bin/python" -m fastportal > "$LOG" 2>&1 &
echo $! > "$PIDF"
echo "fastportal pid $! → http://localhost:$PORT  (로그 $LOG)"
