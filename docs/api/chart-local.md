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
| POST | `api/rerank` | 규칙 후보 순서 다시 매기기 + 더 맞는 그래프 최대 3개 제안 (LLM, 선택) | **LLM**, 수 초 (한 번 90초·합쳐 120초 제한, 넘으면 규칙 순서) | 예 (`id` 를 주면 이력에 저장) |
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
| `kind` | 그래프 종류 (필수). `api/meta` 의 `kinds` 키: `col` `bar` `col_group` `bar_group` `col_stack` `col_stack100` `bar_stack` `line` `area` `area_stack` `scatter` `pie` `doughnut` `hist` `box` `heatmap` `panels` `index` `dual` · (2026-10-20 추가) `radar` `sparkline` `candlestick` `waterfall` `funnel` `bubble` `step` `lollipop` `dumbbell` `treemap` `bullet` `rose` `stack_line` `semi_doughnut` · (2026-10-20 추가) `ohlc` — 아래 '종류별 필드' |
| `name` | 종류의 한국어 이름 (표시용) |
| `x` | x 축(범주·시간) 열 이름. 없으면 `null` (행 순서) |
| `ys` | 값 열 이름 배열 |
| `group` | 긴 형식 표를 계열로 펼칠 범주 열 (선택) |
| `y2` | `dual` 일 때 오른쪽 축 열, `stack_line` 일 때 꺾은선 열(같은 값 축) |
| `target` | `bullet` 목표 열 (가로 눈금) |
| `volume` | `candlestick`·`ohlc` 거래량 열 (거래량 칸·매물대에 씀, 선택) |
| `ma`, `bb`, `panels`, `vprofile`, `colors_updown`, `macd`, `rsi` | `candlestick`·`ohlc` 주가 지표 (2026-10-20 추가) — 아래 '주가 지표' |
| `trend_n` | 막대 추세선 이동평균 창 (`trend: "ma"`, 기본 3) |
| `size`, `label` | `bubble` 원 크기 열(양수, 넓이 ∝ 값)·점 이름 열 |
| `totals`, `show_total`, `total_label` | `waterfall`: 0부터 그리는 합계 막대로 볼 x 값 목록 · 끝 누적 합계 막대(없으면 마지막이 합계가 아닐 때만) · 그 이름(기본 `"합계"`) |
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
| `trend` | 산점도: `true` = 추세선. 막대 종류(`col` `col_group` `bar` `bar_group` `col_stack` `bar_stack`): `"linear"`(계열별 최소제곱 직선) \| `"ma"`(이동평균, 창 `trend_n`) \| `"none"` — 누적은 합계의 추세 하나 (2026-10-20 추가) |
| `zero_base`, `unit`, `colors` | 0 기준 축, 단위 표기, 계열 색 (화면 편집기에서 씀) |
| `why`, `score` | 추천 이유 문장·점수 (추천 결과에만, 그리기에는 영향 없음) |

### 종류별 필드와 문서에 들어가는 방식 (2026-10-20 추가 종류)

