# 배터리 API (`battery-local`)
PyBaMM 물리 모델로 리튬이온 셀의 방전·충전 성능과 수명(열화)을 계산하는 작업 큐 서버. 로컬 LLM 은 자연어 질문 → 작업 설정 변환과 결과 해설에만 씀.

포털 경로 접두어: `/t/battery-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/jobs` → `<서버>/t/battery-local/api/jobs`)

- 요청·응답은 모두 JSON(UTF-8). POST 본문은 `Content-Type: application/json`.
- 사용자 구분: 포털이 `X-Agent-User` 헤더를 붙여 넘기고, 서버는 사용자마다 작업 폴더를 따로 씀. 다른 사용자의 작업은 목록에도 안 보이고 id 로도 열 수 없음(404). 직접 헤더를 붙일 필요는 없음(포털 로그인 쿠키로 처리).
- 작업 id 형식: `YYYYMMDD-HHMMSS-xxxx`(xxxx = 16진 4자리). 형식이 다르면 404.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 서버·PyBaMM 상태 | – | – |
| GET | `api/meta` | 파라미터 세트·모델·한도 정보 | 첫 호출만 수 초~수십 초(세트 정보 계산 후 캐시) | 공용 |
| GET | `api/models` | 쓸 수 있는 LLM 이름 목록 | – | 공용 |
| GET | `api/jobs` | 내 작업 목록 + 대기열 | – | 예 |
| GET | `api/jobs/{id}` | 작업 상태·설정·결과(부분 결과)·해설 | – | 예 |
| GET | `api/jobs/{id}/file/{name}` | 결과 파일 다운로드(CSV·PNG·JSON) | – | 예 |
| POST | `api/validate` | 설정 검사·기본값 채우기(실행 안 함) | – | – |
| POST | `api/jobs` | 계산 작업 제출(큐) | 예 — 수 초~수십 분, CPU(GPU 아님) | 예 |
| POST | `api/jobs/{id}/cancel` | 대기·실행 중 작업 취소 | – | 예 |
| POST | `api/jobs/{id}/delete` | 작업 삭제(실행 중이면 취소 후) | – | 예 |
| POST | `api/ask` | 자연어 질문 → 설정(→ 바로 제출) | LLM, SSE 스트리밍 | 예 |
| POST | `api/jobs/{id}/explain` | 결과 해설 생성 | LLM, SSE 스트리밍 | 예 |
| GET | 그 밖의 경로(`/` 등) | 화면 `ui.html` | – | – |

## 공통 오류
- 응답 본문 `{"error": "메시지(한국어)"}`.
- GET: 없는 작업·없는 파일·잘못된 id·없는 `api/…` 경로 → **404 `{"error": "없음"}`**(구체 메시지 없음). 그 밖의 예외 → 500(오류 문구 속 서버 폴더 경로는 지우고 파일 이름만 남김).
- POST: JSON 파싱 실패·본문이 객체가 아님 → 400 `{"error": "잘못된 요청"}`. 끝난 작업 취소 → 409. 설정 검증 실패(ValueError) → **400** + 구체 메시지(예: `"C-rate: 0.02~10 범위여야 합니다 (입력 20)"`). 없는 작업 → 404. 없는 경로 → 404 `{"error": "없는 경로"}`. 그 밖 → 500.
- SSE 엔드포인트(`api/ask`, `…/explain`)는 응답 헤더를 먼저 200 으로 보내므로, 오류(빈 질문, 결과 없음 등)도 **상태 코드 200 + 이벤트 `{"error": "..."}`** 로 옴.

## 엔드포인트

### GET api/health
- 응답: `{"ok": true, "python": true, "pybamm": "26.3.0"}` — `python` 은 PyBaMM 파이썬을 찾았는지(참/거짓, 서버 경로는 주지 않음), `pybamm` 은 못 찾으면 `null`.

