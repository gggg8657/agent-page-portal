#!/bin/bash
# 본인 테스트용 포털 (:8701, 이 서버 안에서만 열림) — tools.json 에서 "hidden": true 인 도구도 목록에 보인다.
# 다른 PC 에서:  ssh -L 8701:localhost:8701 <user>@<server-ip>   →  브라우저 http://localhost:8701
# 운영(:8700)과 같은 fastportal·같은 DB — 로그인·권한 규칙도 같음(예전 portal.py 는 로컬 요청을 토큰 없이 관리자로 봐서 바꿈).
# PORTAL_RECOVER=0: 이 포털을 다시 띄워도 운영에서 진행 중인 흐름·제출 검증을 '멈춤'으로 바꾸지 않음.
#   start-test-portal.sh [stop]
PORT=8701 HOST=127.0.0.1 SHOW_HIDDEN=1 PORTAL_RECOVER=0 exec "$(dirname "$0")/start-fastportal.sh" "$@"
