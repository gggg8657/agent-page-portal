# 차폐 계산기 API (`shield-local`)
방사선 차폐·선량률 간이 계산(감마 점선원·평판 차폐, 베타 최대비정, 중성자 비차폐 선량률). 계산은 코드가 결정론적으로 하고, LLM 은 자연어 질문(`api/ask`)에서 질문 → 입력값 변환과 결과 설명에만 씁니다.

포털 경로 접두어: `/t/shield-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/calc` → `http://<서버>/t/shield-local/api/calc`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | - | - |
| GET | `api/meta` | 핵종·재질·단위·근사 규칙 목록, LLM 연결 여부 | LLM 서버 확인(최대 2초) | - |
| GET | `api/nuclide?n=<핵종>` | 핵종 광자선 원자료와 Γ 상수 | - | - |
| GET | `api/validation` | 문헌값 대조표(검증 결과) | - | - |
| GET | `api/history` | 내 계산 이력 목록 | - | 예 |
| GET | `api/history/<id>` | 이력 한 건(요청+결과 전체) | - | 예 |
| GET | `api/report/<id>.md` | 이력 한 건의 Markdown 보고서 다운로드 | - | 예 |
| POST | `api/calc` | 감마 차폐 계산 (이력에 자동 저장) | 빠름(1초 안팎) | 예(저장) |
| POST | `api/other` | 베타(`mode:"beta"`) / 중성자(`mode:"neutron"`) 계산 (이력 저장) | 빠름 | 예(저장) |
| POST | `api/history/delete` | 이력 한 건 삭제 | - | 예 |
| POST | `api/ask` | 자연어 질문 → 변환·계산·설명 (**SSE 스트리밍**) | LLM 2회, 수 초~수십 초 | 예(계산되면 저장) |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) HTML | - | - |

공통
- 요청 본문은 JSON(`Content-Type: application/json`). 응답은 JSON(`ensure_ascii=False`, UTF-8), `Cache-Control: no-store`.
- 오류 응답은 `{"error": "메시지"}`. 상태 코드는 아래 "오류 규칙" 참고.
- 업로드·파일 첨부는 없습니다.

### 오류 규칙 (코드 기준)
- GET: 없는 이력·잘못된 id·없는 핵종·보고서 불가 → **404** `{"error": "없음"}` (구체 사유는 안 줌). 그 밖의 예외 → 500 `{"error": "<예외이름>: <메시지>"}`.
- POST(`calc`·`other`·`history/delete`): 입력 오류(`ValueError`) → **400** `{"error": "<한국어 사유>"}`, 그 밖의 예외 → 500. 본문이 JSON 이 아니면 400 `{"error": "잘못된 요청"}`. 없는 POST 경로 → 404 `{"error": "없는 경로"}`.
- `api/ask` 는 항상 200 으로 SSE 를 열고, 오류는 스트림 안 `{"error": ...}` 이벤트로 옵니다.

## 공통 값 형식
값과 단위는 `{"value": 숫자, "unit": "단위"}` 객체로 주고받습니다.

| 종류 | 허용 단위 (`api/meta` 의 키) |
|---|---|
| 방사능 `act_units` | `Bq` `kBq` `MBq` `GBq` `TBq` `Ci` `mCi` `uCi` |
| 길이 `len_units` | `mm` `cm` `m` `in` |
| 선량률 `rate_units` | `Sv/h` `mSv/h` `uSv/h` `nSv/h` `Gy/h` `mGy/h` `uGy/h` `nGy/h` |
| 시간 `time_units` | `s` `min` `h` `d` `wk` `y` |

- μ 는 `u` 로 씁니다(`uSv/h`). 서버도 `µ`/`μ` 를 `u` 로 정규화합니다.
- 결과 숫자의 기본 단위: 선량률 **Sv/h**(hstar) 또는 **Gy/h**(kerma), 길이 **cm**, 방사능 **Bq**, 시간 **h**.
- `quantity`: `"hstar"`(주위선량당량률 H*(10), 기본) 또는 `"kerma"`(공기커마율).
- `rule`(다층 축적인자 근사): `"max"`(기본, 보수적) · `"last"` · `"none"`(좁은 빔).
- 재질 키: `lead` `tungsten` `iron` `ss304` `copper` `aluminium` `concrete` `water` `polyethylene` (정확한 목록·밀도는 `api/meta` 의 `materials`).

