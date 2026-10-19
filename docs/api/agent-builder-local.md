# 내 에이전트 빌더 API (`agent-builder-local`)
지시문·예시 질문·지식 자료(HWP·PDF 등)로 업무 에이전트를 만들고, 나만 쓰거나 부서장 승인을 받아 우리 부서와 같이 쓰는 도구. 에이전트마다 서버를 띄우지 않고 이 서비스 하나가 설정을 읽어 답한다.
포털 경로 접두어: `/t/agent-builder-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/agent-builder-local/api/agents`)
OpenAPI 3.1: [`openapi/agent-builder-local.json`](openapi/agent-builder-local.json)

> 포털은 GET·POST 만 넘기므로 고치기·지우기도 POST(`…/delete` 등)다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 권한 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지, 문서 엔진·포털 DB 읽기 가능 여부 | 아니오 | 누구나 |
| GET | `api/me` | 나(이름·부서·역할)·승인 대기 수·안 읽은 피드백 수 | 아니오 | 본인 |
| GET | `api/templates` | 템플릿 5종 | 아니오 | 누구나 |
| GET | `api/agents?tab=mine\|dept\|fav` | 목록(내가 만든 것·부서 공유·즐겨찾기) | 아니오 | 볼 수 있는 것만 |
| POST | `api/agents` | 새 에이전트(초안) | 아니오 | 누구나(만든 계정 기록) |
| GET | `api/agents/{id}` | 상세 | 아니오 | 볼 수 있는 사람(아니면 404) |
| POST | `api/agents/{id}` | 설정 고치기(자동 저장) | 아니오 | 만든 사람·관리자(403) |
| POST | `api/agents/{id}/publish` | 게시 — 나만 / 우리 부서(승인 요청) | 아니오 | 만든 사람·관리자 |
| POST | `api/agents/{id}/duplicate` | 복제(초안·나만, 자료 포함) | 아니오 | 만든 사람·관리자 · 볼 수 있는 부서 공개 에이전트 |
| POST | `api/agents/{id}/delete` | 지우기(자료·피드백 포함) | 아니오 | 만든 사람·관리자 |
| POST | `api/agents/{id}/docs` | 자료 올리기 → 읽기·색인 | 예 — **SSE** | 만든 사람·관리자 |
| POST | `api/agents/{id}/docs/{doc}/delete` | 자료 빼기 | 아니오 | 만든 사람·관리자 |
| GET | `api/agents/{id}/docs/{doc}/file` | 원문 파일 | 아니오 | 볼 수 있는 사람 |
| GET | `api/agents/{id}/chunk?id=` | 근거 조각 + 앞뒤 | 아니오 | 볼 수 있는 사람 |
| POST | `api/agents/{id}/chat` | 질문 → 인용 달린 답 | 예 — LLM, **SSE** | test: 만든 사람·관리자 / real: 볼 수 있는 사람 |
| GET | `api/agents/{id}/convs` | 내 지난 대화 목록 | 아니오 | 본인 것만 |
| GET | `api/agents/{id}/convs/{conv}` | 지난 대화 하나 | 아니오 | 본인 것만(남의 것 404) |
| POST | `api/agents/{id}/convs/{conv}/delete` | 지난 대화 지우기 | 아니오 | 본인 |
| POST | `api/agents/{id}/fav` | 즐겨찾기 켜기·끄기 | 아니오 | 본인 |
| POST | `api/agents/{id}/helpful` | 도움됨 기록 | 아니오 | 볼 수 있는 사람 |
| POST | `api/agents/{id}/feedback` | 틀림 알리기 | 아니오 | 볼 수 있는 사람 |
| GET | `api/feedback` | 받은 피드백 | 아니오 | 만든 사람(자기 것)·관리자(전부) |
| POST | `api/feedback/{fid}/done` | 처리함 표시 | 아니오 | 만든 사람·관리자 |
| GET | `api/approvals` | 내가 승인할 부서 공개 요청 | 아니오 | 부서장·관리자 |
| POST | `api/approvals/{id}` | 승인·거절 | 아니오 | 그 부서 부서장(만든 사람 본인 제외, 없으면 관리자)·관리자 |
| GET | 그 밖의 경로 | 화면(`ui.html`) | | |

