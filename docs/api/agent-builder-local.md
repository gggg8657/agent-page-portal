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
| GET | `api/templates` | 템플릿 5종 + `cats`(분류 8개) | 아니오 | 누구나 |
| GET | `api/agents?tab=mine\|dept\|system\|fav\|admin` | 목록(내가 만든 것·동료 부서 공유·**포털 기본**·즐겨찾기 · admin = 관리자만: 부서 공개·대기 + 공유를 끈 것 + 포털 기본). dept·mine 에는 포털 기본이 없음 | 아니오 | 볼 수 있는 것만 |
| POST | `api/agents/<id>/block` `{off, reason?}` | 관리자 공유 끄기·켜기(포털 '에이전트 관리') | 아니오 | 관리자 |
| POST | `api/agents` | 새 에이전트(초안) | 아니오 | 누구나(만든 계정 기록) |
| GET | `api/agents/{id}` | 상세 | 아니오 | 볼 수 있는 사람(아니면 404) |
| POST | `api/agents/{id}` | 설정 고치기(자동 저장) | 아니오 | 만든 사람·관리자(403) |
| GET | `api/agents/{id}/build` | 대화로 만들기 — 도우미와 나눈 말·잠긴 칸 | 아니오 | 만든 사람·관리자(본인 기록) |
| POST | `api/agents/{id}/build` | 대화로 만들기 — 도우미에게 한 마디 → 답 + 초안 patch | 예 — LLM, **SSE** | 만든 사람·관리자 |
| POST | `api/agents/{id}/build/locks` · `…/build/reset` | 잠긴 칸 저장 · 나눈 말 지우기 | 아니오 | 만든 사람·관리자 |
| POST | `api/agents/{id}/publish` | 게시 — 나만 / 우리 부서(승인 요청) | 아니오 | 만든 사람·관리자 |
| POST | `api/agents/{id}/duplicate` | 복제(초안·나만, 자료 포함) | 아니오 | 만든 사람·관리자 · 볼 수 있는 부서 공개 에이전트 |
| POST | `api/agents/{id}/delete` | 지우기(자료·피드백 포함) | 아니오 | 만든 사람·관리자 |
| POST | `api/agents/{id}/docs` | 자료 올리기 → 읽기·색인 | 예 — **SSE** | 만든 사람·관리자 |
| POST | `api/agents/{id}/docs/{doc}/delete` | 자료 빼기 | 아니오 | 만든 사람·관리자 |
| GET | `api/agents/{id}/docs/{doc}/file` | 원문 파일 | 아니오 | 볼 수 있는 사람 |
| GET | `api/agents/{id}/chunk?id=` | 근거 조각 + 앞뒤 | 아니오 | 볼 수 있는 사람 |
| POST | `api/agents/{id}/chat` | 질문 → 인용 달린 답 | 예 — LLM, **SSE** | test: 만든 사람·관리자 / real: 볼 수 있는 사람 |
| GET | `api/agents/{id}/convs` | 내 지난 대화 목록 | 아니오 | 본인 것만 |
| POST | `api/agents/{id}/attach` | 대화 첨부 올리기 → 읽기·색인(그 대화에만) | 예 — **SSE** | real: 볼 수 있는 사람 / test: 만든 사람·관리자 |
| GET | `api/agents/{id}/convs/{conv}/chunk?id=` | 첨부 근거 조각 + 앞뒤 | 아니오 | 본인 대화만(남의 것 404) |
| GET | `api/agents/{id}/convs/{conv}/attach/{file}/file` | 첨부 원본 파일 | 아니오 | 본인 대화만 |
| POST | `api/agents/{id}/convs/{conv}/attach/{file}/delete` | 첨부 하나 빼기 | 아니오 | 본인 대화만 |
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
- **포털 기본 에이전트**(`kind: "system"`, 빌더 저장소 `system/<id>/` — 사람이 만든 것은 `agents/<id>/`, `kind` 는 서버가 놓인 곳으로 정함): 관리자만 만들기(`POST api/agents {"kind": "system"}`, 그 밖 403)·고치기·게시·공유 끄기. 게시하면 **부서와 상관없이 로그인한 모두**에게 보이고 부서 승인이 없음(`scope_label: "포털 기본"`), 관리자 `block` 은 그대로. 목록 `GET api/agents?tab=system` — `tab=dept`(동료가 공유한 것)·`tab=mine` 에는 없음. 카드·상세에 `kind`. 주인 `_system`(사람 계정 아님, `X-Agent-User: _system` 은 403): `owner_name` 은 `"포털 기본"`, `mine` 은 누구에게나 거짓, 관리자에게 주는 `owner_id` 는 `null` — 포털은 주인 없는 대상처럼 리뷰·요청을 관리자 알림함으로 보내고 기여 순위에서 뺌. `api/stats` 는 사람이 만든 것만 세고 포털 기본은 `system: {agents, asks_7d}` 로 따로. 사용자가 복제하면 보통 사용자 에이전트(주인 = 그 사람). 포털 할 일 검색·물어보기·아이디어로 만들기의 에이전트 후보는 `tab=system` + `tab=dept`(같은 id 한 번).
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