## 엔드포인트

### GET api/health
- 응답: `{"ok": true}`

### GET api/meta
화면 초기화에 필요한 목록 전부.
- 응답 (줄임):
```json
{"version": "1.0",
 "nuclides": {"Cs-137": {"half_life_s": 947300000.0, "note": "Ba-137m 661.7 keV 포함(Cs-137 붕괴당)", "lines": 5}, "...": {}},
 "materials": {"lead": {"name": "납 (Pb)", "rho": 11.35, "buildup": "lead"}, "...": {}},
 "act_units": ["Bq", "..."], "len_units": ["mm", "cm", "m", "in"], "rate_units": ["Sv/h", "..."], "time_units": ["s", "..."],
 "rules": {"max": "층별 재질 B(총 mfp) 중 최댓값 (보수적)", "last": "...", "none": "..."},
 "quant": {"kerma": ["공기커마율", "Gy/h"], "hstar": ["주위선량당량률 H*(10)", "Sv/h"]},
 "limits": [{"id": "worker", "name": "방사선작업종사자", "annual_mSv": 50.0, "note": "..."}, {"id": "visitor", ...}, {"id": "public", ...}],
 "beta": ["Sr-90", "..."], "neutron": {"Cf-252": "설명", "Am-Be": "설명"},
 "sources": ["데이터 출처 문자열", "..."], "disclaimer": "간이 계산 결과입니다. ...",
 "model": "<LLM 모델 이름>", "llm": true}
```
- `nuclides`: 감마 핵종(키가 계산 요청의 `source.nuclide`). `lines` 는 광자선 개수.
- `materials[*].name` 은 화면 표시명, `rho` 기본 밀도 g/cm³, `buildup` 축적인자에 쓰는 재질.
- `beta`: `api/other` 베타 모드에 쓸 핵종 키. `neutron`: 중성자 선원 키 → 설명.
- `llm`: LLM 서버가 응답하는지(false 면 `api/ask` 가 실패함). 이 확인 때문에 LLM 이 꺼져 있으면 최대 2초 걸릴 수 있음.

### GET api/nuclide?n=<핵종>
- 쿼리: `n` = 감마 핵종 키(예: `Cs-137`). 서버가 URL 디코딩을 하지 않으므로 키에 특수문자가 없는 지금 목록에서는 그대로 넣으면 됩니다.
- 응답:
```json
{"half_life_s": 947300000.0,
 "photons": [[0.0318174, 0.0199, "X", "Cs-137"], [0.6616553, 0.8501, "g", "Cs-137"]],
 "note": "Ba-137m 661.7 keV 포함(Cs-137 붕괴당)", "ddep_ref": ["Cs-137: CEA/LNE-LNHB - 2023"],
 "gamma_const": 76.87, "gamma_const10": 76.87}
```
- `photons`: `[에너지 MeV, 붕괴당 방출률(분율), 종류 "g"|"X", 출처 핵종]`.
- `gamma_const` / `gamma_const10`: 공기커마율 상수 Γ (μGy·m²/(GBq·h)), 차단에너지 20 keV / 10 keV.
- 오류: 없는 핵종 → 404 `{"error": "없음"}`.

### GET api/validation
- 응답: 배열. 각 항목
```json
{"group": "감쇠계수", "name": "납 1 MeV μ/ρ", "calc": 0.07102, "ref": 0.07102, "unit": "cm²/g",
 "source": "NIST XCOM / Hubbell-Seltzer 표", "tol": 0.005, "err": 2.2e-16, "ok": true, "note": "간섭성 포함(표 값)"}
```
- `err` 는 상대오차, `tol` 허용 상대오차, `ok = err <= tol` 성격. 화면은 처음 열 때 한 번만 부름.

### POST api/calc
감마 점선원 + 평판 다층 차폐 계산. 성공하면 이력에 `kind:"calc"` 로 저장되고 응답에 `id` 가 붙습니다.