### GET api/meta
- 응답(줄임):
```json
{"pybamm": "26.3.0", "python": "3.11.15",
 "sets": [{"name": "OKane2022", "chem": "NMC811 / 흑연-SiOx", "cell": "LG M50 21700 원통형", "note": "…",
           "capacity": 5.0, "vmin": 2.5, "vmax": 4.2,
           "caps": {"thermal": true, "sei": true, "plating": true, "lam": true},
           "sei_models": ["solvent-diffusion limited", "ec reaction limited", "…"], "sei_ea": 38000.0, "doc": "…"}],
 "models": {"SPM": "단일입자 모델 — …", "SPMe": "…(권장)", "DFN": "…"},
 "sei_label": {…}, "mech_label": {…}, "ea_default": …,
 "llm_model": "<기본 LLM>", "max_cycles": 5000, "max_jobs": 2, "disclaimer": "물리 모델(PyBaMM) 기반 시뮬레이션입니다. …"}
```
- `sets[].caps` 로 세트가 지원하는 열화(`sei`·`plating`·`lam`)와 열 모델(`thermal`)을 판단 → 화면에서 체크박스 회색 처리에 씀. `sei_models` 는 `sei_model` 값으로 허용되는 목록.
- `sei_label`·`mech_label`·`ea_default` 의 정확한 모양은 `worker.py` 의 `meta()` 참고(코드 확인 필요).
- 오류: 세트 정보를 못 구하면 500 `{"error": "..."}`.

### GET api/models
- 응답: 문자열 배열 `["qwen3:8b", "gemma4:31b", …]`. LLM 서버에 못 붙으면 `[]`.
- `api/ask`·`explain` 의 `model` 값으로 씀(비우면 서버 기본 = `meta.llm_model`).

### GET api/jobs
- 응답:
```json
{"jobs": [{"id": "20261007-064417-06ea", "kind": "rate", "title": "방전 C-rate 비교 0.2C, 0.5C, 1C, 2C · Chen2020 · SPMe · 25°C",
           "state": "done", "pct": 100, "msg": "완료 (3.6 초)", "created": "2026-10-07T06:44:17",
           "elapsed": 3.6, "origin": "form", "question": ""}],
 "queue": ["20261007-…"]}
```
- `jobs`: 최근 순, 최대 300개. `state` = `queued | running | done | error | canceled`. `origin` = `form`(화면/API 제출) · `ask`(AI 질문) · `cli`.
- `queue`: 대기 중인 내 작업 id 배열(다른 사용자의 작업·서버 폴더 경로는 들어가지 않음).

### GET api/jobs/{id}
- 응답:
```json
{"status": {"id": "…", "kind": "life", "title": "…", "state": "running", "pct": 42.0, "msg": "사이클 200/500",
            "created": "…", "started": "…", "elapsed": 31.2, "origin": "form", "question": "", "partial": true},
 "job": { …검증된 설정(아래 POST api/jobs 참고)… },
 "partial": { …계산 중 단계 결과(수명 작업만, result 가 생기면 안 옴)… },
 "result": { …최종 결과… },
 "explain": { …AI 해설(만들었으면)… },
 "steps": [["Discharge at 1C until 2.5V", "Rest for 5 minutes", "Charge at 0.5C until 4.2V", "Hold at 4.2V until C/20", "Rest for 5 minutes"]],
 "disclaimer": "…"}
```
- `result`·`partial`·`explain` 은 있을 때만 키가 생김. `steps` 는 `kind=life` 일 때만(시나리오별 한 사이클 PyBaMM 실험 문자열).
- `result` 공통 키: `kind`, `set`, `model`, `temp_C`, `sec`(계산 초), `pybamm`, `cap_nom`(공칭 Ah), `files`(다운로드 가능한 파일 이름 목록), `notes`(주의 문장 배열).
  - `rate`·`charge`·`drive`: `thermal`, `runs: [{"metrics": {...}, "series": {...}}]`. `metrics` 예(rate): `label, c_rate, current_A, capacity_Ah, capacity_pct, energy_Wh, avg_V, duration_min, end_V, T_max_C, T_rise_C`. `series` 는 곡선 배열(그래프용). `charge` 는 `cv_cut_C` 추가.
  - `life`: `scenarios: [{label, scenario, steps, temp_C, rows, curves, notes, eol: {threshold_pct, simulated, simulated_dis, extrap_ratio?, fit_curve?, …}, final: {cycle, soh, soh_dis, LLI_pct, LAM_ne_pct, …, dominant}}]`.
  - 세부 필드는 `worker.py` 의 `run_rate/charge/drive/life` 반환값 기준(코드 확인 필요).
