# 상장 API (`award-local`)
상장·표창장·감사장을 A4(300dpi)로 그려 PDF/PNG/ZIP 으로 발급합니다. 한 장 또는 명단 일괄. LLM 은 문구 다듬기에만(선택).
포털 경로 접두어: `/t/award-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/issue` → `https://<서버>/t/award-local/api/issue`)

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- 포털이 `X-Agent-User` 를 붙여 사용자별 폴더(`WORKSPACE/u/<id>`)를 씁니다(업로드 이미지·발급본·이력·다음 번호가 사용자마다 따로).
- POST 본문은 모두 JSON, `Content-Length` 필수. 모든 응답에 `Cache-Control: no-store`.
- 오류는 `{"error":"..."}` — 형식 오류·없는 파일·LLM 실패 등 대부분 `500`(예외 이름 앞에 붙음, 예 `"UnidentifiedImageError: ..."`), 일부만 `400`/`404`, 크기 초과는 `413`.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | 아니오 | 아니오 |
| GET | `api/meta` | 디자인·종류·예시 문구·다음 번호·오늘 | 아니오 | 예(다음 번호) |
| GET | `api/models` | LLM 모델 목록(없으면 `[]`) | LLM 서버 조회(3초 제한) | 아니오 |
| GET | `api/history` | 발급 이력(최근 500장) | 아니오 | 예 |
| GET | `api/issued/<id>.pdf` | 발급본 PDF 다시 받기 | 아니오 | 예 |
| GET | `api/thumb/<design>.jpg` | 디자인 견본 썸네일 | 처음 한 번 그림 | 아니오 |
| GET | `api/upload/<uid>` | 올린 로고·직인 이미지(PNG) | 아니오 | 예 |
| POST | `api/preview` | 미리보기 JPEG(90dpi) | 약 0.x초 | 아니오(읽기만) |
| POST | `api/issue` | 발급(PDF/PNG/ZIP) + 이력 기록 | 장수에 비례(일괄은 수 초~) | 예(저장) |
| POST | `api/upload` | 로고·직인 이미지 올리기 | 아니오 | 예(저장) |
| POST | `api/rows` | 명단 붙여넣기/xlsx → 행 목록 | 아니오 | 아니오 |
| POST | `api/polish` | 수상 문구를 상장 문체로 다듬기 | 예(LLM, 수 초~수십 초) | 아니오 |
| GET | 그 밖의 경로 | 화면 HTML | — | — |

## 상장 설정 `cfg` (preview·issue 공통)
| 이름 | 형식 | 설명 |
|---|---|---|
| `design` | 문자열 | `api/meta` 의 `designs[].id` (`traditional` `official` `modern` `hanji` `ribbon` `goldframe` `goldcorner`). 모르는 값이면 `traditional` |
| `orient` | 문자열 | `landscape` 면 가로, 그 밖은 세로 |
| `kind` | 문자열 | 큰 제목(상장·표창장·감사장·공로상·우수상 또는 자유 입력) |
| `award` | 문자열 | 상 이름(선택, 제목 아래 줄) |
| `number` | 문자열 | 번호. `제` 로 시작하지 않으면 `제 <번호> 호` 로 찍힘. 빈 값이면 안 찍힘 |
| `name` `dept` `position` | 문자열 | 수상자 이름·소속·직위 |
| `text` | 문자열 | 본문 문구 |
| `date` | 문자열 | `YYYY-MM-DD` 면 `2026년 10월 6일` 로 바꿔 찍음, 그 밖은 그대로. 비면 오늘 |
| `org` `giver_title` `giver_name` | 문자열 | 수여 기관·직함·이름 |
| `seal_mode` | 문자열 | `auto-square`(기본) · `auto-round` · `upload` · `none` |
| `seal_text` | 문자열 | 자동 직인 글자(비면 기관명+직함으로 자동) |
| `seal_id` | 문자열 | `seal_mode: "upload"` 일 때 `api/upload` 가 준 id |
| `logo_mode` | 문자열 | `top`(기본) · `watermark` · `none` |
| `logo_id` | 문자열 | `api/upload` 가 준 id(16자리 16진) |
| `corners` | 문자열 | `goldcorner` 디자인에서 `"2"` 면 대각선 두 모서리, 그 밖은 네 모서리 |