| kind | 이름 | 필요한 열 | XLSX | PPTX·DOCX | HWPX |
|---|---|---|---|---|---|
| `radar` | 방사형 | `x`=축 범주(3~12), `ys` 1~5 | 네이티브 radarChart | 네이티브 | 한글 차트(`hwp-native`) |
| `sparkline` | 스파크라인 | `x`=시간, `ys` 여러 개(또는 `group`) | 엑셀 스파크라인(x14, `verify.sparklines`) | 계열마다 작은 꺾은선 차트 | 그림 |
| `candlestick` | 캔들스틱 | `ys`=시가·고가·저가·종가 순서 4개, `volume` 선택 | 주식형 stockChart(+거래량 막대 차트) | 같음 | 그림 |
| `waterfall` | 폭포 | `ys[0]`=증감, `totals` | 누적 막대(투명 기준 계열·수식) | 같음 | 그림 |
| `funnel` | 깔때기 | `ys[0]`=단계 값 | 누적 가로 막대(투명 여백) | 같음 | 그림 |
| `bubble` | 거품 | `x`·`ys[0]` 숫자, `size`, `label` | bubbleChart | 같음 | 그림 |
| `lollipop` | 막대사탕 | `ys[0]` | 꺾은선 표식 + 오차 막대 줄기 | 같음 | 그림 |
| `dumbbell` | 덤벨 | `ys` 앞 2개(또는 2수준 `group`) | 꺾은선 표식 + 최고-최저 선 | 같음 | 그림 |
| `bullet` | 목표 대비 | `ys[0]`=실적, `target` | 막대 + 목표 표식(같은 축) | 같음 | 그림 |
| `stack_line` | 누적 막대+꺾은선 | `ys` 쌓을 열, `y2` 선 | 누적 막대 + 꺾은선(같은 축) | 같음 | 그림 |
| `semi_doughnut` | 반원 도넛 | `ys[0]` 양수 | 도넛 + 투명 아래 반 | 같음 | 그림 |
| `step`, `treemap`, `rose` | 계단선·트리맵·로즈 | `ys[0]` | 그림(PNG)+데이터 표 | 그림+표 | 그림 |

- 그림(`image`)은 서버의 `rsvg-convert` 로 만든 PNG — 없으면 데이터 표만(`table`). 오피스·한글 차트에 같은 형식이 없는 종류만 그림으로 넣고 `report[].modes`·`editable` 에 그대로 적음.
- 폭포에서 누적이 0 아래로 내려가는 구간은 엑셀·파워포인트에서는 맞지만 LibreOffice 는 음수 누적 막대를 위로 그림(문서 주석에 안내).
- `waterfall` 색은 `colors` = [증가, 감소, 합계], `candlestick`·`ohlc` 는 [상승, 하락] — 기본값은 `api/meta` 의 `kind_colors`(`colors_updown: "intl"` 이면 `updown_colors.intl`).

### 주가 지표 (`candlestick`·`ohlc`, 2026-10-20 추가 — 모두 추가 필드, 없으면 예전과 같음)

| 필드 | 형식 | 설명 |
|---|---|---|
| `ma` | 정수 배열 | 이동평균 창(종가 단순 평균), 예 `[5, 20, 60, 120]`. 기간 수보다 긴 창은 빠지고(응답 `note` 에 안내) 앞 창−1 칸은 빈칸 |
| `bb` | `{"period": 20, "k": 2}` \| 없음 | 볼린저 밴드 = period 일 평균 ± k × **모집단** 표준편차(n 으로 나눔, 엑셀 STDEV.P·STDEVP). 채운 띠로 가격에 겹침 |
| `panels` | `["volume","macd","rsi"]` 중 | 가격 아래 칸(같은 날짜 축). `volume` 은 거래량 열 필요(막대 색 = 그날 상승·하락), `macd` 는 기간 34개 이상(12·26·9), `rsi` 는 15개 이상(14, 와일더 평활, 30·70 기준선). **키가 없으면** 예전처럼 거래량 열이 있을 때 거래량 칸만 |
| `vprofile` | `{"bins": 24, "va": true}` \| 없음 | 매물대 — 가격 오른쪽의 가격대별 거래량. 기간마다 거래량을 그 기간 저가~고가에 **고르게 나눠** 겹치는 길이만큼 구간에 더함(고가=저가면 그 구간에 전부). POC = 최다 구간, `va` = POC 에서 위·아래 많은 쪽을 붙여 70% |
| `colors_updown` | `"kr"`(기본) \| `"intl"` | 상승 빨강·하락 파랑 \| 상승 초록·하락 빨강. `colors` 0·1 을 직접 고르면 그 색이 먼저 |
| `macd`, `rsi` | `{"fast","slow","signal"}`, `{"period"}` | 기간 바꾸기(선택) |