- `explain`: `{"text", "unverified_numbers": [...], "facts": {...}, "disclaimer", "model", "ts"}`.
- **진행 상황 조회**: 이 엔드포인트를 폴링. 지금 화면은 `state` 가 `queued`/`running` 인 동안 1.5초마다 다시 부름. `pct`(0~100)·`msg`·`elapsed` 로 진행 막대 표시.

### GET api/jobs/{id}/file/{name}
- `name` 은 확장자 `.csv`·`.png`·`.json` 만 허용(그 외·`.` 로 시작 → 404).
- 실제로 생기는 파일: `plot.png`, `summary.csv`, `curves.csv`(성능 작업), `cycles.csv`(수명 작업), `result.json`. 정확한 목록은 `result.files`(+`result.json`).
- 응답 헤더: `Content-Type` = `text/csv; charset=utf-8` / `image/png` / `application/json`, `Content-Disposition: attachment; filename="battery-{id}-{name}"`. CSV 는 UTF-8 BOM 포함(엑셀용).
- `<a href="api/jobs/{id}/file/summary.csv">` 링크로 바로 받으면 됨.

### POST api/validate
- 요청: 작업 설정 객체. `{"job": {...}}` 또는 설정 객체를 바로 보내도 됨.
- 응답: `{"job": {...기본값을 채운 설정...}, "steps": [[...]] | null}` — `steps` 는 `life` 일 때 시나리오별 한 사이클 실험 문자열(미리보기용), 아니면 `null`.
- 오류: 400 `{"error": "<무엇이 틀렸는지>"}`. 실행하지 않음 → 폼 입력 중 미리보기·검증에 쓰기 좋음.

### POST api/jobs
- 요청: `{"job": {...}}` 또는 설정 객체 그대로. 서버가 `api/validate` 와 같은 검증을 한 뒤 큐에 넣음.
- 응답: `{"id": "20261013-101500-ab12"}` → 이후 `GET api/jobs/{id}` 폴링.
- 작업 설정 필드(범위 밖이면 400):

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `kind` | `"rate" \| "charge" \| "drive" \| "life"` | 필수 | 방전 C-rate 비교 / CC-CV 충전 / 전류 프로파일 / 수명 |
| `set` | 문자열 | 필수 | `meta.sets[].name` 중 하나 |
| `model` | `"SPM" \| "SPMe" \| "DFN"` | | 기본 life=`SPM`, 그 외 `SPMe` |
| `temp_C` | 숫자 | | 주변 온도 −20~60, 기본 25 |
| `thermal` | `"lumped" \| "isothermal"` | | life 제외. 기본 `lumped`(세트가 지원 안 하면 `isothermal`로 바뀜) |
| `h` | 숫자 | | 열전달계수 0.1~1000 (life 제외) |
| `c_rates` | 숫자 배열 또는 `"0.2, 0.5, 1"` 문자열 | rate·charge 필수 | 0.02~10, 최대 6개(초과분은 버림) |
| `cv_cut_C` | 숫자 | | charge: CV 종료 전류 0.005~0.5 C, 기본 0.05 |
| `soc0` | 숫자 | | 시작 SOC %. charge 0~90(기본 0), drive 5~100(기본 100) |
| `profile` | `[[시간 s, 전류], …]` | drive 필수 | 2~50000점. **양수=방전, 음수=충전** |
| `unit` | `"A" \| "C"` | | drive 전류 단위, 기본 `A` |
| `repeat` | 정수 | | drive 반복 1~200, 기본 1 |
| `mechanisms` | `["sei","plating","lam"]` 부분집합 | | life. 기본 `["sei"]`. 세트 `caps` 에 없는 것은 400. `lam` 만 고르면 `sei` 자동 추가 |
| `sei_model` | 문자열 | | life. 세트 `sei_models` 중 하나, 기본 `"solvent-diffusion limited"` |
| `sei_arrhenius` | bool | | life. 기본 true |
| `sei_scale` | 숫자 | | life. SEI 속도 배율 0.01~1000, 기본 1 |
| `max_cycles` | 정수 | | life. 1~`meta.max_cycles`, 기본 500 |
| `eol` | 숫자 | | life. EOL 기준 SOH % 50~99, 기본 80 |
| `scenarios` | 객체 배열 | | life. 1~4개, 기본 `[{}]` |

  `scenarios[]` 원소:

