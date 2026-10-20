# 프롬프트 다듬기 API (`promptlab-local`)
거친 프롬프트를 규칙 점검표로 진단하고, LLM 으로 구조 템플릿(역할·배경·할 일·제약·출력 형식·예시)으로 다듬고, 원래·다듬은 것을 로컬 LLM 에 나란히 돌려 비교합니다. 사용자별 보관함(판·내보내기). 명세: `openapi/promptlab-local.json`.

포털 경로 접두어: `/t/promptlab-local/` (예: `api/analyze` → `http://<서버>/t/promptlab-local/api/analyze`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM 상태·안내 글, 점검 항목, 대상 모델, 출력 형식, 제한 | LLM 확인(60초 기억) | - |
| POST | `api/analyze` | 규칙 점검표(7항목·0~2점·근거·고칠 점, 100점, 팁) | - (LLM 안 씀) | - |
| POST | `api/rewrite` | 구조 템플릿으로 다듬기 + 낱말 비교 + 점수 전후 | LLM 수 초~수십 초(없으면 규칙 틀) | - |
| POST | `api/diff` | 두 글 낱말 비교 | - | - |
| POST | `api/run` | A/B 실행 시작 → 202 작업 | LLM 2n(+판단 1)번 | 작업(메모리) |
| GET | `api/run/{job}` | A/B 상태·결과(본인 것만) | - | 예 |
| GET | `api/tips?target=` | 대상 모델 팁(gemma·small·large) | - | - |
| GET·POST | `api/library` | 보관함 목록 · 저장(새 항목 또는 `id` 로 새 판) | - | 예 |
| GET | `api/library/{id}` | 항목 하나(모든 판) | - | 예 |
| GET | `api/library/{id}/export?fmt=md\|txt&v=` | 내려받기(attachment) | - | 예 |
| POST | `api/library/{id}/delete` · `api/library/{id}/versions/{v}/delete` | 지우기 | - | 예 |
| GET | `/` | 예전 화면(`ui.html`) | - | - |

## 점검표 (`api/analyze`)
- 요청 `{prompt, purpose?, target?: gemma|small|large, format?}`. 항목 `role`·`context`·`task`·`constraints`·`format`·`examples`·`ambiguity`(흐린 말이 적을수록 2점).
- 표현을 찾아 보는 결정론 규칙 — 같은 입력이면 같은 결과. `found` 는 근거로 찾은 표현, `note` 는 "규칙 점검" 안내(LLM 이 없으면 그 사실 포함).
- 목적·형식 칸에만 적고 본문에 없으면 "모델은 본문만 봅니다"라고 알려 줌.

## 다듬기 (`api/rewrite`)
- LLM 메타 프롬프트: 사용자 프롬프트·목적은 `<<<PROMPT>>> … <<<END_PROMPT>>>`·`<<<META>>>` 구분자 안 **데이터**(구분자 흉내·제어문자 지움), 그 안의 지시는 따르지 않음. `response_format: json_object` 로 `{role, context, task, constraints[], format, examples[], input_slot, questions[]}` → 서버가 `# 역할 / # 배경 / # 할 일 / # 제약 / # 출력 형식 / # 예시 (/ # 입력)` 로 묶음.
- 원문에 없는 사실은 자리표시(`[대상 독자]`)로. `questions` 는 사용자에게 물어볼 점(최대 3).
- LLM 이 없거나 `mode: "rules"` 면 규칙 틀(원문 = 할 일, 빠진 항목 = 자리표시), `mode: "rules"`·`note` 로 표시.

## A/B 실행 (`api/run`)
- 요청 `{original, improved, input?, n: 1~3, judge?, purpose?}`. 두 프롬프트를 **그대로** 사용자 메시지로 보냄(시험이 목적). `input` 은 다듬은 프롬프트의 입력 칸(`[여기에 처리할 자료를 붙여 넣으세요]`)에 넣고, 칸이 없으면 `<<<입력>>>` 구분자로 뒤에 붙임. 표본마다 두 쪽을 동시에 부름.
- 표본: `text`·`latency_ms`(서버에서 잰 시간)·`prompt_tokens`·`completion_tokens`(LLM usage, 없으면 추정·`tokens_estimated`)·`finish`. 한 표본이 실패하면 그 표본만 `error`.
- `judge` 면 마지막에 AI 판단: 목적·형식·지어낸 사실·간결성 1~10점, 두 결과 순서를 무작위로 바꿔 묻고 되돌림, `label` "AI 판단 — 참고용". 결과 글은 구분자 안 데이터.
- `warnings`: 다듬은 프롬프트에 남은 `[ ]` 자리표시, 입력 칸이 있는데 시험 입력이 빈 경우.
- 작업은 메모리에만(1시간, 서버 재시작 시 사라짐). 남의 작업은 404.

## 보관함
- `WORKSPACE/u/<id>/library.json`(owner 는 WORKSPACE 바로 아래). 항목 `{id, title, purpose, target, format, tags[], created, updated, versions[{v, ts, prompt, improved, score, note}]}` — 항목 300개, 판 50개(넘으면 오래된 판부터).
- 내보내기 md = 정보 + 다듬은 프롬프트 + 원래 프롬프트, txt = 다듬은 글(없으면 원문)만. 파일 이름 `<제목>_v<판>.md` (`filename*` UTF-8).
- **에이전트 빌더로 보내기**는 이 도구 API 가 아니라 화면이 사용자 권한으로 빌더 API 를 부름: `POST /t/agent-builder-local/api/agents {template: "blank", name}` → `POST /t/agent-builder-local/api/agents/{id} {instructions, desc, only_docs: false, cite: false}` → 빌더 편집 화면.

## 제한·오류
- 사용자당 AI 작업(다듬기·A/B) 동시 1(409 `busy`)·10분 20번(429 `rate_limited`, `Retry-After`; A/B 는 n + 판단 1만큼 셈). 도구 전체 LLM 동시 2, 호출 시간 제한 120초, A/B 출력 800토큰, 다듬기 1500토큰.
- LLM 이 없으면 `api/run` 503 `unavailable`, `api/rewrite` 는 규칙 틀. LLM 오류(시간 초과·JSON 아님)는 502 `llm_error`.
- 입력 오류 400 `bad_request`(프롬프트 8000자·시험 입력 6000자), 없는 항목·작업 404, 본문 256KB 초과 413.