- 계산: EMA = SMA 로 시작(앞 n 개 평균) 후 이전 + 2/(n+1)×(값 − 이전) · MACD = EMA12 − EMA26, 시그널 = MACD 의 EMA9, 히스토그램 = 차 · RSI = 100 − 100/(1 + 평균 상승/평균 하락), 첫 평균은 14개 단순 평균, 이후 (이전×13 + 이번)/14, 평균 하락 0 이면 100(둘 다 0 이면 50).
- `ohlc` = 몸통 없는 OHLC 막대(세로선 고가~저가, 왼쪽 눈금 시가, 오른쪽 눈금 종가).
- 문서: XLSX·PPTX·DOCX 는 모두 **네이티브 차트** — 가격 = 이동평균·볼린저 꺾은선(보이는 주 축) + 주식형 stockChart(같은 범위로 고정한 숨은 보조 축, 범례에서 시가·고가·저가·종가는 지움), 거래량 = 상승·하락 두 계열 겹친 막대, MACD = 히스토그램 막대 + MACD·시그널 꺾은선(같은 축), RSI = 꺾은선 + 70·30 기준선(0~100), 매물대 = 가로 막대(가격 축을 함께 쓰지 않는 별도 차트, POC·가치 영역 색 계열). `ohlc` 문서 차트는 주식형의 고가~저가 선 + 시가(점)·종가(가로 표식, 상승·하락 색). 볼린저 띠 채움은 그림에서만(문서는 선 3개).
- **XLSX 는 지표가 엑셀 수식**: 그림 시트 표의 가격·거래량 칸은 데이터 시트 참조, 이동평균 `=AVERAGE(창)`, 볼린저 `=AVERAGE ± k*STDEVP(창)`, EMA·시그널 재귀 수식, RSI 와일더 재귀 수식, 매물대 구간 경계(`MIN`/`MAX`)·구간 거래량(`SUMPRODUCT`)·POC(`MAX`) — 데이터 시트 가격을 고치면 다시 계산됨. 창이 차기 전 칸은 `=NA()`(차트에서 빈칸). 계산값도 셀에 함께 저장(열자마자 보임, LibreOffice 재계산과 1e-9 안에서 같음 확인). 가치 영역 70% 표시만 내보낼 때 값.
- PPTX·DOCX 내장 워크북은 **계산값**(수식 아님) — 값을 고치면 차트는 바뀌지만 지표는 다시 계산되지 않음. HWPX 는 그림 + 표(지표 값 포함).

### 막대 추세선 (`trend: "linear"|"ma"`, 2026-10-20 추가)
- x = 1, 2, … (엑셀 범주 축 추세선과 같음). `linear` 는 빈 값을 뺀 점으로 최소제곱 직선, `ma` 는 앞 `trend_n` 개 평균(앞 창−1 칸 빈칸).
- 그림: 계열색을 어둡게 한 점선, 범례 `<계열> 추세`. 누적 막대는 `합계 추세` 하나.
- 문서: 묶은·한 계열 막대(세로·가로)는 **오피스 추세선**(`c:trendline` linear·movingAvg) — 값을 고치면 따라 바뀜. 누적 막대는 오피스가 추세선을 못 붙여 `합계 추세` 꺾은선 계열(XLSX 는 순서·`=SUM`·`=TREND`/`=AVERAGE` 수식 열, PPTX·DOCX 는 계산값). HWPX 는 그림 + 표.
- 추천: x 가 기간이고 막대 후보가 있으면 `"<계열> 막대 위에 계열별 추세선…"` 후보 하나(`trend: "linear"`).

