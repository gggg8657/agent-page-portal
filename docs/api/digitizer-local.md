# 그래프 디지타이저 API (`digitizer-local`)
논문·보고서의 그래프 그림(PNG·JPG·PDF 쪽)에서 곡선·점·막대 값을 숫자로 뽑아 CSV·엑셀로 내보냅니다. 좌표는 픽셀 처리와 축 보정으로 계산하고, 로컬 VLM 은 눈금 숫자·축 이름·범례·범주 이름 읽기만 돕습니다.

축이 없는 인포그래픽은 인쇄된 수치와 픽셀 위치로 각 계열을 따로 보정하고, 여러 패널은 패널별로 잘라 처리합니다. 누적 막대에서 색 조각이 원본 이미지에서 1픽셀보다 얇으면 값을 복원할 수 없어 빈칸으로 남깁니다. 이때 출력은 이미지 측정 근삿값이므로 보정 위치와 결과 표를 확인해야 합니다.

포털 경로 접두어: `/t/digitizer-local/`. 아래 경로는 모두 이 뒤에 붙습니다(예: `api/auto` → `/t/digitizer-local/api/auto`). 단독 실행(`python3 app.py`, 기본 포트 8783)이면 `http://<서버>:8783/api/auto` 입니다.

- 모든 POST 는 **JSON 본문**(`Content-Type: application/json`)입니다. multipart 는 쓰지 않으며, 파일은 **data URL(base64)** 로 JSON 에 넣어 보냅니다.
- 스트리밍(SSE·청크)은 없습니다. 모든 호출은 한 번에 응답합니다(오래 걸리는 `api/auto` 도 동기).
- 응답 헤더: `Cache-Control: no-store`, `X-Author` 가 붙습니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | - | - |
| GET | `api/meta` | 기본 모델·모델 목록·PDF/엑셀 지원 여부 | LLM 서버에 모델 목록만 물음(3초 제한) | - |
| GET | `api/history` | 저장된 작업 목록(최근 200개) | - | 예 |
| GET | `api/project/<pid>` | 저장된 작업 하나 전체 | - | 예 |
| GET | `api/img/<id>.png` | 올린(또는 자른) 그림 PNG | - | 예 |
| GET | `api/pdfpage/<pdfid>/<쪽>.png?dpi=` | PDF 한 쪽을 PNG 로(썸네일용) | 쪽·dpi 따라 수 초 | 예 |
| POST | `api/image` | 그림 올리기 → 그림 id | - | 예(파일 저장) |
| POST | `api/pdf` | PDF 올리기 → PDF id·쪽 수 | - | 예(파일 저장) |
| POST | `api/pdfpage` | PDF 한 쪽을 그림으로 바꿔 저장 → 그림 id | 수 초 | 예(파일 저장) |
| POST | `api/auto` | 자동 보정(축·눈금 검출 + VLM 눈금 읽기) + 계열/막대 자동 추출 | **길다**(VLM 사용 시 10~60초+, LLM/GPU) | 예(읽기) |
| POST | `api/crop` | 그림 일부를 새 그림으로 저장 | - | 예(파일 저장) |
| POST | `api/panels` | 그림 안 그래프(패널) 후보 찾기 | 짧음(CPU) | 예(읽기) |
| POST | `api/colors` | 그림 영역 안의 계열 후보 색 | 짧음 | 예(읽기) |
| POST | `api/extract` | 색 하나로 계열 추출(곡선·점) | 짧음~수 초(CPU) | 예(읽기) |
| POST | `api/export` | CSV / XLSX 파일 만들기(다운로드) | 짧음 | - |
| POST | `api/save` | 작업 저장(이력) | - | 예(파일 저장) |
| POST | `api/history/delete` | 저장된 작업 지우기 | - | 예(삭제) |
| GET | 그 밖의 모든 경로 | 화면 HTML(`ui.html`) | - | - |

