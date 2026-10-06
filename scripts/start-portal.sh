#!/bin/bash
# 에이전트 페이지 포털 기동 — 로컬 Ollama(기본 :11436, gemma4:31b)를 모든 도구에 넘긴다. 데이터: <상위폴더>/_data
# 이 파일은 상위 폴더(도구들을 나란히 clone 한 곳)의 start-portal.sh 사본입니다. 상위 폴더에서 실행해도 되고 여기서 실행해도 됩니다.
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
OLLAMA_PORT="${OLLAMA_PORT:-11436}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2,3}" PATH="$HOME/.local/bin:$PATH"  # 쓸 GPU 번호 · ffmpeg·python3.11
curl -s -m 2 127.0.0.1:$OLLAMA_PORT/api/version >/dev/null || { [ -x ~/.local/ollama-gpu/start.sh ] && ~/.local/ollama-gpu/start.sh; }  # Ollama 가 꺼져 있으면 띄움(자기 환경에 맞게)
export LLM_API=ollama LLM_BASE_URL=http://127.0.0.1:$OLLAMA_PORT LLM_MODEL="${LLM_MODEL:-gemma4:31b}" MODEL="${LLM_MODEL:-gemma4:31b}" VISION_MODEL="${LLM_MODEL:-gemma4:31b}" AGENT_DATA="$PWD/_data"
# 도구끼리 연결: 음성 합성(tts) · 받아쓰기(meeting STT) · 립싱크(avatar), GPU 사용
export TTS_BASE_URL=http://127.0.0.1:8771/v1 STT_BASE_URL=http://127.0.0.1:8767/v1 AVATAR_URL=http://127.0.0.1:8777/api/run
export TTS_VOICE=KR TTS_MODEL=melo TTS_DEVICE=cuda DEVICE=cuda WHISPER_DEVICE=cuda
cd agent-page-portal && nohup python3 portal.py > portal.log 2>&1 &
echo "portal pid $! → http://$(hostname -I | awk '{print $1}'):8700"
