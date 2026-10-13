# 그래프 만들기 API (`chart-local`)
표 데이터(붙여넣기·CSV·XLSX)를 읽어 그래프 후보를 추천하고, SVG 로 미리 보여 주고, 고른 그래프를 고칠 수 있는 문서(XLSX·PPTX·DOCX·HWPX)로 내보냅니다. 포털 경로 접두어: `/t/chart-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/chart-local/api/meta`)

- 서버: 표준 라이브러리 `http.server` (`app.py`). 요청·응답은 모두 JSON (파일 받기만 바이너리).
- 스트리밍(SSE) 없음. 모든 호출은 끝날 때까지 기다렸다가 한 번에 응답합니다.
- 사용자 구분: 포털이 붙이는 `X-Agent-User` 헤더로 이력 폴더가 나뉩니다(`WORKSPACE/u/<id>/history/…`). 헤더가 없거나 `owner` 이면 기본 `WORKSPACE/history/`.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | - | - |
| GET | `api/meta` | 샘플 목록·그래프 종류·내보내기 형식·모델·색 | - | - |
| GET | `api/models` | LLM 서버의 모델 이름 목록 | 최대 3초 | - |
| GET | `api/sample/<id>` | 샘플 표 텍스트 | - | - |
| POST | `api/parse` | 텍스트/파일 → 표(열 이름·단위·타입 추론) | - | - |
| POST | `api/suggest` | 표 → 그래프 후보 + SVG, 이력 저장 | 수백 ms | 예 (이력 생성·갱신) |
| POST | `api/render` | 그래프 설정 하나 → SVG | - | - |
| POST | `api/describe` | 후보마다 제목·한 줄 설명 (LLM) | **LLM**, 수 초~수십 초 (타임아웃 600초) | - |
| POST | `api/export` | 고른 그래프들 → 문서 파일 생성 + 구조 검사 | 수 초 (HWPX 는 Node 실행으로 더 걸림) | 예 (파일 저장) |
| GET | `api/history` | 이력 목록 (최근 200개) | - | 예 |
| GET | `api/history/<id>` | 이력 하나 (표·후보·내보낸 파일 목록) | - | 예 |
| POST | `api/history/delete` | 이력과 내보낸 파일 삭제 | - | 예 |
| GET | `api/file/<id>/<파일명>` | 내보낸 문서 받기 | - | 예 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) HTML | - | - |

## 공통 자료형

### 표 (`table`)
```json
{
  "title": "연도별 예산",
  "columns": [
    {"name": "연도", "unit": "", "type": "date"},
    {"name": "예산", "unit": "억원", "type": "num"}
  ],
  "rows": [["2021", "120"], ["2022", "135"]]
}
```
| 필드 | 형식 | 설명 |
|---|---|---|
| `title` | 문자열 | 표 제목 (첫 줄이 제목처럼 보이면 자동으로 채워짐, 없으면 `""`) |
| `columns[].name` | 문자열 | 열 이름 (빈 값이면 서버가 `"열"` 로 바꿈) |
| `columns[].unit` | 문자열 | 단위, 12자까지 |
| `columns[].type` | `"num"` \| `"date"` \| `"cat"` | 숫자 / 날짜·기간 / 범주. 그 밖의 값은 `"cat"` 으로 바뀜 |
| `rows` | 문자열 2차원 배열 | 셀 값은 **문자열** (`null` → `""`, 숫자도 문자열로 바뀜). 숫자 해석(쉼표·단위·통화 기호)은 서버가 함 |

표를 받는 모든 POST(`suggest`·`render`·`describe`·`export`)는 같은 검사를 거칩니다: 열·행이 비면 오류, 최대 **5000행 × 60열**.

### 그래프 설정 (`spec`, 후보 하나)
`api/suggest` 가 주는 후보 객체가 곧 설정이며, 화면은 이 객체의 필드를 고쳐서 `api/render`·`api/export` 에 그대로 다시 보냅니다(`svg` 필드는 빼고 보냄).