## 공통
- **사용자 분리**: 포털이 넘기는 `X-Agent-User` 헤더로 작업 폴더가 갈립니다(`WORKSPACE/u/<id>/`). 헤더가 없거나 `owner` 면 서버 주인 폴더. 그림 id·PDF id·작업 pid 는 **그 사용자 폴더 안에서만** 찾아지므로, 다른 사용자의 id 로는 404 가 납니다.
- **그림 id**: 그림 PNG 내용의 SHA-1 앞 16자(소문자 16진수, 예 `b046ba4cd81dbfa7`). 같은 그림을 다시 올리면 같은 id. **PDF id** 도 같은 형식(PDF 바이트 SHA-1).
- **작업 pid**: `YYYY-MM-DD-xxxxxx`(xxxxxx 는 16진수 6자).
- **좌표**: 모두 **그림 픽셀 좌표**(왼쪽 위 원점, y 는 아래로 증가). 데이터 값으로의 변환은 보정(calib)으로 합니다.
- **보정 객체 `calib`** (여러 엔드포인트 공통):
  ```json
  {"x": {"p1": [px, py], "p2": [px, py], "v1": 0, "v2": 10, "log": false},
   "y": {"p1": [px, py], "p2": [px, py], "v1": 0, "v2": 100, "log": false}}
  ```
  축마다 그림 위 두 점(p1·p2, 픽셀)과 그 점의 값(v1·v2), 로그축 여부. 막대그래프의 범주축은 `"cat": true` 가 붙고 v1·v2 는 픽셀값 그대로입니다. 값 변환식은 화면 `ui.html` 의 `mat()/toData()` 와 서버 `digitize.py` 가 같습니다(축 방향 벡터에 투영 → 선형/로그10 보간).
- **그림 영역 `box`**: `{"x0":…, "y0":…, "x1":…, "y1":…}` (축선 안쪽, 픽셀).
- **오류**: 본문 `{"error": "메시지"}`.
  - 404: 그림·PDF·작업 파일이 없음(`FileNotFoundError`), POST 의 없는 경로(`"없는 경로"`).
  - 413: POST 본문이 약 56MB(40MB × 1.4) 초과(포털을 거쳐도 413 — 서버가 본문을 버려 읽은 뒤 응답).
  - 500: 그 밖의 모든 오류(잘못된 id 형식, 필수 필드 누락, JSON 파싱 실패, LLM 연결 실패 등) — 메시지는 `"<예외 이름>: <내용>"` 형식(예 `"ValueError: 이미지 id 가 잘못되었습니다"`, 필드 누락은 `"KeyError: 'box'"`). 400 은 쓰지 않습니다.
- **업로드 크기**: 원본 파일 40MB 이하(`MAX_UPLOAD`). 그림은 긴 변 6000px 초과 시 줄여 저장합니다.

## 엔드포인트

### GET api/health
- 응답: `{"ok": true}`

### GET api/meta
- 응답 예:
  ```json
  {"model": "gemma4:31b", "models": ["gemma4:31b", "qwen3:32b"], "pdf": true, "xlsx": true}
  ```
  | 필드 | 설명 |
  |---|---|
  | `model` | 기본 VLM 모델(`VISION_MODEL` 또는 `LLM_MODEL`) |
  | `models` | LLM 서버의 모델 목록(Ollama `/api/tags` 또는 OpenAI 호환 `/models`). 연결 실패 시 `[]` |
  | `pdf` | `pdftoppm` 이 있는지(없으면 PyMuPDF 로 대신 시도) |
  | `xlsx` | `openpyxl` 이 있는지 — 없으면 `api/export` 가 `xlsx` 요청에도 CSV 를 돌려줌 |
- 메모: 모델 목록에 이미지 입력이 안 되는 모델(임베딩 등)도 섞여 나옵니다. 거르는 기능은 없습니다.

### GET api/history
- 응답: 배열(최근 수정 순, 최대 200개)
  ```json
  [{"pid": "2026-10-07-3cbb09", "name": "fig3", "saved": "2026-10-07T07:20:20", "image": "b046ba4cd81dbfa7",
    "series": [{"name": "계열 1", "n": 42}]}]
  ```
  `image` 는 그림 id(`api/img/<id>.png` 로 썸네일), `series[].n` 은 점 개수.

### GET api/project/&lt;pid&gt;
- 응답: `api/save` 로 저장한 `project` 객체 그대로 + `pid`, `saved`(ISO 시각). 형식은 서버가 검사하지 않으므로 저장한 쪽(화면)이 정한 모양입니다(아래 `api/save` 참고).
- 오류: pid 형식이 틀리면 500(`ValueError: 잘못된 id`), 없으면 404.

### GET api/img/&lt;id&gt;.png
- 응답: `image/png` 바이트. 없으면 404 JSON.