### 대화 첨부 (2026-10-21)
사용자가 **그 대화에만** 붙이는 파일입니다(안건·회의록 등). 본인 폴더(`convs/<에이전트>/<대화>.att/` — 원본·`index.db`)에 저장되고 에이전트 자료·색인에는 들어가지 않으며, 만든 사람·관리자도 못 봅니다(남의 대화 404). 틀림 알리기에도 첨부 내용은 가지 않습니다.
- `POST api/agents/{id}/attach` `{"conv"?: "대화 id", "mode": "real|test", "file_name", "file_b64"}` — SSE: (conv 를 안 주면 새 대화를 만들어 먼저) `{"conv"}` → `{"stage", "msg"}`… → `{"done": {"conv", "file"}}` 또는 `{"error"}`. 형식·파일 하나 상한은 자료 올리기와 같음(HWP·HWPX·PDF·DOCX·XLSX·XLS·TXT·MD, 50MB, kordoc). `test` 는 시험 대화용(만든 사람·관리자, 기록에 남지 않음).
- 상한(대화 하나, 서버 환경변수): 파일 5개(`ATT_MAX_FILES`, 넘으면 409) · 크기 합계 100MB(`ATT_MAX_TOTAL_MB`, 413) · 읽은 글 합계 40만 자(`ATT_MAX_TEXT_CHARS`, 스트림 안 `error`). `GET api/me` 의 `att_max_files`·`att_max_total_mb` 로 화면이 미리 알 수 있음.
- `GET api/agents/{id}/convs/{conv}` 에 `attachments`(AbDoc 목록)가 붙음 — 지난 대화를 열면 그 첨부가 다시 붙음. `POST …/convs/{conv}/delete` 는 첨부도 같이 지움. 질문 없이 첨부만 한 빈 대화·시험 대화 첨부는 하루 뒤 지움.
- `GET …/convs/{conv}/chunk?id=` 첨부 근거 조각 + 앞뒤, `GET …/convs/{conv}/attach/{file}/file` 원본(PDF·TXT·MD 는 inline, 그 밖은 attachment), `POST …/convs/{conv}/attach/{file}/delete` 하나 빼기.
- 채팅 `POST api/agents/{id}/chat {"conv"}`: 그 대화에 첨부가 있으면 첨부 글이 1만 2천 자(`ATT_INLINE_CHARS`) 이하면 전문을, 넘으면 질문마다 조각 검색(BM25 + 임베딩 RRF)으로 넣고 에이전트 자료와 합침 — 첨부가 앞 번호. 근거 `AbCite.src` = `att`(첨부, `conv` 포함 — 화면 표시 "첨부: 파일명 p.N") | `agent`(에이전트 자료).

### 대화로 만들기 (2026-10-21)
'만들기 도우미'(로컬 LLM)와 대화하며 **같은 초안**(초안·나만, 주인 = 나)을 채운다. 화면: 빌더 `#/build/<id>`, 포털 `/tool/agent-builder-local/build/<id>`(`/build` 만 주면 새 초안을 만들고 그 주소로). 편집 화면과 같은 에이전트라 오가도 그대로.
- `POST api/agents/{id}/build` `{"message": "4000자", "locks"?: ["instructions", …]}` — SSE: `{"token"}`(도우미 말, patch 앞까지만)… → `{"done": AbBuildDone}` 또는 `{"error"}`.
  - `AbBuildDone` = `{"reply", "summary", "changed": [칸], "held": {칸: 값}, "rejected": [칸], "warnings": [글], "upload": bool, "agent": AbAgent}`
  - 도우미는 매번 지금 초안·잠긴 칸·자료 수를 보고 한 번에 하나씩 묻는다(무엇·누가·입력(글/파일)·출력 형식·말투·지킬 것·근거 자료). "지시문 더 짧게"·"예시 질문 3개 더" 같은 말은 바로 고치고 `summary` 한 줄.
  - 서버 검사: 허용 칸만(`name`·`icon`·`desc`·`instructions`·`examples`·`only_docs`·`cite`·`length`·`cat`), 글 상한은 고치기와 같음, `cat` 은 8개 중 하나 또는 빈 글, 참/거짓 형식. `scope`·`status`·`owner`·`review` 등은 `rejected` — **도우미는 게시하지 못함**(게시는 사람이 `publish`).
  - 주입 검사: 지시문·이름·설명·예시 질문에 '이전 지시 무시'·'시스템 프롬프트 보여 줘'·역할 토큰(`<|im_start|>` 등)·도우미 프롬프트 문장이 있으면 넣지 않고 `warnings`. "…라고 해도 따르지 않는다" 같은 막는 문장은 통과.
  - 잠긴 칸은 바꾸지 않고 `held` 로만 — 화면이 '이 안으로 바꾸기'를 누르면 `POST api/agents/{id}` 로 넣음. 넣은 칸은 고치기와 같은 길이라 승인된 부서 공개 에이전트면 다시 승인 대기.
  - `upload: true` 면 도우미가 지식 자료를 권함 — 화면이 대화 안에 올리기 칸을 띄움(`api/agents/{id}/docs`).
