#!/bin/bash
# 포털과 tools.json 의 모든 도구를 같은 상위 폴더에 나란히 clone (이미 있으면 git pull).
#   mkdir agent-page && cd agent-page && git clone https://github.com/gggg8657/agent-page-portal && bash agent-page-portal/scripts/clone-all.sh
cd "$(dirname "$0")/../.."
GH="${GH:-https://github.com/gggg8657}"
for d in agent-page-portal $(python3 -c "import json;[print(t['dir']) for t in json.load(open('agent-page-portal/tools.json'))]"); do
  if [ -d "$d/.git" ]; then git -C "$d" pull --ff-only; else git clone "$GH/$d.git" "$d"; fi
done
ln -sfn agent-page-portal portal   # audit.sh 등은 portal/ 이름으로 찾는다
echo "다음: 각 도구 폴더의 README 대로 의존성 설치(bash <도구>/setup.sh) → bash agent-page-portal/scripts/start-portal.sh"