### GET api/pdfpage/&lt;pdfid&gt;/&lt;쪽&gt;.png
- 쿼리: `dpi`(기본 40, 20~600 으로 자름). 화면은 썸네일에 `dpi=24`(→ 20 으로 올림) 사용.
- 응답: `image/png`. 저장하지 않습니다(그림 id 없음). 분석하려면 `POST api/pdfpage` 를 쓰세요.
- 오류: PDF 없음 404, 쪽 번호가 범위 밖이면 500.

### POST api/image
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `data` | 문자열 | 예 | 그림 파일의 data URL(`data:image/png;base64,...`). 쉼표 뒤 base64 만 씀. PIL 이 여는 형식이면 됨 |
- 응답: `{"id": "b046ba4cd81dbfa7", "w": 1200, "h": 800}` (저장된 PNG 의 크기)

### POST api/pdf
- 요청: `{"data": "data:application/pdf;base64,..."}` — 내용이 `%PDF-` 로 시작해야 함.
- 응답: `{"id": "<pdfid>", "pages": 12}`
- 오류: PDF 가 아니면 500(`ValueError: PDF 파일이 아닙니다`), 변환 도구가 없으면 500.

### POST api/pdfpage
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `id` | 문자열 | 예 | PDF id |
  | `page` | 정수 | 아니오 | 1부터. 기본 1 |
  | `dpi` | 정수 | 아니오 | 기본 200(20~600). 200~300 권장 |
- 응답: `api/image` 와 같음 `{"id", "w", "h"}` — 이 쪽이 그림으로 저장됨.

### POST api/auto
자동 보정의 핵심. 축선·눈금·눈금 글자 위치를 픽셀로 찾고(결정론), VLM 이 읽은 눈금 값과 짝지어 보정을 만든 뒤, 계열(선·점) 또는 막대를 자동 추출합니다.
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `id` | 문자열 | 예 | 그림 id |
  | `vlm` | bool | 아니오 | 기본 `true`. `false` 면 LLM 을 부르지 않고 축·눈금 검출만(또는 `xvals`/`yvals` 로 보정) |
  | `model` | 문자열 | 아니오 | VLM 모델 이름(없으면 기본 모델) |
  | `xvals`, `yvals` | 배열 | 아니오 | 눈금 값을 직접 지정(“AI 없이”). 숫자 또는 `"1,000"`, `"10^3"`, `"1e-2"`, `"−0.5"`, `"10⁻²"` 같은 문자열. 둘 다 주면 VLM 을 건너뜀 |
  | `chart` | 문자열 | 아니오 | `auto`(기본) · `xy`(선·점) · `bar`/`vbar`(세로 막대) · `hbar`(가로 막대) |
  | `whole` | bool | 아니오 | `true` 면 그래프가 여럿이어도 패널 고르기를 건너뛰고 그림 전체로 진행 |
- 응답(최상위):
  | 필드 | 설명 |
  |---|---|
  | `message` | 한국어 안내(무엇을 했고 다음에 뭘 하면 되는지). 늘 채워짐 |
  | `need_panel` | `true` 면 그래프가 여러 개 — `panels` 중 하나를 골라 `api/crop`(box=`crop`, plot=`box`) 후 다시 `api/auto` |
  | `panels` | `[{"box": [x0,y0,x1,y1], "crop": [x0,y0,x1,y1], "kind": "plot"|"image", "score": …}]` — `box` 는 축 안쪽, `crop` 은 눈금 글자·축 이름까지 포함한 자르기 범위, `image` 는 지도·사진 등 |
  | `mode` | `"xy"` 또는 `"bar"` |
  | `detect` | 검출·보정 결과(아래). `need_panel` 이면 `{"ok": false, "error": message}` |
  | `vlm` | VLM 이 읽은 것(안 썼거나 실패하면 `null`): `x_ticks`, `y_ticks`, `y2_ticks`, `x_ticks_each`/`y_ticks_each`(글자 덩어리별 1:1 값, 있을 때), `x_log`, `y_log`, `x_label`, `y_label`, `title`, `legend`(`[{name,color,style,marker,axis}]`), `chart_type`, `raw`(원문) |
  | `bar` | 막대 모드 결과(아래) 또는 `null` |
  | `error` | VLM 실패 메시지(진행은 계속됨) 또는 `null` |
  | `axis_break` | 값축이 끊긴 그래프일 때만 |