| 필드 | 설명 |
|---|---|
| `id` | 후보 id (`c1`, `c2` …) |
| `kind` | 그래프 종류 (필수). `api/meta` 의 `kinds` 키: `col` `bar` `col_group` `bar_group` `col_stack` `col_stack100` `bar_stack` `line` `area` `area_stack` `scatter` `pie` `doughnut` `hist` `box` `heatmap` `panels` `index` `dual` |
| `name` | 종류의 한국어 이름 (표시용) |
| `x` | x 축(범주·시간) 열 이름. 없으면 `null` (행 순서) |
| `ys` | 값 열 이름 배열 |
| `group` | 긴 형식 표를 계열로 펼칠 범주 열 (선택) |
| `y2` | `dual` 일 때 오른쪽 축 열 |
| `dual_shape`, `panel_shape` | 이중축·2단 비교의 모양 (예: `"bar_line"`) |
| `title`, `subtitle` | 그림 제목·부제 |
| `x_title`, `y_title`, `y2_title` | 축 제목 (비우면 자동) |
| `legend` | 범례 위치 (기본 `"top"`) |
| `labels` | 값 표시 (기본 `"auto"`) |
| `hidden` | 숨길 계열 이름 배열 |
| `hidden_cats` | 숨길 범주 (코드 확인 필요: 일부 차트에서만 사용) |
| `sort` | `"none"` \| `"asc"` \| `"desc"` (시간축은 정렬 안 함) |
| `top` | 앞에서 N 개 범주만 |
| `agg` | 같은 x 가 여러 행일 때 `"sum"`(기본) \| `"mean"` |
| `bins` | 히스토그램 구간 수 (비우면 자동) |
| `trend` | 산점도 추세선 (참이면 표시) |
| `zero_base`, `unit`, `colors` | 0 기준 축, 단위 표기, 계열 색 (화면 편집기에서 씀) |
| `why`, `score` | 추천 이유 문장·점수 (추천 결과에만, 그리기에는 영향 없음) |

## 엔드포인트

### GET api/health
- 응답: `{"ok": true}`

### GET api/meta
- 응답 예:
```json
{
  "samples": [{"id": "budget", "label": "연도별 예산"}, {"id": "measure", "label": "실험 측정값(산점)"}, {"id": "share", "label": "부서별 비율"}],
  "kinds": {"col": "세로 막대", "bar": "가로 막대", "...": "..."},
  "formats": [{"id": "xlsx", "label": "엑셀 (XLSX)", "ok": true}, {"id": "hwpx", "label": "한글 (HWPX)", "ok": true}],
  "model": "<모델 이름>",
  "palette": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
}
```
- `formats[].ok`: HWPX 는 서버에 Node + kordoc 이 있을 때만 `true`. `model` 은 `api/describe` 가 기본으로 쓰는 모델.

### GET api/models
- 응답: 문자열 배열 (예: `["qwen3:8b", "gemma4:31b"]`). LLM 서버가 안 되면 `[]`. 지금 화면은 부르지 않음.

### GET api/sample/<id>
- `<id>` 는 `api/meta` 의 `samples[].id`.
- 응답: `{"text": "탭으로 구분된 표 텍스트", "label": "연도별 예산"}`
- 오류: 없는 id → 404 `{"error": "없음"}`

### POST api/parse
표 텍스트 또는 파일을 표로 바꿉니다. 저장은 하지 않음.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `text` | 문자열 | 둘 중 하나 | 붙여넣은 표 (탭·쉼표 등 구분) |
| `file_b64` | 문자열(base64) | 둘 중 하나 | 파일 내용. 있으면 `text` 무시 |
| `filename` | 문자열 | `file_b64` 와 함께 | 확장자로 형식 판단: `.xlsx`/`.xlsm` → 엑셀, `.xls` → 오류, 그 밖 → CSV |
| `sheet` | 문자열 | 아니오 | 엑셀 시트 이름 (없으면 첫 시트) |
| `transpose` | 참/거짓/생략 | 아니오 | `true` 행·열 바꿈, `false` 안 바꿈, 생략 시 "연도가 머리글에 가로로 늘어선 표"면 자동으로 바꿈 |

