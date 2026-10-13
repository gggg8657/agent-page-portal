# 글쓰기 도우미 API (`writer-local`)
건배사·경조사·축사·공지·사과문 같은 상황별 글을 "패턴"(입력 칸 + 지시문)으로 로컬 LLM 에게 쓰게 하는 도구.
포털 경로 접두어: `/t/writer-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/writer-local/api/generate`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/patterns` | 패턴 목록(입력 칸 정의 포함) | 아니오 | 아니오 |
| GET | `api/models` | LLM 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/runs` | 지난 생성 목록(최근 50개) | 아니오 | 예 |
| GET | `api/runs/<run_id>` | 지난 생성 하나 | 아니오 | 예 |
| POST | `api/generate` | 패턴으로 글 생성 (SSE) | 예 — LLM 1콜, 수 초~수십 초 | 예 (저장) |
| GET | `api/humanize_ok` | humanize-kr-local 연결 여부 | 아니오 | 아니오 |
| POST | `api/humanize` | 글을 humanize-kr-local 로 윤문 (한 번에) | 예 — LLM, 수십 초~수 분 | 예(그쪽 기록에 저장) |
| GET | `api/polish_ok` | kordoc-local 윤문 연결 여부 | 아니오 | 아니오 |
| POST | `api/polish` | 글을 kordoc-local 로 윤문 (한 번에) | 예 — LLM | 아니오 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/patterns
- 응답: 패턴 배열(지금 17개). 지시문 본문(`prompt`)은 빠져 있음.
```json
[{"name": "apology", "title": "사과문", "category": "공문", "description": "실수·지연·사고에 대한 사과",
  "outputs": 2,
  "inputs": [{"key": "what", "label": "무슨 일이 있었나", "type": "textarea", "required": true},
             {"key": "form", "label": "형식", "type": "select", "options": ["이메일", "메신저", "공식 문서"]}]}]
```
- `inputs[].type`: `text` · `textarea` · `select`(`options` 사용) · `number`. 그 밖의 키: `required`, `placeholder`.
- `outputs`: 몇 개의 안(variant)을 만드는지.
- 메모: 패턴은 서버 `patterns/*.md` 파일에서 기동 시 한 번 읽음. API 로 추가·수정 불가.

### GET api/models
- 응답: 문자열 배열. 오류 시 500 `{"error": "..."}`. 서버 기본 모델은 API 로 주지 않음(`model` 을 비워 보내면 기본값).

### GET api/runs
- 응답: `[{"run_id": "2026-10-07-142530-a1", "title": "사과문", "model": "gemma4:31b", "ts": "...", "head": "첫 안 앞 40자"}]`

### GET api/runs/<run_id>
- `run_id` 형식 `YYYY-MM-DD-HHMMSS-xx`(16진 2자리). 응답은 `api/generate` 의 `done` 객체와 같음. 없으면 404 `{"error": "없음"}`.

### POST api/generate
- 요청 (JSON)

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `pattern` | string | 예 | 패턴 `name` (예 `apology`) |
| `values` | object | 아니오 | `{입력 key: 값}`. 빈 칸은 `○○○` 로 채워 보냄. `required` 칸이 비면 오류 |
| `model` | string | 아니오 | 비우면 서버 기본 |
| `tweak` | string | 아니오 | 다시 쓰기 지시: `shorter`(절반 길이) · `formal`(더 격식) · `casual`(더 캐주얼). `prev` 와 함께일 때만 적용 |
| `prev` | string | 아니오 | 고칠 직전 결과 글(안 하나) |

- 응답: `text/event-stream`, `data: <JSON>\n\n`
  - `{"stage": "llm", "msg": "모델 · 패턴 제목", "reset": true}` → `{"token": "..."}`… → `{"done": {결과}}`
  - 오류: `{"error": "ValueError: 필수 입력: 사과 대상"}` / `"ValueError: 패턴 없음: ..."` (HTTP 는 200)
- 결과 객체: `run_id`, `pattern`, `title`, `model`, `values`, `tweak`, `variants`(안 배열, 최대 `outputs` 개), `raw`(모델 출력 전체 — 여러 안이면 `=====` 줄로 구분됨), `ts`
- 메모: 토큰 스트림은 여러 안이 `=====` 로 이어진 원문 그대로. 안별로 나눈 결과는 `done.variants` 에서 받기. 모델이 빈 출력을 내면 1회 자동 재시도(그때 토큰이 다시 흐름).

### GET api/humanize_ok · POST api/humanize
- `api/humanize_ok` → `{"ok": true|false}` (humanize-kr-local 서버가 응답하는지)
- `api/humanize` 요청 `{"text": "글", "genre": "essay"(선택)}` → `{"output": "윤문된 글"}` (스트리밍 없이 끝날 때 한 번)
- 오류: 502 `{"error": "RuntimeError: ..."}`
- 메모: 내부에서 humanize-kr-local `api/humanize` 를 끝까지 기다림(수십 초~수 분). 같은 사용자 헤더를 넘기므로 humanize 쪽 실행 기록에도 남음. 게이트 결과·비교 등 자세한 값이 필요하면 humanize-kr-local API 를 직접 부를 것([humanize-kr-local.md](humanize-kr-local.md)).

### GET api/polish_ok · POST api/polish
- `api/polish_ok` → `{"ok": true|false}`
- `api/polish` 요청 `{"text": "...", "strength": "light|standard|strong"}` → kordoc-local `api/polish_text` 응답 그대로(`output`, `changes`, `rejected`, `units`, ...). [kordoc-local.md](kordoc-local.md) 참고.
- 오류: 502 `{"error": "..."}`

## 흐름 예
```js
const base = '/t/writer-local/';
const pats = await fetch(base + 'api/patterns').then(r => r.json());
const p = pats.find(x => x.name === 'apology');
// 입력 칸은 p.inputs 로 그리기
const r = await fetch(base + 'api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({pattern: p.name, values: {what: '납품 지연', to: '고객사'}})});
// SSE 읽기는 humanize-kr-local 문서의 예와 같음 → done.variants 를 카드로 보여 주기

// "더 짧게": 고른 안을 prev 로
await fetch(base + 'api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({pattern: p.name, values, tweak: 'shorter', prev: done.variants[0]})});
```

## 화면만의 것 / 주의
- 경로는 정확히 일치해야 함 → 쿼리를 붙이면 화면 HTML 이 돌아옴.
- 화면이 하는 것: 패턴 `inputs` 로 입력 폼 만들기, 안(variant) 탭 전환, 복사.
- `api/humanize`·`api/polish` 는 옆 도구(humanize-kr-local·kordoc-local)를 대신 불러 주는 중계. 그 도구가 꺼져 있으면 `*_ok` 가 false.
- 생성 기록은 사용자 폴더에 JSON 파일로 저장. 정적 파일 경로 없음.
