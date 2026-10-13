#!/bin/bash
# 에이전트 페이지 포털 기동(:8700, 모든 주소에서 열림) — 포털 = FastAPI(fastportal, uvicorn 1워커) since 2026-10-13. 데이터: <상위폴더>/_data
# LLM 은 vLLM(OpenAI 호환, 기본 :11440, 전용 GPU 1장, 이름 gemma4:31b), 임베딩은 로컬 Ollama(기본 :11436, bge-m3) — 값은 scripts/portal-env.sh
# 이 파일은 상위 폴더(도구들을 나란히 clone 한 곳)의 start-portal.sh 사본입니다. 상위 폴더에서 실행해도 되고 여기서 실행해도 됩니다.
#   start-portal.sh                         # FastAPI 포털(scripts/start-fastportal.sh 를 PORT=8700 BACKUP=1 로) · 로그 agent-page-portal/portal.log
#   PORTAL_IMPL=classic start-portal.sh     # 되돌리기: 예전 portal.py(ThreadingHTTPServer) — 같은 DB·쿠키라 사용자 세션은 그대로
# 바꿔 띄우기 전에 떠 있는 포털을 그 PID 로 끄기: FastAPI 는 scripts/start-fastportal.sh 가 pid 파일(fastportal/run-8700.pid)로 알아서 끔,
#   portal.py 는 pid 를 찾아 kill <pid>(pkill -f 는 다른 포트의 portal.py 까지 죽이므로 쓰지 말 것)
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
OLLAMA_PORT="${OLLAMA_PORT:-11436}"
VLLM_PORT="${VLLM_PORT:-11440}"
# vLLM 은 자동으로 띄우지 않음(GPU 메모리를 미리 잡고 뜨는 데 1~3분) — 꺼져 있으면 경고만. 띄우는 법(예): ~/.local/vllm/start.sh
#   vllm serve <Gemma 4 31B-it FP8> --served-model-name gemma4:31b --host 127.0.0.1 --port 11440 --max-model-len 32768 --enable-prefix-caching
curl -s -m 3 127.0.0.1:$VLLM_PORT/v1/models >/dev/null || echo "경고: vLLM(:$VLLM_PORT) 이 응답하지 않음 — LLM 을 쓰는 도구가 실패함" >&2
curl -s -m 2 127.0.0.1:$OLLAMA_PORT/api/version >/dev/null || { [ -x ~/.local/ollama-gpu/start.sh ] && OLLAMA_GPUS="${OLLAMA_GPUS:-0,1,2}" ~/.local/ollama-gpu/start.sh; }  # Ollama(임베딩·되돌리기용)가 꺼져 있으면 띄움(자기 환경에 맞게)
. agent-page-portal/scripts/portal-env.sh   # LLM·임베딩·음성 연결·GPU 후보 export — 포털이 도구를 켤 때 그대로 물려줌

IMPL="${PORTAL_IMPL:-fast}"
if [ "$IMPL" = fast ] && [ ! -x "${FASTPORTAL_VENV:-$HOME/.local/fastportal-venv}/bin/python" ]; then
  echo "경고: fastportal venv 가 없어 예전 portal.py 로 띄움 — README 'FastAPI 포털' 의 설치를 먼저" >&2; IMPL=classic
fi
if [ "$IMPL" = classic ]; then
  cd agent-page-portal && nohup python3 portal.py > portal.log 2>&1 &
  echo "portal(portal.py) pid $! → http://$(hostname -I | awk '{print $1}'):8700"
else
  PORT=8700 BACKUP="${BACKUP:-1}" FASTPORTAL_LOG=portal.log agent-page-portal/scripts/start-fastportal.sh   # 백업 루프(매일 03시 KST)는 :8700 만
fi