- 응답:
```json
{"table": {"title": "", "columns": [...], "rows": [...]},
 "notes": ["첫 줄 '…' 을(를) 제목로 사용", "머리글 아래 단위 행을 열 단위로 사용"],
 "sheets": ["Sheet1", "Sheet2"], "sheet": "Sheet1"}
```
  - `notes`: 서버가 자동으로 처리한 것의 한국어 설명. `sheets`·`sheet` 는 엑셀일 때만 값이 있고 아니면 `null`.
- 오류(400): `{"error": "표 데이터를 찾지 못했습니다"}`, `"파일이 너무 큽니다 (20MB 이하)"`, `.xls` 안내 등.
- 메모: 업로드는 multipart 가 아니라 **JSON 안의 base64** 입니다. 디코드 후 20MB, 요청 본문 약 28MB 를 넘으면 413 `{"error": "요청이 너무 큽니다"}`.

### POST api/suggest
표에서 그래프 후보(최대 12개)를 규칙으로 추천하고 각각 SVG(720×440)를 그립니다. 이력에 표와 후보를 저장합니다.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `id` | 문자열 | 아니오 | 기존 이력 id. 있으면 그 이력을 갱신, 없거나 못 찾으면 새 이력 |
| `source` | 문자열 | 아니오 | 출처 메모 (예: `"붙여넣기"`, 파일 이름) — 이력 목록에 표시 |

- 응답:
```json
{"id": "20261013-101500-ab12",
 "candidates": [{"id": "c1", "kind": "line", "name": "꺾은선", "x": "연도", "ys": ["예산"],
                 "why": "…", "score": 90, "title": "연도별 예산 추이", "legend": "top", "labels": "auto",
                 "svg": "<svg …>…</svg>"}]}
```
  - 후보는 점수 높은 순. 이력에는 `svg` 를 뺀 후보가 저장됨.
- 오류(400): 숫자 열이 없으면 `"숫자 열이 없어 그래프를 만들 수 없습니다 — 열 타입을 '숫자'로 바꿔 보세요"`, 표 검사 실패 메시지.

### POST api/render
설정 하나를 SVG 로 다시 그립니다 (편집할 때마다 부름). 저장 안 함.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `spec` | 그래프 설정 | 예 | `kind` 필수 |
| `w`, `h` | 정수 | 아니오 | 크기 (기본 720 × 440) |

- 응답: `{"svg": "<svg …>…</svg>", "note": "같은 '연도' 값이 여러 행이라 합계로 묶음"}` — `note` 는 범주형 그래프에서 데이터를 묶었을 때만, 아니면 `""`.
- 오류(400): `"그래프 종류가 없습니다"` (`kind` 가 없거나 모르는 값).

### POST api/describe  (LLM)
로컬 LLM 이 후보마다 보고서용 제목(`title`)과 한 줄 설명(`insight`)을 붙입니다. 숫자는 만들지 않게 하고, **표에 없는 숫자가 섞인 문장은 서버가 버립니다**.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `candidates` | 설정 배열 | 예 | `api/suggest` 후보에서 `svg` 를 뺀 것 (`id`·`kind`·`x`·`ys`·`group`·`why` 를 읽음) |
| `model` | 문자열 | 아니오 | 모델 이름 (기본은 `api/meta` 의 `model`) |

- 응답:
```json
{"items": {"c1": {"title": "연도별 예산 추이", "insight": "예산 변화를 연도 순으로 보여 줌"}},
 "dropped": 1, "model": "<모델 이름>"}
```
  - `items` 에는 성공한 후보만 들어감 (`title` 40자, `insight` 90자로 자름). 둘 중 하나만 있을 수도 있음.
  - `dropped`: 표에 없는 숫자가 들어 있어 버린 문장 수.
