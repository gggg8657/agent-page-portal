#!/bin/bash
# 컨테이너 재시작(재부팅) 후 첫 로그인 셸에서 한 번: Ollama + 포털 + tools.json 의 모든 도구(숨김 포함).
# ~/.zshrc 끝에서 백그라운드로 부른다. 이미 떠 있으면 아무것도 안 함, 셸이 여러 개 떠도 잠금으로 한 번만.
cd "$(dirname "$0")/../.."   # agent-page-portal/scripts → 상위 폴더
exec 9>/tmp/agent-page-autostart.lock; flock -n 9 || exit 0
curl -s -m 2 localhost:8700/api/tools >/dev/null && exit 0
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2,3}" PATH="$HOME/.local/bin:$PATH"
{
  echo "== $(date '+%F %T') autostart"
  agent-page-portal/scripts/start-portal.sh
  for i in $(seq 60); do curl -s -m 2 localhost:8700/api/tools >/dev/null && break; sleep 1; done
  python3 -c "import json;[print(t['dir']) for t in json.load(open('agent-page-portal/tools.json'))]" | while read -r d; do
    [ -d "$d" ] && curl -s -X POST localhost:8700/api/start -d "{\"dir\":\"$d\"}" >/dev/null && echo "start $d"
  done
} >> autostart.log 2>&1
