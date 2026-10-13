#!/bin/bash
# LLM 서버 감시(systemd·cron 없는 컨테이너용) — vLLM(:11440, 채팅) + Ollama(:11436, bge-m3 임베딩·되돌리기용)
#   scripts/llm-watchdog.sh &      # autostart.sh 가 로그인 때마다 부름. 잠금(flock)으로 한 개만 돈다(이미 돌면 바로 끝남)
# 1분마다 vLLM /health · Ollama /api/version 확인:
#   - 프로세스가 없으면 바로 띄움(같은 서버를 10분 안에 두 번은 안 띄움 — 실패 반복 방지)
#   - 프로세스는 있는데 연속 FAILS(3)번 응답 없으면: pid 파일의 그 PID(명령줄 확인)와 자식만 끄고 다시 띄움
#   - 막 띄운 vLLM 은 모델 적재(1~3분) 동안 응답이 없으므로 GRACE(15분) 동안은 기다림
# 끄기(감시만 멈춤, 서버는 그대로): kill $(cat /tmp/agent-page-llm-watchdog.pid)
# vLLM 을 일부러 꺼 두려면(예: Ollama 로 되돌림): touch ~/.local/vllm/DISABLED  — 있으면 vLLM 은 건드리지 않음(Ollama 감시는 계속)
# env: VLLM_DIR(~/.local/vllm, start.sh·vllm.pid) OLLAMA_DIR(~/.local/ollama-gpu, start.sh·ollama.pid) VLLM_PORT(11440) OLLAMA_PORT(11436)
#      OLLAMA_GPUS(0,1,2 — vLLM 전용 GPU 3 은 뺌) INTERVAL(60) FAILS(3) GRACE(900) WATCHDOG_LOG(<상위폴더>/llm-watchdog.log)
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
LOCKF="${WATCHDOG_LOCK:-/tmp/agent-page-llm-watchdog}"
exec 9>"$LOCKF.lock"; flock -n 9 || exit 0
echo $$ > "$LOCKF.pid"

VLLM_DIR="${VLLM_DIR:-$HOME/.local/vllm}" OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/ollama-gpu}"
VLLM_PORT="${VLLM_PORT:-11440}" OLLAMA_PORT="${OLLAMA_PORT:-11436}"
INTERVAL="${INTERVAL:-60}" FAILS="${FAILS:-3}" GRACE="${GRACE:-900}" MIN_GAP="${MIN_GAP:-600}"
LOG="${WATCHDOG_LOG:-$PWD/llm-watchdog.log}"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
trim_log() { [ -f "$LOG" ] && [ "$(stat -c %s "$LOG")" -gt 1000000 ] && { tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; }; }

# pid 파일의 PID 가 살아 있고 명령줄에 $2 와 $3 이 다 들어 있을 때만 그 PID 를 출력(남의 프로세스를 건드리지 않게)
owned_pid() {
  local pid; pid="$(cat "$1" 2>/dev/null)"
  [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ] || return 1
  local cmd; cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
  [[ "$cmd" == *"$2"* && "$cmd" == *"$3"* ]] && echo "$pid"
}
descendants() { ps -eo pid=,ppid= | awk -v r="$1" '{p[$1]=$2} END{for(k in p){x=k; while(x in p && x!=r && x>1) x=p[x]; if(x==r && k!=r) print k}}'; }
listening() { local h; h="$(printf ':%04X' "$1")"; awk -v h="$h" '$4=="0A" && substr($2, length($2)-4)==h' /proc/net/tcp /proc/net/tcp6 2>/dev/null | grep -q .; }
age() { ps -o etimes= -p "$1" 2>/dev/null | tr -d ' '; }
stop_tree() {   # 정확한 PID + 그 자식(vLLM EngineCore 등) — TERM 후 30초 기다리고 남으면 KILL
  local pid="$1" kids; kids="$(descendants "$pid")"
  kill "$pid" 2>/dev/null
  for _ in $(seq 30); do [ -d "/proc/$pid" ] || break; sleep 1; done
  for k in $pid $kids; do [ -d "/proc/$k" ] && kill -9 "$k" 2>/dev/null; done
  sleep 3
}

vfail=0 ofail=0 vlast=0 olast=0
log "watchdog 시작 pid $$ (vLLM :$VLLM_PORT, Ollama :$OLLAMA_PORT, ${INTERVAL}s 간격, 연속 ${FAILS}회 실패 시 재기동)"
while :; do
  now=$(date +%s)
  # ---- vLLM ----
  if [ -f "$VLLM_DIR/DISABLED" ]; then vfail=0
  elif curl -sf -m 10 -o /dev/null "127.0.0.1:$VLLM_PORT/health"; then
    [ "$vfail" -ge 1 ] && log "vLLM 응답 회복"; vfail=0
  else
    pid="$(owned_pid "$VLLM_DIR/vllm.pid" 'vllm serve' "--port $VLLM_PORT")"
    if [ -z "$pid" ]; then
      if listening "$VLLM_PORT"; then vfail=$((vfail + 1)); [ "$vfail" -eq "$FAILS" ] && log "vLLM :$VLLM_PORT 을 우리 pid 파일에 없는 프로세스가 잡고 응답 없음 — 손대지 않음"
      elif [ $((now - vlast)) -ge $MIN_GAP ]; then
        log "vLLM 프로세스 없음 → 기동"; vlast=$now; vfail=0
        "$VLLM_DIR/start.sh" 9>&- >> "$LOG" 2>&1
      fi
    elif [ "$(age "$pid")" -lt "$GRACE" ]; then :   # 적재 중
    else
      vfail=$((vfail + 1)); log "vLLM(pid $pid) /health 실패 $vfail/$FAILS"
      if [ "$vfail" -ge "$FAILS" ] && [ $((now - vlast)) -ge $MIN_GAP ]; then
        log "vLLM(pid $pid) 재기동"; stop_tree "$pid"; vlast=$(date +%s); vfail=0
        "$VLLM_DIR/start.sh" 9>&- >> "$LOG" 2>&1
      fi
    fi
  fi
  # ---- Ollama (임베딩) ----
  if curl -sf -m 5 -o /dev/null "127.0.0.1:$OLLAMA_PORT/api/version"; then
    [ "$ofail" -ge 1 ] && log "Ollama 응답 회복"; ofail=0
  else
    ofail=$((ofail + 1)); log "Ollama /api/version 실패 $ofail/$FAILS"
    pid="$(owned_pid "$OLLAMA_DIR/ollama.pid" 'ollama' 'serve')"
    if [ -z "$pid" ] && listening "$OLLAMA_PORT"; then
      [ "$ofail" -eq "$FAILS" ] && log "Ollama :$OLLAMA_PORT 을 우리 pid 파일에 없는 프로세스가 잡고 응답 없음 — 손대지 않음"
    elif { [ -z "$pid" ] || [ "$ofail" -ge "$FAILS" ]; } && [ $((now - olast)) -ge $MIN_GAP ]; then
      if [ -n "$pid" ]; then log "Ollama(pid $pid) 재기동"; stop_tree "$pid"; else log "Ollama 프로세스 없음 → 기동"; fi
      olast=$(date +%s); ofail=0
      OLLAMA_GPUS="${OLLAMA_GPUS:-0,1,2}" "$OLLAMA_DIR/start.sh" 9>&- >> "$LOG" 2>&1
    fi
  fi
  trim_log
  sleep "$INTERVAL" & wait $!   # kill 이 바로 먹게
done
