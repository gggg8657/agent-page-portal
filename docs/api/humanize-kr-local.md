# Humanize KR API (`humanize-kr-local`)
AI가 쓴 한국어 글의 번역투·상투구를 걷어내는 윤문 도구. 로컬 LLM 으로 진단 → 윤문 → 결정적 복원 → 4축 게이트를 돈다.
포털 경로 접두어: `/t/humanize-kr-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/humanize-kr-local/api/humanize`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | LLM 서버에 있는 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/runs` | 지난 실행 목록(최근 50개) | 아니오 | 예 |
| GET | `api/runs/<run_id>` | 지난 실행 하나의 전체 결과 | 아니오 | 예 |
| POST | `api/humanize` | 윤문 실행 (SSE 스트림) | 예 — LLM 1~3콜, 수십 초~수 분 | 예 (결과 저장) |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/models
- 요청: 없음
- 응답: 문자열 배열. 예 `["gemma4:31b", "qwen3:8b", "bge-m3:latest"]`
  - Ollama 백엔드면 `/api/tags` 의 `name`, OpenAI 호환이면 `/models` 의 `id`. 임베딩 모델도 섞여 나오므로 화면에서 걸러야 함.
- 오류: LLM 서버에 닿지 못하면 500 `{"error": "URLError: ..."}`
- 메모: 기본 모델 이름은 API 로 주지 않음. 지금 화면은 서버가 `ui.html` 의 `%MODEL%` 자리에 넣어 주는 값을 씀(새 화면이면 목록 첫 항목이나 사용자가 고른 값을 쓰거나 `model` 을 비워 보내 서버 기본값 사용).

### GET api/runs
- 요청: 없음
- 응답: 최근 것부터 최대 50개
```json
[{"run_id": "2026-10-07-1352", "route": "light", "model": "gemma4:31b",
  "gate": "OK", "gate_code": 0, "ts": "2026-10-07T06:24:04", "head": "입력 앞 40자"}]
```
- 메모: 결과 파일(`result.json`)이 있는 실행만 나옴(도중 실패한 실행은 빠짐).

### GET api/runs/<run_id>
- 경로: `run_id` 는 `YYYY-MM-DD-xxxx`(xxxx = 16진 4자리) 형식만 받음. 다르면 화면 HTML 이 돌아옴(404 아님).
- 응답: `api/humanize` 의 `done` 객체와 같은 모양(아래 참고).
- 오류: 없는 id → 404 `{"error": "not found"}`

### POST api/humanize
- 요청 (JSON)

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `text` | string | 예 | 윤문할 원문. 앞뒤 공백 제거 후 비면 400 |
| `genre` | string | 아니오 | `essay`(기본)·`column`·`report`·`blog`·`abstract`. 그 밖의 값은 `essay` 로 처리 |
| `model` | string | 아니오 | LLM 모델 이름. 비우면 서버 기본(`LLM_MODEL`) |
| `route` | string\|null | 아니오 | `light`·`standard`·`heavy`. 비우거나 다른 값이면 정량 점수의 `route_hint` 로 자동 결정(없으면 standard) |

- 응답: `Content-Type: text/event-stream`. 각 이벤트는 `data: <JSON>\n\n` 한 줄. JSON 모양은 네 가지:
  - 단계: `{"stage": "shim"|"route"|"diagnose"|"rewrite"|"gate"|"finalize", "msg": "설명", "reset": true?}` — `reset: true` 가 오면(rewrite 시작, 롤백 재실행 포함) 지금까지 받은 토큰을 지우고 새로 받기
  - 토큰: `{"token": "글자 조각"}` — 윤문 콜의 출력이 흘러나옴(진단·finalizer 콜은 토큰을 보내지 않음)
  - 끝: `{"done": {결과 객체}}`
  - 오류: `{"error": "RuntimeError: LLM HTTP 404: ..."}` — HTTP 상태는 이미 200 으로 나간 뒤라 본문으로만 옴
- 결과 객체(`done`) 주요 필드

| 필드 | 설명 |
|---|---|
| `run_id` | 실행 id (`api/runs/<run_id>` 로 다시 볼 수 있음) |
| `route`, `model`, `genre` | 실제 쓰인 값 |
| `calls` | LLM 호출 횟수 |
| `input`, `output` | 원문, 최종 윤문 본문(요약 블록 뺀 것) |
| `summary` | 모델이 붙인 `HUMANIZE-SUMMARY` 블록 내용(없으면 빈 문자열) |
| `diagnosis` | 진단 콜 결과 마크다운(light 경로면 빈 문자열) |
| `gate_code`, `gate` | 0 `OK` · 1 `WARN (30%+)` · 2 `ABORT (50%+)` · 3 `ERROR` |
| `gate_report` | 게이트 스크립트(`verify_gates.py --json`) 결과 객체 그대로 |
| `change_rate` | 변경률 0~1 (없으면 null) |
| `rolled_back` | ABORT 로 보수 강도 재실행을 했는지 |
| `finalized` | null · `"corrected"` · `"abort"`(두 번 ABORT → 원문 반환) · `"HOLD_AND_REPORT: 사유"` |
| `metrics` | 사전 정량 점수 |
| `diff_html` | 원문↔결과 비교 HTML(`<del>`·`<ins>`·`<br>`, 본문은 이스케이프됨) |
| `log` | 단계별 로그 텍스트 |
| `ts` | 끝난 시각 ISO |

- 오류: 빈 입력 → 400 `{"error": "빈 입력"}` (스트림 시작 전). 본문이 JSON 이 아니면 연결이 끊김(예외 처리 없음).
- 메모: 대략 light 1콜 · standard 2콜 · heavy 3콜(+ABORT 롤백 1콜). 로컬 모델 기준 수십 초~수 분. 진행률 숫자는 없고 단계 이벤트만 있음. 중간에 연결을 끊어도 서버 쪽 작업은 계속될 수 있음. 1.5만 자 넘는 글은 나눠 넣기 권장(사용법).

## 흐름 예
```js
// 1) 모델 목록과 기록
const models = await fetch('/t/humanize-kr-local/api/models').then(r => r.json());
const runs = await fetch('/t/humanize-kr-local/api/runs').then(r => r.json());

// 2) 윤문 (POST 라 EventSource 대신 fetch 스트림으로 읽음)
const r = await fetch('/t/humanize-kr-local/api/humanize', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({text, genre: 'essay', model: models[0], route: null})});
const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', out = '';
for (;;) {
  const {value, done} = await rd.read(); if (done) break;
  buf += dec.decode(value, {stream: true});
  let i; while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data: ')) continue;
    const ev = JSON.parse(line.slice(6));
    if (ev.reset) out = '';
    if (ev.token) out += ev.token;
    if (ev.error) throw new Error(ev.error);
    if (ev.done) console.log(ev.done.gate, ev.done.output);
  }
}
```

## 화면만의 것 / 주의
- 경로 비교는 `self.path` 전체 문자열로 함 → `api/runs?x=1` 처럼 **쿼리를 붙이면 API 가 아니라 화면 HTML 이 돌아옴**. 쿼리 없이 부를 것.
- 탭(비교·진단·게이트·요약·로그)은 결과 객체 필드를 그대로 보여 줄 뿐, 화면에서 따로 계산하는 값은 없음.
- 사용자 구분: 포털이 넣는 `X-Agent-User` 헤더로 사용자별 폴더에 저장. 서버 주인(owner)은 예전 기록 전체가 보임. 다른 사용자 기록은 안 보임.
- 정적 파일 경로 없음(화면은 HTML 한 장).