### 색 프로필 (`palette`, 2026-10-20 추가 — 추가 필드, 없으면 예전과 같은 색)
- `spec.palette`: `api/meta` 의 `palettes[].id` — `default`(기본) · `pastel`(파스텔) · `vivid`(선명) · `contrast`(고대비) · `okabe`(색각이상 배려, Okabe–Ito) · `print`(흑백 인쇄) · `warm`(따뜻한 색) · `cool`(차가운 색) · `mono`(단색 계열) · `report`(보고서용), 또는 `custom` + `palette_colors: ["#rrggbb", …]`(최대 12, '내 색 프로필'·'원본 그림 색'). 모르는 id·빈 custom 은 서버가 버림(기본 색).
- 우선순위: 계열별 `colors` > `palette` > 기본. 모든 종류에 적용 — 막대·선·원 조각·누적·방사형·트리맵·히트맵(팔레트 주 색의 명도 단계 `seq`). `candlestick`·`ohlc` 는 상승·하락 관례 색을 그대로 두고 이동평균·볼린저·MACD·RSI·매물대 선만 팔레트 색(상승·하락 색과 먼 색부터). `waterfall` 은 증가·감소 색을 두고 합계만 팔레트 주 색.
- `print`(흑백 인쇄): 회색 단계 + 막대·조각·면은 **무늬**(빗금·점·격자 …), 선은 **진한 회색 + 선 모양**(점선·파선 …). 범례 견본도 같은 모양. 주가 상승·하락은 옅은/진한 회색. 오피스 문서(XLSX·PPTX·DOCX)는 네이티브 차트 XML 의 `a:pattFill`·`a:prstDash` 로 같은 구분. HWPX 한글 차트는 무늬가 없어 회색 단계(선은 진한 회색)만, 그림으로 넣는 종류는 무늬 포함.
- 문서: 오피스 네이티브 차트의 계열 채우기·선·원 조각 색, 히트맵 칠한 표·XLSX 조건부 서식 색조, HWPX 한글 차트 `colors` 가 모두 팔레트 색.
- 값 라벨 글자색은 색 칸마다 흰색·잉크 중 대비가 큰 쪽(모든 팔레트 4.4:1 이상, selftest 확인).

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
  "palette": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
  "palettes": [{"id": "default", "name": "기본", "kind": "categorical", "colors": ["#2a78d6", "…"], "seq": ["#cde2fb", "…", "#0d366b"], "desc": "…"},
               {"id": "print", "name": "흑백 인쇄", "kind": "print", "colors": ["#3d3d3d", "…"], "line_colors": ["#000000", "…"],
                "patterns": [null, "diag", "dots", "…"], "dashes": [null, "6 3", "…"], "seq": ["…"], "desc": "…"}],
  "kind_colors": {"waterfall": ["#1baf7a", "#e34948", "#2a78d6"], "candlestick": ["#e34948", "#2a78d6"], "ohlc": ["#e34948", "#2a78d6"]},
  "updown_colors": {"kr": ["#e34948", "#2a78d6"], "intl": ["#1baf7a", "#e34948"]},
  "fin": {"ma_default": [5, 20, 60, 120], "ma_max": 250, "panels": ["volume", "macd", "rsi"], "bb": {"period": 20, "k": 2},
          "macd": [12, 26, 9], "rsi": 14, "vprofile_bins": 24, "ma_colors": ["#eda100", "#e87ba4", "#4a3aa7", "#008300", "#eb6834", "#52514e"]},
  "trend_kinds": ["bar", "bar_group", "bar_stack", "col", "col_group", "col_stack"],
  "rerank": {"max_candidates": 12, "max_extra": 3, "rate_n": 10, "rate_s": 600}
}
```
- `samples` 는 13개(2026-10-20부터 주가·역량 평가·월별 손익·채용 단계·목표 대비 실적·기준금리·시도별 지표·월별 운영 지표·월별 강수량, 이어서 `stock_daily` 주가 일봉 160일(가상 데이터) 추가).
- `palettes`(2026-10-20 추가): 색 프로필 10개 — `kind` 는 `categorical`·`sequential`(한 색의 명도 단계)·`print`(흑백 인쇄, `line_colors`·`patterns`·`dashes` 도 줌). 첫째 `default` 는 `palette` 와 같은 색.
- `formats[].ok`: HWPX 는 서버에 Node + kordoc 이 있을 때만 `true`. `model` 은 `api/describe`·`api/rerank` 가 기본으로 쓰는 모델. `rerank` 는 `api/rerank` 한도(LLM 에 보내는 후보 수·제안 수·10분 호출 수).

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
  - `notes`: 서버가 자동으로 처리한 것의 한국어 설명. 값이 모두 빈 열은 빼고 알림(예: `"값이 모두 빈 열 '막대 끝' 을(를) 뺌"`).
  - `source_colors`(있을 때만, 2026-10-20 추가): 디지타이저(`digitizer-local`)가 내보낸 XLSX 의 '보정' 시트에 `['계열 색', 계열 이름, '#rrggbb']` 줄이 있으면 **값 열 이름 → 원본 그림 색**. 열 이름이 계열 이름과 같거나 `이름 y`·`이름 (…)` 처럼 그 이름으로 시작하면 짝. '데이터'(첫) 시트를 읽을 때만. `api/suggest` 의 `palette: "source"` 로 넘김.
  - 열 타입: `1월…12월`·`1분기`·`Q1`·`2026년`·`1주차` 처럼 숫자+기간 글자만 있는 열은 숫자(단위 '월')가 아니라 **기간(date)** — 값이 서로 다르거나 머리글이 범주·항목·구분·기간 같은 말일 때. `3개`·`5명` 같은 수량은 그대로 숫자. `sheets`·`sheet` 는 엑셀일 때만 값이 있고 아니면 `null`.
- 오류(400): `{"error": "표 데이터를 찾지 못했습니다"}`, `"파일이 너무 큽니다 (20MB 이하)"`, `.xls` 안내 등.
- 메모: 업로드는 multipart 가 아니라 **JSON 안의 base64** 입니다. 디코드 후 20MB, 요청 본문 약 28MB 를 넘으면 413 `{"error": "요청이 너무 큽니다 (파일 20MB 이하)"}`(포털을 거쳐도 413).

### POST api/suggest
표에서 그래프 후보(최대 12개)를 규칙으로 추천하고 각각 SVG(720×440)를 그립니다. 이력에 표와 후보를 저장합니다.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `id` | 문자열 | 아니오 | 기존 이력 id. 있으면 그 이력을 갱신, 없거나 못 찾으면 새 이력 |
| `source` | 문자열 | 아니오 | 출처 메모 (예: `"붙여넣기"`, 파일 이름) — 이력 목록에 표시 |
| `palette` | 문자열 | 아니오 | 모든 후보에 적용할 색 프로필 id, `custom`(`palette_colors` 함께), `source`(`source_colors` 함께 — 후보의 값 열 이름으로 색을 찾아 `custom` 으로 바꿈, 그룹 계열 후보는 기본 색). 모르는 값 400 (2026-10-20 추가) |
| `palette_colors` | `#rrggbb` 배열 | `custom` 일 때 | 최대 12 |
| `source_colors` | {열 이름: `#rrggbb`} | `source` 일 때 | `api/parse` 의 `source_colors` |

