# AtomSlide 발표자료 API (`atomslide-local`)
주제·문서로 발표 슬라이드 초안을 만들고(LLM) 편집해 PPTX·PDF 로 내보내는 **AtomSlide**(오픈소스 Presenton 기반, 제출자 제공 초안)를 포털 관문 뒤에 둔 것. 명세: `openapi/atomslide-local.json`(관문이 넘기는 주요 경로만).

포털 경로 접두어: `/t/atomslide-local/` (예: `api/v1/ppt/presentation/all` → `http://<서버>/t/atomslide-local/api/v1/ppt/presentation/all`)

## 구조
- 관문(`atomslide-local/app.py`, 127.0.0.1:8817) → 앱 컨테이너(Presenton FastAPI + Next.js, 내부 전용 망·인터넷 없음). LLM 은 호스트 vLLM 을 중계로만 부름.
- **사용자 분리**: 포털 사용자(서명된 `X-Agent-User`)마다 관문이 Presenton 계정을 자동으로 만들고 서버 쪽에서 로그인한 세션 쿠키를 붙임. 브라우저는 Presenton 계정·쿠키를 보지 않음. 남의 발표자료·파일은 404.
- **막는 경로(403)**: Presenton 자체 계정·관리 `api/v1/auth/*`(status·verify 빼고)·`api/v1/admin/*`·`api/v1/webhook/*`·`api/v2/*`·`mcp`·`docs`·`openapi.json`·화면 `admin`·`community`. `api/v1/auth/logout` 은 아무 일 안 함(200).
- 화면(Next.js)은 basePath `/t/atomslide-local` 로 빌드됨 — 새 화면에 끼워 넣을 때는 iframe 으로 `/t/atomslide-local/` 를 연다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `healthz` | 관문·앱(FastAPI·Next.js)·LLM 중계 상태(서명 없이). 앱이 안 뜨면 503 | - | - |
| GET | `api/v1/ppt/presentation/all` | 내 발표자료 목록 | - | 예 |
| GET·DELETE | `api/v1/ppt/presentation/{id}` | 하나 보기·지우기(남의 것 404) | - | 예 |
| POST | `api/v1/ppt/presentation/generate` | `{content, n_slides, language, template, export_as}` → 한 번에 만들고 내보내기(동기) | LLM, 3장 약 1.5분 | 예 |
| POST | `api/v1/ppt/presentation/{id}/export` | `{export_as: pptx\|pdf}` → `{path}` | 수 초~수십 초 | 예 |
| GET | `app_data/exports/users/<uid>/<파일>` | 내보낸 파일(앞의 `path` 에 접두어만 붙임) | - | 예 |
| GET | `__gate/whoami` · `__gate/status` | 관문이 본 사용자 · (관리자만) 연결된 계정 수·중계 상태 | - | - |
| GET | `/`, `dashboard`, `upload`, `outline`, `presentation?id=` | 화면 | - | 예 |

화면은 위 외에도 Presenton 의 단계별 API(목차 SSE `api/v1/ppt/outlines/stream/{id}`, 슬라이드 SSE `api/v1/ppt/presentation/stream/{id}`, 파일 올리기 `api/v1/ppt/files/upload`, Next.js `api/export-presentation` 등)를 그대로 씀 — 모양은 Presenton 코드(atomslide-local/vendor)가 기준.

## 템플릿·언어
- 한국어 연구 발표: `template: "research_kr"`, `language: "Korean"`. 기본 템플릿은 `general`.
- 웹 검색·이미지 생성·외부 공급자는 꺼 둠(폐쇄망).

## 오류
- 서명 없음·틀림·v1 서명 → 401 `{"error", "code": "unauthenticated"}`
- 앱 준비 중·연결 실패 → 503/502 `{"error", "code": "app_unavailable"}` (내부 주소·경로는 감춤)
- 막은 기능 → 403 `{"error", "code": "forbidden"}`