## 권한·공유 규칙
- 사람 구분은 포털이 붙이는 `X-Agent-User` 하나. `owner` = 관리자.
- **부서·부서장**은 같은 서버 포털 DB(`_data/portal.db` 의 `users.dept`·`dept_heads`)를 **읽기 전용**으로 조회(포털 코드 변경 없음).
- 에이전트 설정·자료는 **공유 데이터**(만든 계정 기록). 고치기·지우기·자료 올리기·시험 대화는 만든 사람·관리자만(403). 응답엔 계정 id 대신 `can_edit`·`owner_name`.
- **볼 수 있는 사람**: 만든 사람·관리자, 부서 공개가 **승인된** 뒤 그 부서 사람, 승인 대기 중이면 그 부서 승인자(검토용). 그 밖에는 404(있는지도 알리지 않음).
- 부서원에게는 `instructions`·`only_docs`·`cite`·`length` 를 내보내지 않는다(대화에 필요한 이름·설명·예시 질문·자료 목록만).
- 대화 기록·즐겨찾기는 사용자 폴더(`WORKSPACE/u/<id>/`)에 본인만.
- 틀림 알리기는 만든 사람과 관리자 **둘 다**의 `api/feedback` 에 나온다.
- 공개 범위 `all`(전체)은 2차 — 지금은 400.

## 엔드포인트

### GET api/me
```json
{"name": "김동주", "dept": "인공지능응용연구실", "admin": false, "heads": [], "role": "일반", "model": "gemma4:31b",
 "max_upload_mb": 50, "exts": [".hwp", ".hwpx", ".pdf", ".docx", ".xlsx", ".xls", ".txt", ".md"], "counts": {"approvals": 0, "feedback": 1}}
```

### POST api/agents
- 요청 `{"template": "blank|rules|faq|review|meeting", "name": "선택"}` → 201 상세(`AbAgent`). 초안(`status: "draft"`, `scope: "me"`).

### GET api/agents/{id} — 상세
```json
{"id": "5a498f636d", "name": "연구비 집행 도우미(예시)", "desc": "…", "icon": "", "examples": ["…"], "status": "published", "scope": "me",
 "scope_label": "나만", "review": null, "docs": [{"id": "…", "name": "예시_집행지침.pdf", "kind": "pdf", "size": 68077, "pages": 1, "chunks": 4, "embedded": true, "created": "…"}],
 "can_edit": true, "can_approve": false, "mine": true, "owner_name": "내가 만듦", "fav": false, "created": "…", "updated": "…",
 "instructions": "…", "only_docs": true, "cite": true, "length": "short", "template": "rules"}
```
- `review`(부서 공개 요청): `{"dept", "status": "pending|approved|rejected", "requested", "decided", "by_name", "note"}`

### POST api/agents/{id} — 고치기
- 보낸 필드만: `name`(60) `desc`(140) `icon`(2) `instructions`(6000) `examples`(글 목록, 8개·200자) `only_docs`·`cite`(참/거짓) `length`(`short|mid|long`). 형식 틀림 400.

### POST api/agents/{id}/publish
- `{"scope": "me"}` → 바로 게시(나만). `{"scope": "dept"}` → 만든 사람 부서로 승인 요청(`review.status: "pending"`). 관리자가 게시하면 바로 승인(관리자는 `dept` 로 부서 지정). 만든 사람 부서가 없으면 400. 이미 같은 부서로 대기·승인 중이면 그대로.

### POST api/agents/{id}/docs — SSE
- 요청 `{"file_name": "지침.hwpx", "file_b64": "…"}` 또는 `{"text": "붙여 넣은 글", "file_name": "선택"}`.
- 스트림 전 오류: 확장자·base64·빈 파일 400, 파일 하나 50MB 초과·본문 상한 초과 413(본문을 버려 읽은 뒤), 권한 403.
- 이벤트: `{"stage": "parse|embed", "msg": "…"}` → `{"done": AbDoc}` 또는 `{"error": "…"}`.
- kordoc `--format chunks` 로 쪽·제목 단위 조각 → 같은 쪽·같은 절끼리 약 900자로 묶음 → 임베딩(bge-m3). 임베딩 서버가 없으면 `embedded: false`(BM25 만).