- 응답:
```json
{"id": "20261013-101500-ab12",
 "candidates": [{"id": "c1", "kind": "line", "name": "꺾은선", "x": "연도", "ys": ["예산"],
                 "why": "…", "score": 90, "title": "연도별 예산 추이", "legend": "top", "labels": "auto",
                 "svg": "<svg …>…</svg>"}]}
```
  - 후보는 점수 높은 순. 이력에는 `svg` 를 뺀 후보가 저장됨. 규칙 후보는 `source: "rule"`.
  - `rerank`(있을 때만): `id` 의 이력에 저장된 AI 순위(`api/rerank` 결과)가 지금 표·후보와 같으면 `stored: true` 로 함께 돌려줌(AI 제안 `svg` 는 다시 그림). 화면은 이때 LLM 을 다시 부르지 않음. 표가 바뀌면 저장된 AI 순위를 지움.
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
- 오류(400): `"LLM 서버 연결 실패: …"`(서버 주소는 넣지 않음), `"LLM HTTP 500: …"` 등.
- 메모: 스트리밍 없음, 한 번에 응답. 모델 크기에 따라 수 초~수십 초 (서버 측 타임아웃 600초). 화면은 결과의 `title` 로 바꾼 뒤 각 후보를 `api/render` 로 다시 그림.