- `detect` 주요 필드:
  | 필드 | 설명 |
  |---|---|
  | `ok` | 축선을 찾았는지. `false` 면 `error` |
  | `box` | 그림 영역 `{x0,y0,x1,y1}` |
  | `axes`, `ticks`, `labels` | 축선·눈금(`x`,`y`,`x_major`,`y_major` 픽셀 위치)·눈금 글자 위치/상자(`x_boxes`,`y_boxes`, `x_band`,`y_band`) |
  | `calib` | 보정 객체(못 맞추면 `null`) |
  | `calib2` | 오른쪽 y축이 따로 있을 때 그 보정(x 는 같고 y 만 다름) |
  | `fit` | 축별 짝짓기 결과(`resid_px` 잔차, `n`, `dropped`, `log`, `source` 등). 막대 모드에서는 `null` |
  | `warn` | 경고 문자열 배열(확인하라는 것) |
  | `colors` | 그림 영역의 계열 후보 색(`api/colors` 와 같은 것) |
  | `legend_boxes` | 테두리 있는 범례 상자 `[[x0,y0,x1,y1]]`(추출 때 자동 제외) |
  | `series` | 제안/자동 추출 계열 배열. 자동 추출되면 각 항목에 `name`, `color`(hex), `px`(픽셀 점 `[[x,y],…]`), `mode`, `how`, `kind`, `warn`, 오른쪽 축 계열이면 `calib` |
  | `bar_guess` | 찾은 막대 수 |
- `bar`(막대 모드) 필드: `ok`, `orient`(`"v"`/`"h"`), `calib`, `fit`, `bars`, `series`, `warn`, `legend_src`, `n_err`, 실패 시 `error`.
  - `bars[]`: `cat`(범주 이름, 중복이면 구분 꼬리표), `group`, `note`, `label`, `key`, `c`(막대 중심 픽셀), `w`([왼, 오른] 픽셀), `base`(바닥 픽셀), `ends`(쌓인 조각마다 끝 픽셀, 아래→위), `series`(조각마다 계열 번호), `err`([위 끝, 아래 끝] 픽셀 또는 `null`), `open`.
  - `series[]`: `name`, `color`, `kind`, `ink`, `legend`(범례와 짝지었는지), `legend_box`.
- 메모: **동기 호출이며 오래 걸립니다.** VLM 을 여러 번 부릅니다(전체 그림, 축별 글자 덩어리 시트, 막대면 범례·범주 시트, 오른쪽 축 등). LLM 호출 하나당 제한 600초. 화면은 10~60초를 안내하고, 502/504/네트워크 끊김이면 3초 뒤 한 번 더 부릅니다. 진행 상황 조회(작업 id·폴링)는 없습니다. 실패해도 대개 200 으로 오고 `message`/`detect.ok`/`bar.ok` 로 판단합니다.

### POST api/crop
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `id` | 문자열 | 예 | 원본 그림 id |
  | `box` | `[x0,y0,x1,y1]` | 예 | 자를 영역(픽셀). 30×30 미만이면 오류 |
  | `plot` | `[x0,y0,x1,y1]` | 아니오 | 그 안의 축 상자(패널 고르기에서 받은 `box`). 다음 `api/auto` 가 이 상자를 힌트로 씀 |
- 응답: `{"id": "<새 그림 id>", "w": …, "h": …, "parent": "<맨 처음 원본 id>"}` — 원본과 위치가 기록되어, 막대 모드에서 그래프 밖 공유 범례를 원본에서 찾습니다.

### POST api/panels
- 요청: `{"id": "<그림 id>"}`
- 응답: `{"panels": [...]}` (`api/auto` 의 `panels` 와 같은 모양)
- 메모: 현재 화면은 이 엔드포인트를 직접 부르지 않고 `api/auto` 응답의 `panels` 를 씁니다.

### POST api/colors
- 요청: `{"id": "<그림 id>", "box": {"x0":…,"y0":…,"x1":…,"y1":…}}` — `box` 필수(없으면 500 `KeyError`).
- 응답: `{"colors": [...]}` — 채도 높은 색 + 거의 검정 후보(최대 6개). 항목: `{"color": "#d62728", "count": 1234}` (count = 그 색 픽셀 수).
- 메모: 현재 화면은 직접 부르지 않고 `api/auto` 응답의 `detect.colors` 를 씁니다.

