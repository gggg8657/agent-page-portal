# 포털 공통 환경변수 — start-portal.sh(운영 :8700)와 start-fastportal.sh(시험 :8720 등)가 같이 읽는다(source).
# 포털은 도구를 켤 때 자기 환경을 그대로 물려주므로, LLM·임베딩·음성 연결 값은 여기 한 곳에서만 고친다.
# LLM 은 vLLM(OpenAI 호환, 기본 :11440, 전용 GPU 1장, 이름 gemma4:31b), 임베딩은 로컬 Ollama(기본 :11436, bge-m3). 데이터: <상위폴더>/_data
_PE_TOP="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd)"   # agent-page-portal/scripts → 상위 폴더(도구들을 나란히 clone 한 곳)
OLLAMA_PORT="${OLLAMA_PORT:-11436}"
VLLM_PORT="${VLLM_PORT:-11440}"
export CUDA_VISIBLE_DEVICES="${AGENT_GPUS:-0,1,2}" CUDA_DEVICE_ORDER=PCI_BUS_ID PATH="$HOME/.local/bin:$PATH"  # 쓸 GPU 후보(도구가 그때그때 여유 많은 것을 고름) — vLLM 전용 GPU(여기선 3)는 뺌 · ffmpeg·python3.11
export LLM_API=openai LLM_BASE_URL=http://127.0.0.1:$VLLM_PORT/v1 LLM_MODEL="${LLM_MODEL:-gemma4:31b}" MODEL="${LLM_MODEL:-gemma4:31b}" VISION_MODEL="${LLM_MODEL:-gemma4:31b}" NUM_CTX="${NUM_CTX:-32768}" AGENT_DATA="$_PE_TOP/_data"  # NUM_CTX = vLLM --max-model-len 과 같게(도구 입력 상한 계산용) — vLLM 값 바꾸면 같이
export EMBED_API=ollama EMBED_BASE_URL=http://127.0.0.1:$OLLAMA_PORT EMBED_MODEL=bge-m3  # notebook 임베딩 — vLLM 채팅 서버엔 /v1/embeddings 가 없음
# 되돌리기(Ollama 로): 위 LLM export 를 아래 줄로 바꾸고 AGENT_GPUS 기본을 0,1,2,3 으로, 포털·도구 재시작
#   export LLM_API=ollama LLM_BASE_URL=http://127.0.0.1:$OLLAMA_PORT LLM_MODEL="${LLM_MODEL:-gemma4:31b}" MODEL="${LLM_MODEL:-gemma4:31b}" VISION_MODEL="${LLM_MODEL:-gemma4:31b}" NUM_CTX="${NUM_CTX:-32768}" AGENT_DATA="$_PE_TOP/_data"  # NUM_CTX = Ollama OLLAMA_CONTEXT_LENGTH
# 도구끼리 연결: 음성 합성(tts) · 받아쓰기(meeting STT) · 립싱크(avatar), GPU 사용
export TTS_BASE_URL=http://127.0.0.1:8771/v1 STT_BASE_URL=http://127.0.0.1:8767/v1 AVATAR_URL=http://127.0.0.1:8777/api/run
export TTS_VOICE=KR TTS_MODEL=melo TTS_DEVICE=cuda DEVICE=cuda WHISPER_DEVICE=cuda
# React 화면(agent-page-web 빌드)을 포털이 같은 출처 /app 아래로 내보냄(fastportal WEB_DIST·WEB_PREFIX). 값은 링크 — agent-page-web/scripts/deploy.sh 가
# 새 빌드를 _web/releases/<시각> 에 두고 이 링크를 원자적으로 바꿔 끼우므로 포털 재시작 없이 새 화면이 나간다. 끄기: WEB_DIST= (빈 값)로 포털 재시작
[ -z "${WEB_DIST+x}" ] && [ -e "$_PE_TOP/_web/app" ] && export WEB_DIST="$_PE_TOP/_web/app"
# '/' 를 React 로 — 2026-10-14 전환(운영 켬). 루트 빌드는 agent-page-web/scripts/deploy.sh --root 가 _web/root 에 둠.
# 되돌리기: 아래 줄 앞에 # 를 붙이고 start-portal.sh(예전 포털 화면이 바로 돌아옴). 절차: agent-page-web/CUTOVER.md
export WEB_ROOT=1 WEB_ROOT_DIST="$_PE_TOP/_web/root"
unset _PE_TOP