### POST api/agents/{id}/chat — SSE
- 요청 `{"question": "…", "mode": "real|test", "conv": "이어 갈 대화 id(real)", "history": [{"q", "a"}](test)}`
- `test`: 만든 사람·관리자만(403), 기록 안 남음, 사용 횟수에 안 셈. `real`: 게시 전이면 만든 사람만(409), 대화가 본인 폴더에 저장되고 `done.conv` 로 이어 감.
- 이벤트: `{"cites": [AbCite…], "mode": "hybrid|bm25|none"}` → `{"token": "…"}`… → `{"done": {"answer", "cites", "used", "mode", "conv"}}` 또는 `{"error": "…"}`.
- `AbCite`: `{"n", "chunk", "doc", "doc_name", "page", "heading", "quote"}` — `used` 는 답에 실제로 쓴 [n].
- 프롬프트: 이름·설명·지시문 + 규칙(자료로만 답·없으면 "올린 자료에서 찾지 못했습니다."·[n] 인용·답 길이·지시문 무시 요청 거부) + 상위 6개 조각. `only_docs` 이고 찾은 조각이 없으면 LLM 을 부르지 않는다.

### GET api/agents/{id}/chunk?id={chunk}
- `{"doc", "doc_name", "kind", "page", "heading", "text", "before", "after"}` — 근거 패널(앞뒤 300자). 원문은 `docs/{doc}/file`(PDF 는 `#page=n`).

### POST api/agents/{id}/feedback
- 요청 `{"question", "answer", "cites": [AbCite…](선택), "memo": "1000자"}` → 201 `{"id"}`.

### GET api/feedback
- `{"feedback": [{"id", "agent", "agent_name", "reporter_name", "question", "answer", "cites", "memo", "ts", "done", "mine"}]}` — 만든 사람은 자기 에이전트 것, 관리자는 전부(`mine: false` 면 남의 것).

### GET api/approvals · POST api/approvals/{id}
- 목록 `{"pending": [{"id", "name", "desc", "owner_name", "dept", "requested", "docs", "instructions", "examples"}]}`
- 결정 `{"ok": true|false, "note": "선택"}` → `{"review": …}`. 승인자가 아니면 403, 볼 수 없으면 404, 대기 중 아니면 409.

## 오류
`{"error": "한국어 설명"}` — 400 잘못된 본문(JSON 아님·객체 아님·형식), 403 권한, 404 없음·볼 수 없음, 409 상태, 413 크기, 500 "서버 내부 오류"(자세한 것은 server.log). SSE 도중 오류는 HTTP 200 + `error` 이벤트. 응답에 서버 경로·내부 주소·계정 id 를 넣지 않는다.

## 화면만의 것
- 자동 저장(입력 0.7초 뒤 `POST api/agents/{id}`), 여러 파일은 하나씩 차례로 올림, 근거 [n] 누르면 `chunk` 로 패널 표시, 목록 탭 기억(localStorage `abTab`).

## 모두의 에이전트 연동 (2026-10-19)
- `GET api/stats` — 참여 현황(수만): `{agents, creators, shared, new_7d, asks_7d, top_creators: [{name, dept, agents, uses_7d}]}`. 제작자 순위는 **부서에 공개(승인)한 에이전트만** 셈 — 비공개 에이전트의 제목·내용은 나가지 않음.
- `POST api/agents/<id>/duplicate` — 내 에이전트뿐 아니라 **내가 볼 수 있는 부서 공개 에이전트**도 내 초안(나만)으로 복제(자료 포함). 볼 수 없으면 404, 공개 안 된 남의 것은 403.
- 카드(`api/agents`)에 `created`, `dept`(부서 공개 승인이면 그 부서) 추가.
- 복제 허용(`allow_fork`, 기본 참): 만든 사람이 편집기 공개 범위 옆 체크로 끔 → 남의 `duplicate` 는 403 "만든 사람이 복제를 허용하지 않았습니다"(내 것·관리자는 됨). 카드·상세에 `allow_fork`, 지시문 본문은 여전히 남에게 안 보임.
- 복제 기록: 복제본 `agent.json` 의 `forked_from {id, name, owner, at, docs_copied}`. 남의 부서 공개 에이전트를 복제하면 **지시문·설정만** 복사하고 자료 파일은 복사하지 않음(내 것·관리자는 자료까지). 카드·상세에 `forks`(수), `forked_from`(원본 — 볼 수 있을 때만 id), 상세에 `forks_list`(내가 볼 수 있는 복제본만).
- `api/stats` 를 관리자(포털 서버, X-Agent-User=owner)가 부르면 `creators_detail[{uid, name, dept, agents, uses_30d, forks_30d}]` 이 더 붙음 — 포털 `/api/community/ranking` 이 리뷰·요청과 합쳐 점수를 냄(화면에는 계정 id 안 나감).