- 요청:

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `source.nuclide` | 문자열 | 예* | 감마 핵종 키(`api/meta` 의 `nuclides`). `custom` 이 있으면 무시 |
| `source.custom` | `[[E_MeV, 방출률분율], ...]` 또는 null | 아니오 | 사용자 정의 광자선. E 0.01~10 MeV, 방출률 > 0. 화면은 keV·% 로 받아 MeV·분율로 바꿔 보냄 |
| `source.activity` | `{value, unit}` | 예 | 방사능, value > 0 |
| `source.cutoff_kev` | 숫자 | 아니오 | 광자 차단에너지(기본 20). 이보다 낮은 광자선은 제외 |
| `distance` | `{value, unit}` | 예 | 선원–검출점 거리, value > 0 |
| `layers` | 배열 | 아니오 | 선원 쪽부터 차폐층 `{material, thickness, unit?(기본 cm), density?(null=기본 밀도)}`. 두께 0 층은 무시 |
| `quantity` | `"hstar"`\|`"kerma"` | 아니오 | 기본 `hstar` |
| `rule` | `"max"`\|`"last"`\|`"none"` | 아니오 | 기본 `max` |
| `target` | `{value, unit}` 또는 null | 아니오 | 목표 선량률 → 필요 추가 두께·거리 역산 |
| `solve_material` | 재질 키 | 아니오 | 역산·반가층·두께 그래프에 쓸 재질(기본: 마지막 층 재질, 층이 없으면 `lead`) |
| `solve_density` | 숫자 | 아니오 | 위 재질 밀도(기본: 재질 기본값). 화면은 보내지 않음 |
| `time` | `{value, unit}` 또는 null | 아니오 | 작업시간 → 누적선량·선량한도 비율 |
| `decay` | bool | 아니오 | 누적선량에 붕괴 보정(기본 true) |

- 응답 (줄임):
```json
{"id": "20261007-065126-1fe2", "version": "1.0",
 "quantity": "hstar", "quantity_name": "주위선량당량률 H*(10)", "unit": "Sv/h", "rule": "max", "rule_name": "...",
 "nuclide": "Cs-137", "half_life_s": 947300000.0, "note": "...",
 "activity_bq": 3.7e8, "activity_in": {"value": 10, "unit": "mCi"}, "r_cm": 30.0, "distance_in": {"value": 30, "unit": "cm"}, "cutoff_kev": 20.0,
 "layers": [{"mat": "lead", "t_cm": 1.0, "rho": 11.35, "name": "납 ..."}], "n_lines": 5, "gamma_const": 76.87,
 "unshielded": {"kerma": 3.2e-4, "hstar": 3.8e-4}, "shielded": {"kerma": 1.2e-4, "hstar": 1.48e-4}, "transmission": 0.39,
 "lines": [{"E_keV": 661.66, "y": 0.8501, "kind": "g", "origin": "Cs-137", "k0": 0.0, "h0": 0.0, "mfp": 0.0, "B": 0.0, "gp": "lead", "k": 0.0, "h": 0.0, "share": 0.99}],
 "warnings": ["..."], "disclaimer": "...",
 "steps": [{"t": "방사능 환산", "f": "A = 값 × 단위", "v": "A = 10 mCi = 3.7e+08 Bq ..."}],
 "target": {"value": 2.5e-6, "in": {"value": 2.5, "unit": "uSv/h"}, "material": "lead", "material_name": "...", "rho": 11.35,
            "t_cm": 3.1, "r_cm_for_target": 230.0, "already": false},
 "hvl": {"hvl_broad": 0.0, "tvl_broad": 0.0, "hvl_narrow": 0.0, "tvl_narrow": 0.0, "tvl_eq": 0.0, "material": "lead", "material_name": "...", "rho": 11.35},
 "time": {"hours": 2.0, "in": {"value": 2, "unit": "h"}, "decay": true, "dose": {"kerma": 0.0, "hstar": 0.0},
          "limits": [{"id": "worker", "name": "방사선작업종사자", "annual_mSv": 50.0, "note": "...", "frac": 0.0001}, "..."], "decay_note": "..."},
 "curve_t": {"material": "lead", "material_name": "...", "points": [[0.0, 1.48e-4], "... 61점 [추가두께 cm, 선량률]"]},
 "curve_r": {"points": [["거리 cm", "선량률"], "... 61점 (로그 간격)"]}}
```
(숫자는 모양 설명용 예시입니다.)
- 필드 설명
  - `unshielded` / `shielded`: 비차폐·차폐 후 선량률(kerma=Gy/h, hstar=Sv/h). 두 양이 항상 함께 옴. `transmission` = 차폐 후 / 비차폐(선택한 `quantity` 기준).
  - `lines`: 차폐 후 기여 큰 순 최대 40개. `k0`/`h0` 비차폐, `k`/`h` 차폐 후, `mfp` 광학두께, `B` 축적인자, `gp` 축적인자에 쓴 재질, `share` 기여 비율.
  - `steps`: 단계별 계산 근거 `{t: 제목, f: 식, v: 대입값}` (상위 1~3개 광자선 + 합계).
  - `target`: `target` 을 줬을 때만. `t_cm` 필요한 **추가** 두께(cm, 찾지 못하면 null), `r_cm_for_target` 차폐 그대로 거리만 늘릴 때 필요한 거리(이미 목표 이하이면 null), `already` 이미 목표 이하인지.
  - `hvl`: 항상 옴. 넓은 빔(첫 층)·좁은 빔 반가층/1/10가층, `tvl_eq` 평형 1/10가층(cm).
  - `time`: `time` 을 줬을 때만. `dose` 누적선량(Sv 또는 Gy), `limits[].frac` 연간 한도 대비 비율(H*(10) 기준). `decay_note` 는 붕괴 영향이 클 때만.
  - `curve_t`: `solve_material` 을 추가로 덧댈 때 두께-선량률 61점. `curve_r`: 차폐 그대로 거리-선량률 61점(역제곱).