## 엔드포인트

### GET api/meta
- 응답:
```json
{"designs": [{"id": "traditional", "name": "전통 금색 테두리"}],
 "kinds": ["상장", "표창장", "감사장", "공로상", "우수상"],
 "phrases": {"상장": [["연구 성과 우수", "위 사람은 … 이에 상장을 수여합니다."]]},
 "next_number": "2026-004", "today": "2026-10-13", "model": "qwen3:8b"}
```
  - `phrases[종류]`: `[라벨, 문구]` 배열. `next_number`: 이 사용자의 올해 이력 중 `YYYY-NNN` 최댓값 + 1.

### GET api/models
- 응답: `["qwen3:8b", ...]`. LLM 서버에 못 닿으면 `[]` — 이때 `api/polish` 는 실패하므로 버튼을 끄세요.

### GET api/history
- 응답(최신순, 최대 500): 발급 한 장(page)마다 한 줄
```json
[{"id": "2026-10-07-312997", "page": 1, "ts": "2026-10-07T06:24:04", "date": "2026-10-07", "number": "제 2026-001 호",
  "name": "홍길동", "dept": "○○연구실", "position": "선임연구원", "kind": "상장", "award": "", "org": "○○연구원", "design": "traditional"}]
```
  - `id` 하나가 한 번의 발급(일괄이면 여러 page). 다시 보기: `api/issued/<id>.pdf#page=<page>`.

### GET api/issued/<id>.pdf
- `id` 형식 `YYYY-MM-DD-xxxxxx`(16진 6자리). `application/pdf`(inline, 파일명 없음). 없으면 `404 {"error":"없음"}`.
- 메모: PNG·ZIP 으로 발급해도 서버에는 PDF 로 보관됩니다.

### GET api/thumb/<design>.jpg
- `image/jpeg` 작은 견본(예시 이름 "홍길동"). 모르는 design 이면 화면 HTML 이 돌아옵니다.

### GET api/upload/<uid>
- `image/png`. 없으면 `404 {"error":"없음"}`.

### POST api/preview
- 요청: `{"cfg": {...}}`
- 응답: `image/jpeg`(90dpi). 저장·이력 없음. 화면은 입력이 바뀔 때마다 300ms 뒤 호출하고 앞 요청은 `AbortController` 로 취소합니다.

### POST api/issue
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `cfg` | 객체 | 예 | 위 설정. 일괄이면 공통 값 |
| `format` | 문자열 | 아니오 | `pdf`(기본) · `png`(한 장일 때만, 여러 장이면 PDF 로 나감) · `zip-pdf` · `zip-png` |
| `rows` | 배열 | 아니오 | 일괄 명단 `[{"name","dept","position","text"}]`(`api/rows` 결과). 최대 300(넘으면 앞 300만) |

- 응답: 파일 바이트 + `Content-Disposition: attachment; filename*=UTF-8''<이름>`
  - 한 장: `<종류>_<이름>.pdf|png`, 여러 장: `<종류>_일괄<N>장.pdf|zip`. ZIP 안: `001_<번호>_<이름>.pdf|png`.
  - `Content-Type`: `application/pdf` · `image/png` · `application/zip`
- 일괄 번호: `cfg.number` 의 마지막 숫자 덩어리를 행마다 1씩 올림(자리수 유지, 예 `2026-009` → `2026-010`). `cfg.number` 가 비어 있으면 서버가 `next_number` 부터 자동으로 매깁니다.
- 행의 `text` 가 비어 있으면 `cfg.text` 를 씀.
- 메모: 발급할 때마다 PDF 보관 + `api/history` 에 기록. 스트리밍 없음.

