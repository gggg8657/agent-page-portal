# SQL 질의 API (`sql-local`)
자연어 질문 → 로컬 LLM 이 SQL(JSON) 생성 → 안전 필터(조회만) → 실행 → 표 + 서버가 만든 SVG 차트 + CSV.
포털 경로 접두어: `/t/sql-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/sql-local/api/ask`)

서버는 Python 표준 `http.server` 단일 파일(`app.py`). 경로는 **정확히 일치**해야 한다. GET 에 쿼리스트링을 붙이면 화면 HTML 이, POST 는 `404 {"error": "없는 경로"}` 가 돌아온다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | LLM 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/schema` | 연결된 DB 의 테이블·열·FK + 스키마 설명 | 아니오 | 아니오 (DB·설명 공유) |
| POST | `api/notes` | 스키마 설명 저장(LLM 프롬프트에 들어감) | 아니오 | **공유** |
| POST | `api/ask` | 질문 → SQL 생성·실행 (SSE) | **예 (LLM 1~2회, 수 초~수십 초)** | 예 (기록 저장) |
| POST | `api/rerun` | 고친 SQL 직접 실행 (LLM 없음) | 아니오 (쿼리 최대 10초) | 예 (기록 저장) |
| GET | `api/runs` | 내 최근 기록 50개 | 아니오 | 예 |
| GET | `api/runs/<run_id>` | 기록 하나(표·SVG 포함) | 아니오 | 예 |
| GET | `api/runs/<run_id>.csv` | 기록 결과 CSV 내려받기 | 아니오 | 예 |
| GET | 그 밖의 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 공통 자료형: 결과(run)
`api/ask` 의 `done`, `api/rerun` 응답, `api/runs/<id>` 가 모두 이 모양이다.
```json
{
  "run_id": "2026-10-13-ab12",
  "question": "부서별 과제 예산 합계를 큰 순서로",
  "model": "qwen3:8b",
  "explanation": "부서별로 예산을 더해 내림차순 정렬",
  "chart": {"type": "bar", "x": "부서", "y": "합계"},
  "sql": "SELECT ... LIMIT 500",
  "error": null,
  "repaired": false,
  "columns": ["부서", "합계"],
  "rows": [["연구1부", 1200000000]],
  "truncated": false,
  "svg": "<svg xmlns=...>...</svg>",
  "ts": "2026-10-13T09:12:30"
}
```
| 필드 | 설명 |
|---|---|
| `sql` | 실제 실행한 SQL(필터가 `LIMIT 500` 을 덧붙였을 수 있음). 실패 시 LLM 이 쓴 원래 SQL, LLM 이 SQL 을 못 냈으면 `null` |
| `error` | 실패 사유(문자열) 또는 `null`. **SQL 실행 실패도 HTTP 오류가 아니라 이 필드로 온다** |
| `repaired` | 첫 SQL 이 실패해 오류를 넣고 1회 재생성했는지 |
| `chart` | LLM 이 고른 차트 `{type, x, y}`. `type` 은 `bar`·`line`·`pie`(그 밖·없음 = 표만). 키가 없을 수도 있음 |
| `rows` | 최대 500행. 값은 DB 자료형 그대로(숫자·문자열·null; JSON 으로 못 바꾸는 값은 문자열) |
| `truncated` | 500행에 닿으면 `true`(정확히 500행이어도 `true`) |
| `svg` | 서버가 만든 차트 SVG 문자열(720×340 viewBox, 최대 40개 점). x·y 열이 없거나 y 가 숫자가 아니면 `null` |
| `model` | `api/rerun` 결과는 `null`, `question` 은 `"(수동 SQL)"` |

## 엔드포인트

### GET api/models
- 응답: `["qwen3:8b", "gemma4:31b"]` (LLM 서버 모델 이름 그대로)
- 오류: LLM 서버 연결 실패 시 `500 {"error": "..."}`
- 메모: 서버 기본 모델은 화면 HTML 의 `%MODEL%` 자리에만 들어간다. 요청에서 `model` 을 비우면 서버 기본 모델.

### GET api/schema
- 응답:
  ```json
  {"db": "<DB 경로 또는 접속 URL>", "demo": true, "kind": "sqlite",
   "tables": [{"name": "논문", "cols": [["id", "INTEGER"], ["과제id", "INTEGER"], ["제목", "TEXT"]], "fks": ["과제id → 연구과제.id"]}],
   "notes": {"논문": {"_": "발표 논문", "IF": "임팩트 팩터"}}}
  ```
  | 필드 | 설명 |
  |---|---|
  | `db` | 서버 환경변수 `DB_URL` 그대로(sqlite 파일 경로 또는 `postgresql://…`/`mysql://…`). 화면은 마지막 경로 조각만 보여 줌 |
  | `demo` | 동봉 가상 샘플 DB(`sample/research.db`)면 `true` |
  | `kind` | `sqlite`·`postgresql`·`mysql` |
  | `tables[].cols` | `[열 이름, 자료형]` 쌍 배열 |
  | `tables[].fks` | `"열 → 테이블.열"` 문자열(sqlite 만, 나머지는 빈 배열) |
  | `notes` | 스키마 설명 `{테이블: {"_": 테이블 설명, <열>: 열 설명}}` |