- 오류(400) 예: `방사능 값을 입력하세요`, `거리를 입력하세요`, `지원하지 않는 핵종: X`, `재질을 모름: X`, `차단에너지 이상 광자선이 없습니다 ...`, `차폐 두께 합(...)이 선원–검출점 거리(...) 이상입니다`, `사용자 정의 광자: 에너지 0.01~10 MeV, 방출률 > 0`.
- 메모: 동기 응답, 보통 1초 이내. 스트리밍 없음.

### POST api/other
베타 또는 중성자. 성공하면 이력에 `kind:"beta"` 또는 `kind:"neutron"` 으로 저장되고 `id` 가 붙습니다.

**베타** — 요청 `{"mode": "beta", "nuclide": "<api/meta 의 beta 키>"}`
```json
{"id": "...", "mode": "beta", "key": "Sr-90", "half_life_s": 0.0,
 "branches": [{"origin": "Sr-90", "Emax_MeV": 0.546, "range_gcm2": 0.18,
               "thickness_mm": {"PMMA (아크릴)": 1.5, "알루미늄": 0.7, "물·조직": 1.8, "유리": 0.7, "납": 0.16},
               "brems_frac": {"PMMA (아크릴)": 0.0012, "...": 0.0}}],
 "disclaimer": "..."}
```
- `range_gcm2` 최대비정(g/cm², Katz–Penfold), `thickness_mm` 재질별 필요 두께(mm), `brems_frac` 제동복사 전환 비율(분율). 재질 이름(키)은 한국어 표시명 그대로.
- 오류: 없는 핵종 → 400 `베타 핵종을 모름: X`.

**중성자** — `mode` 가 `"beta"` 가 아니면 모두 중성자로 처리합니다.

| 필드 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `mode` | `"neutron"` | - | |
| `source` | `"Cf-252"`\|`"Am-Be"` | 예 | `api/meta` 의 `neutron` 키 |
| `amount` | `{value, unit}` | 예 | unit: `n/s`, `ug`/`mg`/`g`(Cf-252 만), 방사능 단위(`Bq`·`Ci` 등) |
| `distance` | `{value, unit}` | 아니오 | 기본 100 cm |
| `time` | `{value, unit}` | 아니오 | 누적선량 |

```json
{"id": "...", "mode": "neutron", "source": "Cf-252", "r_cm": 100.0,
 "S_n_per_s": 2.31e6, "phi": 18.4, "h_pSv_cm2": 385.0, "hstar": 2.5e-5, "note": "...",
 "dose": 5.1e-5, "limits": [{"id": "worker", "name": "방사선작업종사자", "annual_mSv": 50.0, "note": "...", "frac": 0.000001}, "..."],
 "disclaimer": "...", "shielding": "중성자 차폐 계산은 지원하지 않습니다(...)"}
```
- `hstar` 비차폐 H*(10) 률(Sv/h), `phi` 플루언스율(cm⁻²s⁻¹). `dose`·`limits` 는 `time` 을 줬을 때만. **중성자 차폐 두께 계산은 없음.**
- 오류: 400 `중성자 선원을 모름: X`, `중성자 선원 양 단위: ...`.