### POST api/extract
색 하나로 계열 하나를 뽑습니다(선·점 그래프).
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `id` | 문자열 | 예 | 그림 id |
  | `calib` | 객체 | 예 | 보정 |
  | `box` | 객체 | 예 | 그림 영역 |
  | `excludes` | 배열 | 아니오 | 제외 영역 사각형 목록(글자·주석 빼기) — 화면의 ⊘ 제외 영역 |
  | `series` | 객체 | 아니오 | 추출 설정(아래) |
- `series` 설정(화면이 보내는 값):
  | 이름 | 기본(서버) | 설명 |
  |---|---|---|
  | `color` | `#000000` 취급 | 계열 색 hex 또는 `[r,g,b]` |
  | `tol` | 60 | 색 허용오차 |
  | `mode` | 선 | `"line"`(곡선) / `"points"`(표식 중심) |
  | `how` | `"all"` | 곡선 출력 방식: `all`(열마다) / `n`(점 수, 로그축은 로그 간격) / `step`(x 간격) / `markers`(선 위 표식 중심) |
  | `n` | 0 | `how="n"` 일 때 점 수(화면 기본 50) |
  | `step` | 0 | `how="step"` 일 때 x 간격(데이터 단위) |
  | `min_area` | 4 | `points` 모드 최소 표식 넓이(px) |
  | `remove_lines` | true | 격자·축선 지우기 |
  | `skip_legend` | true | 범례 견본 빼기 |
- 응답:
  ```json
  {"px": [[412.0, 233.5], ...], "data": [[0.5, 12.3], ...], "warn": [], "mask": "data:image/png;base64,...", "legend_boxes": [[...]]}
  ```
  `px` 는 찾은 픽셀 점, `data` 는 값(출력 방식대로 보간), `mask` 는 색이 잡힌 영역 오버레이 PNG(data URL).
- 메모: 화면은 `data` 를 다시 픽셀로 되돌려(`toPix`) 계열의 `px` 로 저장합니다(`points` 모드면 `px` 그대로). 내보낼 때는 이 픽셀 점을 보냅니다 — “화면에 보이는 점 = 내보내는 점”.

### POST api/export
- 요청:
  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `calib` | 객체 | 예 | 보정 |
  | `format` | 문자열 | 아니오 | `csv`(기본) / `xlsx` |
  | `series` | 배열 | 아니오 | 선·점: `[{"name", "px": [[x,y],…], "calib"?}]` (오른쪽 축 계열은 자기 `calib`). 막대: `[{"name"}]`(계열 이름만) |
  | `bars` | 배열 | 아니오 | 있으면 막대 모드 — `api/auto` 의 `bar.bars` 모양(고친 것 포함) |
  | `orient` | 문자열 | 아니오 | 막대 방향 `v`(기본)/`h` |
  | `meta` | 객체 | 아니오 | `name`(파일 이름), 그 밖의 문자열·숫자 값은 엑셀 '보정' 시트에 적힘 |
- 응답: 파일 바이트.
  - CSV: `text/csv; charset=utf-8`, UTF-8 BOM 포함, 숫자 유효숫자 6자리. 선·점은 열이 `<이름> x, <이름> y` 쌍(계열마다 x 순 정렬). 막대는 `범주, <계열들…>, [막대 끝(합계)], [<계열> 오차 +/−]`.
  - XLSX: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`. 시트: `데이터`, 계열별 시트(막대면 `막대 조각`: 범주·계열·아래 끝·위 끝·조각 값), `보정`. `openpyxl` 이 없으면 조용히 CSV 로 바뀜(파일명 확장자로 판단).
  - 파일명: `Content-Disposition: attachment; filename*=UTF-8''<meta.name>.csv|xlsx` (공백·특수문자는 `_`, 없으면 `digitized`).

### POST api/save
- 요청: `{"project": {...}}` — 내용은 서버가 그대로 저장합니다. `pid` 가 올바른 형식이면 덮어쓰고, 없거나 틀리면 새 pid 를 만듭니다. 이력 목록이 읽는 필드: `name`, `image.id`, `calib`, `series[].name`, `series[].px`.
  화면이 보내는 모양: `{"pid", "name", "image": {"id","w","h"}, "calib", "box", "excludes", "series": [...], "det", "bar"}`
- 응답: `{"pid": "2026-10-13-a1b2c3"}`

### POST api/history/delete
- 요청: `{"pid": "<pid>"}`
- 응답: 늘 `{"ok": true}` (없는 pid·틀린 형식이어도).

## 흐름 예

### 1) 그림 올리기 → 자동 보정·추출 → CSV 받기
```js
const base = '/t/digitizer-local/';
const post = (p, b) => fetch(base + 'api/' + p, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});