### POST api/upload
- 요청: `{"data": "data:image/png;base64,...."}` (데이터 URL 또는 base64만)
- 응답: `{"id": "1a2b3c4d5e6f7a8b"}` — 내용 해시(같은 그림이면 같은 id). 15MB 넘으면 거절, 긴 변 2400px 로 줄이고 PNG 로 저장.
- 오류: `413 {"error":"이미지가 너무 큽니다 (15MB 이하)"}`, POST 본문 25MB 초과(모든 POST 공통) `413 {"error":"요청이 너무 큽니다 (최대 25MB — 이미지 15MB 이하)"}`, 이미지가 아니면 `500 {"error":"UnidentifiedImageError: ..."}`.

### POST api/rows
- 요청: `{"text": "이름\t소속\t직위\n홍길동\t○○실\t연구원"}` 또는 `{"xlsx": "data:...;base64,...."}`(첫 시트만)
- 응답: `{"rows": [{"name": "홍길동", "dept": "○○실", "position": "연구원", "text": ""}]}`
- 규칙: 탭이 있으면 탭, 없으면 쉼표로 나눔. 첫 줄 첫 칸이 머리글(`이름`·`성명`·`name`…)이면 그 열 순서를 따르고, 아니면 `이름, 소속, 직위, 문구` 순서. 머리글: 이름/성명/name, 소속/부서/dept, 직위/직급/position, 문구/공적/text. 이름 없는 줄은 버림.

### POST api/polish
- 요청: `{"text": "올해 논문 3편 게재", "kind": "표창장", "model": "선택"}`
- 응답: `{"text": "위 사람은 … 이에 표창합니다."}` (한 단락)
- 오류: 빈 문구 `400 {"error":"다듬을 문구가 비어 있습니다"}`, LLM 연결 실패 `500 {"error":"RuntimeError: LLM 서버 연결 실패: ..."}`(서버 주소는 넣지 않음).

## 흐름 예
```js
const base = '/t/award-local/', post = (p, b) => fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
const meta = await fetch(base + 'api/meta').then(r => r.json());
const cfg = { design: 'traditional', kind: '상장', number: meta.next_number, name: '홍길동', dept: '○○연구실', position: '선임연구원',
              text: meta.phrases['상장'][0][1], date: meta.today, org: '○○연구원', giver_title: '원장', giver_name: '○○○' };
img.src = URL.createObjectURL(await post('api/preview', { cfg }).then(r => r.blob()));   // 미리보기

// 일괄 발급: 명단 → ZIP
const { rows } = await post('api/rows', { text: pasted }).then(r => r.json());
const r = await post('api/issue', { cfg, rows, format: 'zip-pdf' });
if (!r.ok) throw new Error((await r.json()).error);
const name = decodeURIComponent((r.headers.get('Content-Disposition').match(/filename\*=UTF-8''([^;]+)/) || [])[1] || 'award.zip');
const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(await r.blob()), download: name }); a.click();
```

## 화면만의 것 / 주의
- 입력값 보관(localStorage `award-form`), 다크 모드 토글은 화면 기능입니다.
- 사용자 지정 종류(예: "봉사상")의 예시 문구는 화면이 `상장` 문구의 "상장을 수여합니다" 를 바꿔 만듭니다(API 는 5가지 종류 문구만 줌).
- 발급 뒤 다음 번호로 올리기(`bump`)와 일괄 번호 범위 표시도 화면이 계산합니다. 화면은 번호가 비어 있으면 "번호 없음" 이라고 보여주지만, 서버는 일괄 발급 때 빈 번호를 자동 번호로 채웁니다(불일치).
- 인쇄는 받은 PDF 를 iframe 에 띄워 브라우저 인쇄를 부릅니다(100% 배율 권장).
- 이력·발급본 삭제 API 는 없습니다. `/static/` 경로는 이 도구 화면이 쓰지 않습니다(폰트는 서버 렌더링용).
