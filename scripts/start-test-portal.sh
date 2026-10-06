#!/bin/bash
# 본인 테스트용 포털 (:8701, 이 서버 안에서만 열림) — tools.json 에서 "hidden": true 인 도구도 목록에 보인다.
# 다른 PC 에서:  ssh -L 8701:localhost:8701 <user>@<server-ip>   →  브라우저 http://localhost:8701
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
OLLAMA_PORT="${OLLAMA_PORT:-11436}"
export CUDA_VISIBLE_DEVICES="${AGENT_GPUS:-0,1,2,3}" CUDA_DEVICE_ORDER=PCI_BUS_ID  # GPU 고정 배정 없음 — 도구가 그때그때 여유 많은 GPU 를 고름
curl -s -m 2 127.0.0.1:$OLLAMA_PORT/api/version >/dev/null || { [ -x ~/.local/ollama-gpu/start.sh ] && ~/.local/ollama-gpu/start.sh; }
export LLM_API=ollama LLM_BASE_URL=http://127.0.0.1:$OLLAMA_PORT LLM_MODEL="${LLM_MODEL:-gemma4:31b}" MODEL="${LLM_MODEL:-gemma4:31b}" VISION_MODEL="${LLM_MODEL:-gemma4:31b}" AGENT_DATA="$PWD/_data"
cd agent-page-portal && PORT=8701 HOST=127.0.0.1 SHOW_HIDDEN=1 nohup python3 portal.py > portal-test.log 2>&1 &
echo "test portal pid $! → http://localhost:8701 (SSH 터널로 접속)"