| 필드 | 형식 | 설명 |
|---|---|---|
| `label` | 문자열 | 이름(최대 40자), 기본 "시나리오 N" |
| `temp_C` | 숫자 | −20~60, 기본 = 작업 `temp_C` |
| `dis_C` / `chg_C` | 숫자 | 방전/충전 C-rate 0.05~5, 기본 1 / 0.5 |
| `dod` | 숫자 | 방전심도 % 5~100, 기본 100 |
| `cv_cut_C` | 숫자 | 0~0.5, 기본 0.05 (0 이면 CV 없음) |
| `rest_min` | 숫자 | 휴지 분 0~600, 기본 5 |
| `vmax` | 숫자 | 충전 상한 전압(세트 vmin~vmax), 기본 세트 vmax |
| `steps` | 문자열 배열 | 직접 쓴 PyBaMM 실험 단계(최대 12개, 각 120자). 있으면 위 C-rate 등 무시. `Discharge|Charge|Rest|Hold` 로 시작해야 함(예 `"Charge at 2C until 4.2V"`) |

- 메모:
  - 계산은 서버 CPU 에서 별도 프로세스로 돎(GPU 아님). 동시 실행 수 `meta.max_jobs`(기본 2), 나머지는 대기열 — 대기열은 **모든 사용자 공용 순서**.
  - 걸리는 시간: 성능 작업 수 초, 수명 1000 사이클 × 시나리오 1개 ≈ 1분(SPM), DFN 은 수십 배. 작업당 최대 7200초(서버 설정) 넘으면 `state=error`.
  - 수명 작업은 사이클을 단계로 나눠 계산하며 중간에 `status.partial=true` + `partial` 결과가 생김(최종 아님).
  - 창을 닫아도 서버에서 계속 돎. 서버가 재시작되면 대기·실행 중이던 작업은 `error`("서버 재시작으로 중단됨")로 바뀜.

### POST api/jobs/{id}/cancel
- 요청 본문 없음(또는 `{}`). 응답 `{"ok": true}`. 대기 중이면 큐에서 빼고, 실행 중이면 프로세스를 끝냄. `state` → `canceled`.
- 이미 끝난 작업(`done`·`error`·`canceled`)이면 **409** `{"error": "이미 끝난 작업은 취소할 수 없습니다"}`, 상태는 그대로. 내 작업이 아니거나 없는 id → 404.

### POST api/jobs/{id}/delete
- 응답 `{"ok": true}`. 실행 중이면 먼저 취소. 작업 폴더(결과·파일 포함)를 지움 — 되돌릴 수 없음.

### POST api/ask  (SSE)
- 요청:

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `question` | 문자열 | 필수 | 자연어 질문(앞 2000자만 씀) |
| `model` | 문자열 | | LLM 이름(`api/models`), 비우면 서버 기본 |
| `run` | bool | | 기본 **true** = 만든 설정을 바로 제출. false 면 설정만 돌려줌 |

- 응답: `Content-Type: text/event-stream`. 각 이벤트는 `data: {json}\n\n` 한 줄.
  - `{"stage": "llm", "msg": "<모델> · 질문 → 실험 설정"}` — 단계 안내
  - `{"token": "..."}` — LLM 출력 토큰(JSON 이 만들어지는 과정, 표시용)
  - `{"stage": "llm", "msg": "형식 재시도", "reset": true}` — JSON 해석 실패로 다시 생성. 지금까지 받은 토큰 표시를 지울 것
  - 마지막: `{"done": {"job": {...}, "reason": "설정 이유", "steps": [[...]] | null, "title": "…", "id": "<작업 id, run=true 일 때>"}}`
  - 오류: `{"error": "..."}` (상태 코드는 200). LLM 이 만든 설정이 검증에 걸려도 여기로 옴.
