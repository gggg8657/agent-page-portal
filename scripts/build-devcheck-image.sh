#!/usr/bin/env bash
# 개발자 업로드 바탕 이미지 dongjukim-devcheck:<판> 만들기(격리 자가검증 + 승인된 도구 컨테이너가 같이 씀 — sdk/devtool/Dockerfile).
#   bash scripts/build-devcheck-image.sh [판]      # 기본 판 = fastportal/devtool.py 의 BASE_TAG(DEVCHECK_IMAGE 기본값과 같음)
# 폐쇄망: 인터넷 되는 곳에서 만든 뒤  docker save dongjukim-devcheck:<판> | gzip > devcheck.tar.gz  →  docker load < devcheck.tar.gz
# 환경변수: DOCKER_HOST(tcp://127.0.0.1:2375) · PY_IMAGE(바탕 파이썬 이미지, 고정 digest 기본)
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export DOCKER_HOST="${DOCKER_HOST:-tcp://127.0.0.1:2375}"
TAG="${1:-$(sed -n 's/^BASE_TAG = "\(.*\)".*/\1/p' fastportal/devtool.py)}"
[ -n "$TAG" ] || { echo "판(태그)을 못 찾음"; exit 1; }
ARGS=(--label "dongjukim.devcheck=base" -t "dongjukim-devcheck:$TAG")
[ -n "${PY_IMAGE:-}" ] && ARGS+=(--build-arg "PY_IMAGE=$PY_IMAGE")
docker build "${ARGS[@]}" sdk/devtool
docker run --rm --name "dongjukim-devcheck-probe-$$" --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --entrypoint python "dongjukim-devcheck:$TAG" -c 'import sys; compile(open("/opt/devtool/entry.py").read(), "entry.py", "exec"); print("python", sys.version.split()[0])'
echo "이미지: dongjukim-devcheck:$TAG"