- 오류: DB 파일이 없으면 `404 {"error": "없음"}`(FileNotFoundError), 드라이버 없음 등 `500`.

### POST api/notes
- 요청: 본문 = notes 객체 그대로 (`{테이블: {"_": "...", "<열>": "..."}}`). **통째로 덮어쓴다**(부분 병합 아님). 지금 화면은 `Content-Type` 없이 보내며 서버도 헤더를 보지 않는다.
- 응답: `{"ok": true}`
- 오류: JSON 아님 등 `500 {"error": "..."}`
- 메모: 다음 질문부터 LLM 프롬프트의 스키마 옆에 주석으로 붙는다.

### POST api/ask
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `question` | 문자열 | 예 | 자연어 질문(비면 `400 {"error": "질문이 비어 있음"}`) |
  | `model` | 문자열 | 아니오 | 비우면 서버 기본 모델 |
  | `history` | `[{question, sql, head}]` | 아니오 | 이어 묻기용 직전 문답. 서버는 **마지막 3개**만 쓴다. `head` 는 결과 머리 요약 문자열(선택) |
- 응답: `200 text/event-stream; charset=utf-8`, `data: <JSON>\n\n` (이벤트 이름 없음)
  ```
  data: {"stage": "llm", "msg": "qwen3:8b SQL 생성"}
  data: {"token": "{\"sql\": \"SELECT"}
  ...
  data: {"stage": "run", "msg": "쿼리 실행"}
  data: {"stage": "repair", "msg": "오류 수정 재생성: ..."}     ← 실패했을 때만
  data: {"done": { ...결과(run)... }}
  ```
  | 이벤트 | 설명 |
  |---|---|
  | `{"stage", "msg"}` | `llm`(생성 시작) · `run`(실행) · `repair`(오류 넣고 재생성) |
  | `{"token": "..."}` | LLM 원문 조각(JSON 텍스트) |
  | `{"done": <run>}` | 끝. SQL 실패·답할 수 없는 질문도 `done` 으로 오며 `error` 필드에 사유 |
  | `{"error": "..."}` | LLM 연결 실패, LLM 이 JSON 을 안 냄 등 파이프라인 자체 실패 |
- 메모: 결과는 성공·실패 모두 사용자 기록에 저장된다. 쿼리 실행 제한 10초(sqlite 만 강제 중단 — 아래 주의).

### POST api/rerun
- 요청: `{"sql": "SELECT ...", "chart": {"type": "bar", "x": "부서", "y": "합계"}}` (`chart` 선택)
- 응답: 결과(run) JSON(스트리밍 아님). 새 `run_id` 로 저장.
- 오류: 안전 필터 위반·SQL 오류·시간 초과는 모두 `500 {"error": "ValueError: SELECT/WITH 로 시작하는 조회만 허용"}` 식.
- 메모: 차트 종류·축만 바꿔 다시 그릴 때도 화면은 이걸 부른다(쿼리를 다시 실행함).