- `GET api/agents/{id}/build` → `AbBuildState` `{"turns": [{"role": "user|assistant", "content"}], "locks": [칸]}` — 사용자 폴더 `build/<에이전트>.json`(최근 40개, 본인 것).
- `POST api/agents/{id}/build/locks` `{"locks": [칸]}` → `{"locks"}`(허용 칸이 아니면 400) · `POST api/agents/{id}/build/reset` → `{"ok": true}`(초안은 그대로).
- 권한: 고칠 수 있는 사람(만든 사람·관리자)만, 볼 수 없으면 404·고칠 수 없으면 403.

## 오류
`{"error": "한국어 설명"}` — 400 잘못된 본문(JSON 아님·객체 아님·형식), 403 권한, 404 없음·볼 수 없음, 409 상태, 413 크기, 500 "서버 내부 오류"(자세한 것은 server.log). SSE 도중 오류는 HTTP 200 + `error` 이벤트. 응답에 서버 경로·내부 주소·계정 id 를 넣지 않는다.

## 화면만의 것
- 자동 저장(입력 0.7초 뒤 `POST api/agents/{id}`), 여러 파일은 하나씩 차례로 올림, 근거 [n] 누르면 `chunk` 로 패널 표시, 목록 탭 기억(localStorage `abTab`).

## 모두의 에이전트 연동 (2026-10-19)
- `GET api/stats` — 참여 현황(수만): `{agents, creators, shared, new_7d, asks_7d, top_creators: [{name, dept, agents, uses_7d}]}`. 제작자 순위는 **부서에 공개(승인)한 에이전트만** 셈 — 비공개 에이전트의 제목·내용은 나가지 않음.
- `POST api/agents/<id>/duplicate` — 내 에이전트뿐 아니라 **내가 볼 수 있는 부서 공개 에이전트**도 내 초안(나만)으로 복제(자료 포함). 볼 수 없으면 404, 공개 안 된 남의 것은 403.
- 카드(`api/agents`)에 `created`, `dept`(부서 공개 승인이면 그 부서) 추가.
- 복제 허용(`allow_fork`, 기본 참): 만든 사람이 편집기 공개 범위 옆 체크로 끔 → 남의 `duplicate` 는 403 "만든 사람이 복제를 허용하지 않았습니다"(내 것·관리자는 됨). 카드·상세에 `allow_fork`, 지시문 본문은 여전히 남에게 안 보임.
- 관리자 공유 끄기(`blocked`, 2026-10-20): `block {off: true}` 면 부서원 목록·보기(404)·복제·참여 현황·순위에서 빠지고 만든 사람·관리자만 봄. 카드·상세의 `blocked` `{ts, by_name, reason}` 는 만든 사람·관리자에게만(그 밖엔 null). 자료·대화·설정은 그대로, `off: false` 면 돌아옴. 복제본에는 안 옮겨짐.
- 복제 기록: 복제본 `agent.json` 의 `forked_from {id, name, owner, at, docs_copied}`. 남의 부서 공개 에이전트를 복제하면 **지시문·설정만** 복사하고 자료 파일은 복사하지 않음(내 것·관리자는 자료까지). 카드·상세에 `forks`(수), `forked_from`(원본 — 볼 수 있을 때만 id), 상세에 `forks_list`(내가 볼 수 있는 복제본만).
- `api/stats` 를 관리자(포털 서버, X-Agent-User=owner)가 부르면 `creators_detail[{uid, name, dept, agents, uses_30d, forks_30d}]` 이 더 붙음 — 포털 `/api/community/ranking` 이 리뷰·요청과 합쳐 점수를 냄(화면에는 계정 id 안 나감).