### POST api/rerank  (LLM, 선택)
규칙 후보를 먼저 보여 준 뒤 따로 부르는 단계입니다. 로컬 LLM 이 **보낸 후보 안에서만** 순서를 정하고, 지원 종류(`api/meta` 의 `kinds`) 중 더 알맞은 것이 있으면 **최대 3개 제안**합니다. 서버가 모두 검증하고, LLM 이 안 되면 오류 대신 규칙 순서를 돌려줍니다.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `table` | 표 | 예 | |
| `candidates` | 설정 배열 | 아니오 | `api/suggest` 후보에서 `svg` 를 뺀 것(`id` = `c숫자`, `kind` 필수, 최대 60). 없으면 서버가 규칙 후보를 다시 만듦. LLM 에는 앞 12개만 보내고 나머지는 순서 뒤에 그대로 |
| `id` | 문자열 | 아니오 | 이력 id — 주면 결과(`ok` 일 때)를 이력 `rerank` 에 저장 |
| `model` | 문자열 | 아니오 | 모델 이름 (기본 `api/meta` 의 `model`) |
| `palette`, `palette_colors`, `source_colors` | | 아니오 | `api/suggest` 와 같음 — AI 제안(`extra`) 그림에 적용(제안이 스스로 아는 팔레트 id 를 고르면 그것). AI 제안 명세의 `palette` 는 아는 id 만 받음 (2026-10-20 추가) |

- 응답(200):
```json
{"ok": true, "order": ["c1", "c3", "c10", "c4", "c2", "c8", "c6", "c5", "c7", "c9"],
 "reasons": {"c1": "역량별 강점과 약점을 한눈에 비교하기에 가장 알맞음"},
 "extra": [{"id": "a1", "source": "ai", "kind": "dumbbell", "name": "덤벨 (두 값 비교)", "x": "역량", "ys": ["김연구", "팀 평균"],
            "why": "역량마다 개인 점수와 팀 평균의 차이를 길이로 비교", "title": "역량별 김연구와 팀 평균 비교",
            "legend": "top", "labels": "auto", "svg": "<svg …>…</svg>"}],
 "model": "<모델 이름>", "dropped": {"ids": 0, "reasons": 0, "extra": 1}, "notes": ["조건에 맞지 않거나 겹치는 제안 1개는 버림"],
 "changed": true, "tries": 1, "sig": "…"}
```
  - `order`: 보낸 규칙 후보 id 의 새 순서(각 한 번). 모르는·겹친 id 는 버리고(`dropped.ids`), 빠진 id 는 규칙 순서대로 뒤에. AI 제안은 `order` 에 없음 — 화면은 맨 뒤에 붙임.
  - `reasons`: 일부 후보의 AI 이유. 한국어 90자 이하, 표에 없는 숫자가 있거나 한국어 문장이 아니면 버림(`dropped.reasons`). 영문 종류 코드는 한국어 이름으로 바꿈.
  - `extra`: AI 제안(최대 3, `a1`~`a3`, `source: "ai"`, `why` = AI 이유). 서버 검사 — 지원 종류, 열 존재·타입(값 열은 숫자, x 는 범주·날짜 / 산점도·거품은 숫자), 종류별 필요한 열(캔들 4열 순서·이중축 `y2`·누적+선 `y2` 같은 단위·불릿 `target` 같은 단위·거품 `size` 양수 등), 규칙과 같은 부적합 막기(원그래프·도넛·반원 2~6개·양수, 음수 있으면 누적·면적·방사형 금지, 시가·고가·저가·종가 쌓기·묶기 금지, 폭포는 양·음 섞임, 깔때기는 줄어드는 값, 로즈는 주기 범주, 항목 수 상한), **SVG 와 문서 블록을 실제로 만들어 보기**, 이미 있는 후보와 같은 `kind`+`x`+`ys`+`group` 제외. 통과 못 하면 `dropped.extra`. 내보내기(`api/export`)는 다른 설정과 같음.
  - `ok: false` — LLM 연결 실패·시간 초과·형식 두 번 틀림: `order` = 규칙 순서, `reasons`·`extra` 빔, `note` 에 한 줄 안내(예: `"AI 순위를 받지 못해 규칙 순서를 그대로 둡니다"`). 오류 코드가 아님.