### GET api/runs
- 응답(파일명 역순 = 대략 최신 먼저, 최대 50):
  ```json
  [{"run_id": "2026-10-13-ab12", "question": "...", "ts": "...", "error": null, "n": 5}]
  ```
  `n` = 결과 행 수.

### GET api/runs/<run_id>
- 응답: 결과(run) JSON. 없으면 `404 {"error": "없음"}`.

### GET api/runs/<run_id>.csv
- 응답: `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename*=UTF-8''<run_id>.csv`. UTF-8 BOM 포함(엑셀 한글 깨짐 방지), 첫 줄은 열 이름.

## 안전 필터 (서버가 강제)
- 주석 제거 후 문장 하나만(`;` 로 여러 문장 불가), `SELECT` 또는 `WITH` 로 시작해야 함.
- 금지어(단어 단위, 대소문자 무시): `insert update delete drop alter create replace truncate pragma attach detach vacuum grant revoke exec execute copy into outfile load_file`. 열·함수 이름이 이 단어와 같아도 막힌다(예: SQL 함수 `replace()`).
- `LIMIT 숫자` 가 없으면 `LIMIT 500` 을 붙임. 있어도 최대 500행만 가져옴.
- sqlite 는 읽기 전용(`mode=ro`)으로 연다.

## 흐름 예

### 1) 질문하기
```js
const BASE = '/t/sql-local/';
const history = [];
const r = await fetch(BASE + 'api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ question: '부서별 과제 예산 합계', model: '', history }) });
if (!r.ok) throw new Error((await r.json()).error);
const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
for (;;) {
  const { value, done } = await reader.read(); if (done) break;
  buf += dec.decode(value, { stream: true });
  let i; while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data: ')) continue;
    const ev = JSON.parse(line.slice(6));
    if (ev.stage) showStage(ev.stage, ev.msg);
    if (ev.error) throw new Error(ev.error);
    if (ev.done) {
      const j = ev.done;
      if (j.error) showError(j.error, j.sql);
      else {
        renderTable(j.columns, j.rows);
        chartBox.innerHTML = j.svg || '';
        csvLink.href = BASE + 'api/runs/' + j.run_id + '.csv';
        history.push({ question: j.question, sql: j.sql, head: j.columns.join(',') + ' / ' + (j.rows[0] || []).join(',') });
      }
    }
  }
}
```

### 2) SQL 직접 고쳐 다시 실행 / 차트 바꾸기
```js
const j = await fetch(BASE + 'api/rerun', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ sql: editedSql, chart: { type: 'line', x: '월', y: '집행액' } }) }).then(r => r.json());
if (j.error) alert(j.error); else chartBox.innerHTML = j.svg || '';
```

## 화면만의 것 / 주의
- 표 그리기, 차트 다크 테마 색 바꾸기(SVG 안 `text/line/path…` 색을 화면이 덮어씀), 이어 묻기 `history` 관리(성공한 문답만 쌓음)는 화면 몫.
- **스키마 설명(`api/notes`)과 DB 는 모든 사용자 공유**: 설명은 사용자 폴더가 아니라 기본 작업 폴더의 `schema_notes.json` 한 곳에 저장되고, 로그인한 누구나 덮어쓸 수 있다(owner 검사 없음). 질문 기록(`runs/`)만 `X-Agent-User` 별로 나뉜다(`WORKSPACE/u/<id>/runs/`).
- **`api/schema` 의 `db` 는 `DB_URL` 원문**이다. PostgreSQL/MySQL 을 `postgresql://user:비번@host/db` 로 붙이면 비밀번호가 응답에 그대로 나간다 — 외부 DB 를 연결할 때는 주의(화면에 보여 주지 말 것).
- 10초 실행 제한은 sqlite 에서만 실제로 쿼리를 끊는다(진행 핸들러). PostgreSQL/MySQL 은 타이머만 돌고 중단 코드가 없다.
- 기록 삭제 API 는 없다. 정적 파일 경로 없음. 모든 응답에 `X-Author` 헤더.