### GET api/history
- 응답: 최신순 배열(최대 300건).
```json
[{"id": "20261007-065126-1fe2", "ts": "2026-10-07T06:51:26", "kind": "calc", "title": "Cs-137 10 mCi · 30 cm · 납 1 cm → 148.2 μSv/h"}]
```
- `kind`: `calc` | `ask` | `beta` | `neutron`. `ask` 의 제목은 `"Q: <질문 앞 60자>"`.

### GET api/history/<id>
- `<id>` 형식: `YYYYMMDD-HHMMSS-xxxx`(xxxx 는 16진 4자리).
- 응답: `{"id", "ts", "kind", "title", "request": <보낸 요청 본문>, "result": <그때의 응답>}`. `ask` 의 `request` 는 LLM 이 변환한 계산 요청에 `question` 이 더해진 것.
- 오류: 형식이 틀리거나 없으면 404 `{"error": "없음"}`.

### GET api/report/<id>.md
- 응답: `text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="shield-<id>.md"`. 입력·결과·계산 근거·광자선별 기여·경고·데이터 출처.
- `kind` 가 `calc`/`ask` 인 이력만. 베타·중성자 이력이나 없는 id → 404 `{"error": "없음"}`.
- 화면은 `<a href="api/report/<id>.md" download>` 링크로 받음.

### POST api/history/delete
- 요청: `{"id": "<이력 id>"}`
- 응답: `{"ok": true}` (없는 id 도 ok).
- 오류: id 형식이 틀리면 400 `잘못된 이력 id`.

### POST api/ask  (SSE 스트리밍)
자연어 질문 하나를 LLM 으로 입력값으로 바꾸고 → 코드가 단위·키·숫자를 검사 → 통과하면 `calc` 와 같은 계산 → LLM 이 설명을 쓰고 → 설명 속 숫자를 결과와 대조합니다.

- 요청: `{"question": "Cs-137 10 mCi 30 cm 거리에서 2.5 μSv/h 이하로 하려면 납 몇 mm?"}`
- 응답: `Content-Type: text/event-stream`. 각 이벤트는 `data: <JSON>\n\n` 한 줄(이벤트 이름 없음). POST 이므로 `EventSource` 대신 `fetch` + `body.getReader()` 로 읽어야 합니다.
- 이벤트 종류(순서대로):

| 이벤트 | 뜻 |
|---|---|
| `{"stage": "parse"}` | 질문 해석 시작 |
| `{"token": "...", "ch": "parse"}` | 해석 LLM 원문 토큰(JSON 조각) — 여러 번 |
| `{"parsed": {"request": {...}, "issues": ["..."]}, "raw": "..."}` | 변환 결과. `request` 는 `api/calc` 요청 형식(+`ask`: `thickness`/`rate`/`distance`/`dose`). `issues` 는 빠진 정보·모르는 단위·질문에 없는 숫자 경고 |
| `{"stage": "calc"}` | 계산 시작 (검사 통과 시만) |
| `{"result": {...calc 응답...}, "id": "<이력 id>"}` | 계산 결과(`api/calc` 응답과 같은 모양, `id` 는 결과 밖에 따로) |
| `{"stage": "explain"}` | 설명 시작 |
| `{"token": "...", "ch": "explain"}` | 설명 토큰 — 여러 번 |
| `{"done": {...}}` | 끝. 아래 참고 |
| `{"error": "..."}` | 오류(빈 질문, LLM 연결 실패, 계산 입력 오류 등). 이후 스트림 종료 |

- `done` 내용
  - 계산함: `{"parsed", "result", "id", "explain": "설명문", "summary": "LLM 에 준 결과 요약", "stray_numbers": ["설명에만 있고 결과에 없는 숫자"]}` — `stray_numbers` 가 비어 있지 않으면 화면은 빨간 경고를 띄움.
  - 계산 안 함(정보 부족·확인 필요, 또는 두께를 묻는데 목표 선량률이 없음): `{"parsed", "result": null, "explain": null, "message": "..."}`. 이 경우 `result`·`explain` 이벤트가 오지 않습니다.