- `EventSource` 는 GET 만 되므로 `fetch` + `ReadableStream` 으로 읽어야 함(아래 예).
- LLM 응답에 따라 수 초~수 분.

### POST api/jobs/{id}/explain  (SSE)
- 요청: `{"model": "<선택>"}`.
- 이벤트: `{"stage": "llm", "msg": "…"}`, `{"token": "..."}` 반복, 마지막 `{"done": {"text", "unverified_numbers", "facts", "disclaimer", "model", "ts"}}`.
- `unverified_numbers`: 해설 문장 속 숫자 중 계산 결과에 없는 것 → 화면에서 ⚠ 경고로 보여줄 것.
- 결과는 서버에 저장되어 이후 `GET api/jobs/{id}` 의 `explain` 으로도 나옴. 결과가 없는(완료 전) 작업이면 `{"error": ...}` 이벤트.

## 흐름 예

### 1) 폼으로 방전 비교 계산 → 폴링 → 다운로드
```js
const B = '/t/battery-local/';
const post = (p, b) => fetch(B + p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)})
  .then(async r => { const j = await r.json(); if (!r.ok) throw new Error(j.error); return j; });

const {id} = await post('api/jobs', {job: {kind: 'rate', set: 'Chen2020', model: 'SPMe', temp_C: 25, c_rates: [0.2, 0.5, 1, 2]}});
let r;
do {
  await new Promise(ok => setTimeout(ok, 1500));
  r = await fetch(B + 'api/jobs/' + id).then(x => x.json());
  console.log(r.status.pct, r.status.msg);
} while (['queued', 'running'].includes(r.status.state));
if (r.status.state === 'done')
  r.result.files.forEach(f => console.log(B + `api/jobs/${id}/file/${f}`));   // 다운로드 링크
```

### 2) AI 질문 (SSE) → 작업 자동 제출 → 끝나면 해설
```js
async function sse(p, body, on) {
  const r = await fetch(B + p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', done = null;
  for (;;) {
    const {value, done: end} = await rd.read(); if (end) break;
    buf += dec.decode(value, {stream: true}); let k;
    while ((k = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, k); buf = buf.slice(k + 2);
      if (!line.startsWith('data:')) continue;
      const ev = JSON.parse(line.slice(5));
      if (ev.error) throw new Error(ev.error);
      if (ev.done) done = ev.done; else on(ev);
    }
  }
  return done;
}
const a = await sse('api/ask', {question: '45도에서 2C 급속충전하면 수명 얼마나 줄어?'}, ev => ev.token && out.append(ev.token));
// a.id 로 위 1) 처럼 폴링 → done 이 되면:
const ex = await sse(`api/jobs/${a.id}/explain`, {}, ev => ev.token && out.append(ev.token));
```

## 화면만의 것 / 주의
- **그래프는 화면이 직접 그림**(외부 라이브러리 없는 자체 SVG). 데이터는 `result.runs[].series` / `result.scenarios[].curves`·`rows`. 그래프별 **SVG 저장도 화면 기능**(서버 API 없음). 서버가 주는 그림은 `plot.png` 하나.
- 작업 목록은 화면이 4초마다 `GET api/jobs` 로 새로 고침, 연 작업은 1.5초마다 `GET api/jobs/{id}`.
- 화면의 "AI 해설 자동"은 작업이 `done` 이 되면 화면이 `explain` 을 부르는 것(서버가 자동으로 하지 않음).
- "충전 C-rate 비교 / 온도 비교 / DoD 비교" 버튼, "예시 프로파일 만들기"(합성 펄스), CSV 프로파일 파일 읽기는 화면이 만들어 `profile`/`scenarios` 로 넣는 것 — 업로드 API 는 없음(프로파일은 JSON 배열로 보냄, 최대 50000점).
- `api/meta`·`api/models` 는 모든 사용자 공용. 작업·결과는 사용자별. 서버 주인(owner)은 예전 기록(사용자 구분 전 기록)도 봄.
- 정적 파일 경로(`/static/…`) 없음. `api/` 가 아닌 모든 GET 은 화면 HTML 을 돌려줌.
- 결과는 물리 모델 시뮬레이션 — `disclaimer` 문구를 화면에 같이 보여 주는 것을 권장.