const data = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(file); });
let img = await (await post('image', {data})).json();            // {id, w, h}
let r = await (await post('auto', {id: img.id, vlm: true, chart: 'auto'})).json();
if (r.need_panel) {                                               // 그래프가 여럿 → 하나 골라 자르고 다시
  const p = r.panels.find(p => p.kind === 'plot');
  img = await (await post('crop', {id: img.id, box: p.crop, plot: p.box})).json();
  r = await (await post('auto', {id: img.id, vlm: true})).json();
}
const d = r.detect;                                               // d.ok, d.calib, d.series, d.warn, r.message
const body = r.mode === 'bar' && r.bar?.ok
  ? {calib: r.bar.calib, bars: r.bar.bars, orient: r.bar.orient, series: r.bar.series.map(s => ({name: s.name})), format: 'csv', meta: {name: 'fig'}}
  : {calib: d.calib, series: d.series.filter(s => s.px?.length).map(s => ({name: s.name, px: s.px, ...(s.calib ? {calib: s.calib} : {})})), format: 'csv', meta: {name: 'fig'}};
const res = await post('export', body);
const blob = await res.blob();
const name = decodeURIComponent((res.headers.get('content-disposition') || '').split("''")[1] || 'digitized.csv');
```

### 2) PDF 의 한 쪽 → 그림
```js
const pdf = await (await post('pdf', {data: pdfDataUrl})).json();            // {id, pages}
// 썸네일: <img src="/t/digitizer-local/api/pdfpage/${pdf.id}/3.png?dpi=24">
const img = await (await post('pdfpage', {id: pdf.id, page: 3, dpi: 200})).json();  // 이후 api/auto
```

### 3) AI 없이 (눈금 값 직접) + 계열 하나 수동 추출
```js
const r = await (await post('auto', {id, vlm: false, xvals: [0, 2, 4, 6], yvals: ['10^0', '10^1', '10^2']})).json();
const ex = await (await post('extract', {id, calib: r.detect.calib, box: r.detect.box, excludes: [],
  series: {color: '#d62728', tol: 60, mode: 'line', how: 'n', n: 50}})).json();   // ex.data, ex.mask
```

## 화면만의 것 / 주의
- **보정 수식·좌표 변환은 화면도 직접 계산**합니다(`ui.html` 의 `calOK/mat/toData/toPix/seriesData`). 표·재구성 그래프·소수 자릿수(`pxDec`: 2픽셀이 바뀌는 자릿수까지만 표시)는 화면 계산이고, 내려받기는 서버가 전체 정밀도로 다시 계산합니다.
- 수동 보정(X1·X2·Y1·Y2 찍기), 점 편집(추가·이동·삭제), 막대 끝 고치기, 제외 영역 그리기, 스포이트 색 고르기, 확대·돋보기, 겹쳐 보기 PNG 저장, 클립보드 복사는 **모두 화면 기능**입니다(API 없음). 결과는 `calib`·`series[].px`·`bars` 를 고쳐서 `api/export`/`api/save` 로 보내면 됩니다.
- 막대 표(`barRows`)는 화면이 만들고 서버 `bar_table` 도 같은 규칙으로 따로 만듭니다. 화면은 `api/export` 에 `table` 도 실어 보내지만 **서버는 무시**합니다.
- 작업 상태는 브라우저 `localStorage`(`digitizer-state`)에도 기억됩니다. 서버 쪽 영구 저장은 `api/save` 뿐(내려받기할 때 화면이 자동 저장).
- 공유 데이터 없음 — 그림·PDF·이력 모두 사용자별 폴더. owner 전용 엔드포인트는 없습니다.
- 정적 파일 경로(`/static/…`)는 없습니다. 화면은 `ui.html` 한 장(CDN 없음)이고, 알 수 없는 GET 경로는 모두 이 HTML 을 돌려줍니다(404 아님).
- 그림·PDF 는 지우는 API 가 없습니다(이력 삭제는 작업 JSON 만 지움).