- 오류: 표·후보 형식 400, 사용자당 동시 1개 넘음 429 `{"code": "busy"}`, 10분 10번 넘음 429 `{"code": "rate_limited"}`(`Retry-After: 60`).
- LLM 에 보내는 것: 표 요약(열 이름·타입·단위·최소/최대/첫/끝 값·음수 수, 범주 값 8개까지 — 값 하나 40자·전체 3000자), 열 목록, 후보(종류 한국어 이름·열·규칙 이유·점수), 허용 종류와 한 줄 쓰임. 표 내용은 `<<<TABLE_DATA>>>` … `<<<END_TABLE_DATA>>>` 사이 데이터로만(안의 `<<<`·`>>>` 는 지움), 그 안의 지시문은 따르지 말라고 알림.
- LLM 호출: JSON 스키마 안내 디코딩(OpenAI 호환 `response_format: json_schema` · Ollama `format`, 후보 id·종류·열 이름은 enum). 형식이 틀리거나 잘리면 한 번 더(JSON 객체 방식 + 앞 오류 알려 줌). 도구 전체 동시 LLM 2개.

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
  - `report[].modes`: 그림마다 들어간 방식 — `native`(네이티브 차트, 데이터 편집 가능), `table`(색 칠한 표, 또는 그림 변환기가 없을 때 데이터 표), `image`(PNG + 데이터 표), `hwp-native`(한글 차트 개체). `editable` 은 `image` 가 없으면 `true`.
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
- **이력 저장 시점**: `api/suggest` 와 `api/export` 가 이력을 만들거나 고칩니다. `api/rerank` 는 `id` 를 줬을 때 그 이력의 `rerank` 만 고칩니다. `api/parse`·`api/render`·`api/describe` 는 저장하지 않습니다.
- **AI 순위 화면**: 규칙 후보를 먼저 그리고 `api/rerank` 를 따로 부릅니다(첫 화면을 막지 않음). 카드에 'AI 추천 순위 N'·'AI 제안' 배지, '규칙 순서로' 로 원래 순서. 실패면 규칙 순서 + 작은 안내.
- **SVG**: 서버가 만든 SVG 문자열을 그대로 DOM 에 넣습니다. 색은 `api/meta` 의 `palette` 순서.
- **권한**: owner 전용 엔드포인트 없음. 각 사용자는 자기 이력·파일만 보입니다(`X-Agent-User`). 공유 데이터는 샘플(`api/sample/*`)뿐.
- **정적 파일**: `/static/…` 없음. 화면은 `ui.html` 한 파일이고, `api/…` 가 아닌 모든 GET 경로가 이 HTML 을 돌려줍니다(없는 GET 경로도 404 가 아니라 200 HTML).
- **POST 경로는 정확히 일치해야 함**: 서버가 쿼리 문자열을 떼지 않고 경로를 비교하므로 `api/parse?x=1` 처럼 보내면 404 `{"error": "없는 경로"}` 입니다.
- 오류 응답은 항상 `{"error": "한국어 메시지"}` — 입력 문제·LLM 실패 400(`api/rerank` 의 LLM 실패는 200 + `ok: false`), 동시·횟수 제한 429(`api/rerank`, `code`), 크기 초과 413, 없음 404, 그 밖 500(`"예외이름: 메시지"`).
- 명령줄 사용(LLM 없이 추천 상위 4개를 문서로): `python3 app.py --cli 표.csv pptx 결과.pptx`.
