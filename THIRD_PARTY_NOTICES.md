# THIRD_PARTY_NOTICES — agent-page-portal

포털 서버(`portal.py`)는 **파이썬 표준 라이브러리만** 씁니다(설치 패키지 없음). 아래는 포털·사용 설명서가 쓰거나 부르는 제3자 구성요소입니다.
연결된 각 도구의 라이브러리·모델·데이터는 각 도구 저장소의 `THIRD_PARTY_NOTICES.md` 를 보세요.

| 이름 | 버전 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|---|
| Python 표준 라이브러리 | 3.x | 실행 | PSF-2.0 | https://docs.python.org/3/license.html | 가능 | |
| Noto Sans KR | — | 외부 서비스(Google Fonts CDN) — `guide/index.html` 글꼴 | SIL OFL 1.1 | https://fonts.google.com/noto/specimen/Noto+Sans+KR | 가능 | 동봉 안 함. 열 때 Google 서버 접속 |
| Ollama | — | 외부 프로그램(도구들이 부름, 포털은 환경변수만 넘김) | MIT | https://github.com/ollama/ollama | 가능 | 동봉 안 함 |
| Gemma 4 `gemma4:31b` | — | 기본 LLM 이름을 도구에 넘김 | Apache-2.0 (Ollama 매니페스트 기준) | https://ai.google.dev/gemma | 가능 | 동봉 안 함 |
| Playwright / playwright-core | 1.63.0 | 개발 도구 — 사용 설명서 화면 캡처·PDF 생성(`guide/tools/`, `node_modules` 는 커밋 제외) | Apache-2.0 | https://github.com/microsoft/playwright | 가능 | 받는 Chromium 은 자체 라이선스 |
| Pillow | 12.1.1 | 개발 도구 — 설명서 그림 처리(`guide/tools/build.py`) | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow | 가능 | |
| nvidia-smi | — | 외부 CLI(GPU 상태 표시) | NVIDIA 독점 | https://developer.nvidia.com | 드라이버 약관 | 동봉 안 함 |

## 확인 필요
- [#1](https://github.com/gggg8657/agent-page-portal/issues/1) **사용 설명서 그림·연결 도구의 제3자 권리**
  - 캐릭터 파동이 그림(`guide/img/padong-local-*`): KAERI·허쉬위쉬 권리, 이용 허락 미확인(padong-local#1). 이 공개 저장소에 커밋하지 말 것.
  - persona·avatar 화면의 실사풍 생성 얼굴: Realistic Vision(CreativeML OpenRAIL-M) 사용 제한, 초상권(persona-local#1, avatar-local#2).
  - 연결 도구의 비상업 요소: Open-Meteo 무료 API(padong-local#2), SadTalker 가중치 미확인(avatar-local#1), GPL ffmpeg(avatar-local#5). portrait-local(InsightFace 비상업, portrait-local#1)은 2026-10-06 포털에서 뺌.
  - `guide/agent-portal.pdf` 는 모든 그림을 담으므로 `.gitignore` 로 제외.