- 오류(400): `"LLM 서버 연결 실패 (…)"`, `"LLM HTTP 500: …"` 등.
- 메모: 스트리밍 없음, 한 번에 응답. 모델 크기에 따라 수 초~수십 초 (서버 측 타임아웃 600초). 화면은 결과의 `title` 로 바꾼 뒤 각 후보를 `api/render` 로 다시 그림.

### POST api/export
고른 그래프들을 하나의 문서로 만들고, 만든 파일을 다시 열어 구조를 검사합니다.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `specs` | 설정 배열 | 예 | 1~30개, 각 `kind` 필수 |
| `format` | `"xlsx"` \| `"pptx"` \| `"docx"` \| `"hwpx"` | 예 | |
| `id` | 문자열 | 아니오 | 이력 id (`api/suggest` 응답). 없거나 못 찾으면 새 이력을 만듦 |
| `title` | 문자열 | 아니오 | 문서 제목 (80자까지). 파일 이름에도 쓰임 |
| `include_table` | 참/거짓 | 아니오 | 데이터 표도 문서에 넣을지 (기본 `true`) |

- 응답:
```json
{"id": "20261013-101500-ab12",
 "file": "연도별_예산_1013_101530.pptx",
 "url": "api/file/20261013-101500-ab12/%EC%97%B0%EB%8F%84%EB%B3%84_%EC%98%88%EC%82%B0_1013_101530.pptx",
 "size": 48211,
 "report": [{"n": 1, "title": "연도별 예산 추이", "format": "pptx", "modes": ["native"],
             "label": "네이티브 차트 (데이터 편집 가능)", "editable": true}],
 "verify": {"format": "pptx", "ok": true, "charts": 1, "tables": 1, "images": 0,
            "checks": ["…검사한 항목…"], "errors": []}}
```
  - `url` 은 **상대 경로**: 포털 화면이면 `/t/chart-local/` 기준으로 붙이면 됨.
  - `report[].modes`: 그림마다 들어간 방식 — `native`(네이티브 차트, 데이터 편집 가능), `table`(색 칠한 표), `image`(PNG + 데이터 표), `hwp-native`(한글 차트 개체). `editable` 은 `image` 가 없으면 `true`.
  - `verify.ok` 가 `false` 여도 파일은 만들어지고 받을 수 있음 (`errors` 에 경고).
- 오류(400): `"내보낼 그래프를 하나 이상 고르세요"`, `"한 문서에 그래프는 30개까지"`, `"형식은 xlsx|pptx|docx|hwpx"`, `"알 수 없는 그래프 종류"`, HWPX 생성기가 없을 때 안내 문구.
- 메모: 수 초. HWPX 는 서버에서 Node(kordoc)를 실행하므로 더 걸릴 수 있음. 이력의 `exports` 목록에 기록됨.

### GET api/history
- 응답: 배열 (최신순, 최대 200개)
```json
[{"id": "20261013-101500-ab12", "title": "연도별 예산", "ts": "2026-10-13T10:15:00",
  "rows": 6, "cols": 3, "source": "붙여넣기",
  "exports": [{"file": "….pptx", "format": "pptx", "ts": "2026-10-13T10:15:30", "n": 2, "ok": true}]}]
```
  - `exports` 는 마지막 4개만.

### GET api/history/<id>
- `<id>` 형식: `YYYYMMDD-HHMMSS-xxxx` (16진 4자리).
- 응답: 저장된 기록 전체 — `{"id", "ts", "title", "source", "table", "candidates"(svg 없음), "exports"(전체), "selected"(마지막 내보내기의 specs, 내보낸 적 있을 때만)}`
- 오류: 없거나 형식이 틀리면 404 `{"error": "없음"}`.

### POST api/history/delete
- 요청: `{"id": "20261013-101500-ab12"}`
- 응답: `{"ok": true}` (없는 id 여도 `ok`)
- 오류: id 형식이 틀리면 400 `{"error": "잘못된 이력 id"}`.
- 메모: 그 이력 폴더(내보낸 파일 포함)를 통째로 지움. 되돌릴 수 없음.