- 메모: LLM 호출 2회(해석·설명). 수 초~수십 초(LLM 이 다른 일로 바쁘면 더). 계산까지 가면 이력에 `kind:"ask"` 로 저장.

## 흐름 예

### 1) 계산 탭 — 감마 계산 + 보고서
```js
const P = '/t/shield-local/';
const meta = await (await fetch(P + 'api/meta')).json();          // 핵종·재질·단위
const r = await fetch(P + 'api/calc', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    source: {nuclide: 'Cs-137', activity: {value: 10, unit: 'mCi'}, cutoff_kev: 20},
    distance: {value: 30, unit: 'cm'},
    layers: [{material: 'lead', thickness: 10, unit: 'mm', density: null}],
    quantity: 'hstar', rule: 'max',
    target: {value: 2.5, unit: 'uSv/h'}, solve_material: 'lead',
    time: {value: 2, unit: 'h'}, decay: true})});
const res = await r.json();
if (!r.ok) throw new Error(res.error);
console.log(res.shielded.hstar * 1e6, 'μSv/h', res.target?.t_cm, 'cm 더');
location.href = P + `api/report/${res.id}.md`;                     // MD 보고서 다운로드
```

### 2) 자연어 질문 (SSE 읽기)
```js
const r = await fetch('/t/shield-local/api/ask', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({question: 'Cs-137 10 mCi 30 cm 에서 2.5 μSv/h 이하로 하려면 납 몇 mm?'})});
const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
for (;;) {
  const {done, value} = await rd.read(); if (done) break;
  buf += dec.decode(value, {stream: true});
  let i;
  while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data:')) continue;
    const ev = JSON.parse(line.slice(5));
    if (ev.error) throw new Error(ev.error);
    if (ev.parsed) showInputs(ev.parsed);              // LLM 이 읽은 입력값 + issues
    if (ev.result) showResult(ev.result, ev.id);
    if (ev.token && ev.ch === 'explain') appendExplain(ev.token);
    if (ev.done) finish(ev.done);                       // result=null 이면 message 표시
  }
}
```

## 화면만의 것 / 주의
- **단위 표시·서식은 화면이 계산**: API 는 선량률을 Sv/h·Gy/h, 길이를 cm 기본 단위 숫자로 줍니다. μSv/h·mm 등으로 바꿔 보여 주는 `fmtRate`·`fmtLen`·`fmtDose`·`fmtTime` 은 화면 코드에만 있습니다(보고서 MD 는 서버가 서식함).
- 사용자 정의 광자선 입력(keV·%)을 MeV·분율로 바꾸는 것, 선량 양 전환(hstar/kerma) 시 목표 단위 목록을 Sv/Gy 로 거르는 것은 화면 일.
- "계산 탭에 채우기": `api/ask` 의 `parsed.request` 를 그대로 `api/calc` 요청 양식에 넣으면 됩니다.
- 이력 링크: 화면은 `#h=<이력 id>` 해시로 열면 `api/history/<id>` 를 불러 다시 표시합니다(감마 이력만).
- PDF 저장은 브라우저 인쇄(`window.print()`)이며 API 가 없습니다. 두께·거리 그래프도 `curve_t`·`curve_r` 점으로 화면이 그립니다.
- **사용자별 데이터**: 이력은 포털이 넘기는 `X-Agent-User` 헤더 기준으로 사용자 폴더에 따로 저장됩니다. 다른 사용자의 이력은 보이지 않고, 서버 주인(owner)은 예전 공용 기록을 봅니다. 포털을 거치면 헤더는 포털이 붙이므로 프론트엔드는 신경 쓸 필요 없습니다.
- 권한: owner 전용 엔드포인트는 없습니다.
- 정적 파일 경로(`/static/…`)는 없습니다. 화면은 `ui.html` 한 파일이며, `api/` 가 아닌 GET 경로는 모두 이 HTML 을 돌려줍니다.
- 모든 결과에 `disclaimer`(간이 계산 경고)가 들어 있으니 새 화면에서도 표시를 권장합니다.