### GET api/file/<id>/<파일명>
- `api/export` 응답의 `url` 그대로. 파일명은 URL 인코딩.
- 응답: 파일 바이너리. `Content-Disposition: attachment; filename*=UTF-8''<파일명>`.
  - Content-Type: xlsx `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, pptx `…presentationml.presentation`, docx `…wordprocessingml.document`, hwpx `application/hwp+zip`.
- 오류: 없으면 404 `{"error": "없음"}`.

## 흐름 예

### 1) 붙여넣기 → 후보 → 문서 받기
```js
const base = '/t/chart-local/';
const post = (p, b) => fetch(base + p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)})
  .then(async r => { const j = await r.json(); if (!r.ok || j.error) throw new Error(j.error); return j; });

const {table, notes} = await post('api/parse', {text: '연도\t예산(억원)\n2021\t120\n2022\t135\n2023\t150'});
const {id, candidates} = await post('api/suggest', {table, source: '붙여넣기'});
// candidates[i].svg 를 그대로 innerHTML 로 보여 줌
const specs = candidates.slice(0, 2).map(({svg, ...s}) => s);
const ex = await post('api/export', {id, table, specs, format: 'pptx', title: '연도별 예산'});
location.href = base + ex.url;   // 또는 <a href download>
```

### 2) 파일 올리기 (엑셀) + 시트 바꾸기 + 편집 다시 그리기
```js
const buf = new Uint8Array(await file.arrayBuffer());
let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
let j = await post('api/parse', {file_b64: btoa(bin), filename: file.name});
if (j.sheets && j.sheets.length > 1) j = await post('api/parse', {file_b64: btoa(bin), filename: file.name, sheet: j.sheets[1]});
// 후보 하나를 고쳐서 다시 그리기
const spec = {...candidates[0]}; delete spec.svg; spec.sort = 'desc'; spec.title = '새 제목';
const {svg, note} = await post('api/render', {table: j.table, spec});
```

## 화면만의 것 / 주의
- **열 편집은 화면이 직접**: 열 이름·단위·타입을 바꾸는 것은 화면이 `table` 객체를 고친 뒤 `api/suggest` 를 다시 부르는 방식입니다(별도 API 없음). 지금 표를 전치할 때도 화면이 표를 TSV 로 되돌려(`이름(단위)` 머리글) `api/parse` 에 `transpose: true` 로 보냅니다.
- **편집기 상태**: 고른 후보 목록, 사용자가 손으로 고친 제목인지(`edited_title`), LLM 설명(`insight`), 규칙 제목(`rule_title`) 은 화면만의 필드입니다. 화면은 `api/render`·`api/export` 로 보낼 때 `svg`·`insight`·`rule_title`·`note` 를 빼고 보냅니다.
- **이력 저장 시점**: `api/suggest` 와 `api/export` 가 이력을 만들거나 고칩니다. `api/parse`·`api/render`·`api/describe` 는 저장하지 않습니다.
- **SVG**: 서버가 만든 SVG 문자열을 그대로 DOM 에 넣습니다. 색은 `api/meta` 의 `palette` 순서.
- **권한**: owner 전용 엔드포인트 없음. 각 사용자는 자기 이력·파일만 보입니다(`X-Agent-User`). 공유 데이터는 샘플(`api/sample/*`)뿐.
- **정적 파일**: `/static/…` 없음. 화면은 `ui.html` 한 파일이고, `api/…` 가 아닌 모든 GET 경로가 이 HTML 을 돌려줍니다(없는 GET 경로도 404 가 아니라 200 HTML).
- **POST 경로는 정확히 일치해야 함**: 서버가 쿼리 문자열을 떼지 않고 경로를 비교하므로 `api/parse?x=1` 처럼 보내면 404 `{"error": "없는 경로"}` 입니다.
- 오류 응답은 항상 `{"error": "한국어 메시지"}` — 입력 문제·LLM 실패 400, 크기 초과 413, 없음 404, 그 밖 500(`"예외이름: 메시지"`).
- 명령줄 사용(LLM 없이 추천 상위 4개를 문서로): `python3 app.py --cli 표.csv pptx 결과.pptx`.
