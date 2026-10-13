# kordoc API (`kordoc-local`)
HWP·HWPX·PDF·DOCX·XLSX·이미지 문서를 kordoc 으로 읽어 로컬 LLM 으로 요약·질문·공문서 초안(HWPX)·윤문을 하는 도구.
포털 경로 접두어: `/t/kordoc-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/kordoc-local/api/run`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | LLM 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/runs` | 지난 실행 목록(최근 50개) | 아니오 | 예 |
| GET | `api/runs/<run_id>` | 지난 실행 전체 결과 JSON | 아니오 | 예 |
| GET | `api/runs/<run_id>/<파일>` | 결과 파일 내려받기(HWPX·HWP·MD) | 아니오 | 예 |
| POST | `api/run` | 요약·질문·초안·윤문 실행 (SSE 스트림) | 예 — LLM, 수십 초~수 분 | 예 |
| POST | `api/polish_text` | 글(마크다운)만 윤문, JSON 한 번에 | 예 — LLM | 아니오(저장 안 함) |
| GET | `api/forms` | 등록된 HWPX 양식 목록 | 아니오 | **공유** |
| POST | `api/forms` | 양식 등록(분석) | 조금(kordoc, LLM 없음) | **공유** |
| POST | `api/forms/delete` | 양식 삭제 | 아니오 | **공유** |
| POST | `api/forms/kind` | 양식 종류(fill/body) 바꾸기 | 아니오 | **공유** |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/models
- 응답: 문자열 배열 `["gemma4:31b", "qwen3:8b", ...]` (임베딩 모델도 섞여 있음)
- 오류: LLM 서버 연결 실패 → 500 `{"error": "URLError: ..."}`
- 메모: 서버 기본 모델은 API 로 주지 않음(화면 HTML 의 `%MODEL%` 자리에만 들어감). `model` 을 비워 보내면 서버 기본값이 쓰임.

### GET api/runs
- 응답 (최근 것부터 최대 50개)
```json
[{"run_id": "2026-10-12-2f1d", "task": "polish", "model": "gemma4:31b",
  "file": "보고서.hwpx", "hwpx": "03_result.hwpx", "ts": "2026-10-12T09:00:45", "head": "요청 또는 결과 앞 40자"}]
```
- `hwpx` 는 결과 파일 이름(`03_result.hwpx` 또는 `03_result.hwp`) 또는 null.

### GET api/runs/<run_id>
- `run_id` 형식 `YYYY-MM-DD-xxxx`(16진 4자리). 응답은 `api/run` 의 `done` 객체와 같음.
- 오류: 없는 id → 404 `{"error": "없음"}`

### GET api/runs/<run_id>/<파일>
- `<파일>` 은 다음 중 하나만: `03_result.hwpx` · `03_result.hwp` · `02_answer.md` · `02_polished.md` · `01_source.md`
- 응답: `Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename*=UTF-8''<이름>`
  - `03_result.*` 의 이름: `<원본이름>_윤문.hwpx` · `<원본이름>_<프리셋>.hwpx` · `<프리셋>.hwpx` 식
  - 나머지: `<run_id>_<파일>`
- 오류: 없는 파일 → 404 `{"error": "없음"}`
- 메모: 결과 JSON 의 `hwpx` 필드 값을 그대로 붙이면 됨(`api/runs/${run_id}/${hwpx}`). `<a href>` 로 바로 내려받기 가능.

### POST api/run
- 요청 (JSON, 파일은 base64 로 본문에 넣음 — multipart 아님)

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `task` | string | 아니오 | `summary`(기본)·`qa`·`draft`·`polish`. 그 밖의 값은 SSE `error` 로 끝남 |
| `question` | string | 조건 | 요청 문장. 파일이 없으면 필수(둘 다 없으면 400). polish 에선 안 씀 |
| `file_name` | string | 아니오 | 올린 문서 이름(확장자로 형식 판단) |
| `file_b64` | string | 아니오 | 문서 내용 base64(`data:` 접두어 없이). 확장자 `.hwp .hwpx .hml .pdf .docx .xlsx .xls .png .jpg .jpeg .webp .md .txt` |
| `from_run` | string | 아니오 | 이전 실행 id. 그 실행의 결과 HWPX/HWP(없으면 올렸던 원본)를 문서로 씀 — 화면의 "윤문하기". `file_b64` 보다 우선 |
| `model` | string | 아니오 | 비우면 서버 기본 |
| `preset` | string | 아니오 | draft 서식: `보고서`(기본)·`기안문`·`간이기안문`·`계획서`·`통지`·`회의록`·`개조식`·`업무보고`·`서울방침`·`보도자료`, 또는 `form:<양식id>`(등록 양식). 모르는 값은 `보고서` |
| `strength` | string | 아니오 | polish 강도 `light`·`standard`(기본)·`strong` |
| `form_name`, `form_b64` | string | 아니오 | draft 에서 바로 쓸 HWPX 양식(이름은 `.hwpx` 로 끝나야 함). 넣으면 `preset` 이 이 양식으로 바뀜 |
| `form_save` | bool | 아니오 | true 면 위 양식을 양식 목록에 남김. 아니면 실행 끝나고 지움 |

- 응답: `Content-Type: text/event-stream`, 이벤트 `data: <JSON>\n\n`
  - 단계: `{"stage": "form"|"parse"|"split"|"llm"|"lint"|"generate"|"patch"|"fill"|"build", "msg": "...", "reset": true?}` — `reset` 이 오면 받은 토큰을 비우기
  - 토큰: `{"token": "..."}` — summary·qa·draft 의 LLM 출력. **polish 는 토큰 없이** `llm` 단계(`윤문 3/7 묶음`)로만 진행 표시
  - 끝: `{"done": {결과}}`
  - 오류: `{"error": "ValueError: ..."}` (HTTP 200 이 이미 나간 뒤)
- 결과 객체 공통 필드: `run_id`, `task`, `model`, `question`, `file`(원본 이름 또는 null), `truncated`(문서가 `MAX_CHARS`=12000자에서 잘렸는지), `output`(답/초안 마크다운, polish 는 윤문된 마크다운), `source`(파싱한 원문 마크다운), `hwpx`(내려받을 결과 파일 이름 또는 null), `valid`(kordoc validate 통과 여부), `lint`(표기법 검수 JSON, 실패 시 `{"raw": ...}`), `preset`, `log`, `ts`
  - draft + 기안문·간이기안문·양식: `fields`(채운 칸 값), 양식이면 `form`(양식 id)
  - polish 추가: `changes`(`[{n, before, after, ratio, applied}]`), `rejected`(`[{n, before, after, reason}]` — 숫자·고유 표기 변경·길이 과다 등으로 원문 유지), `units`(조각 수), `strength`, `mode`(`patch`=HWP/HWPX 원본 서식 유지, `generate`=새 HWPX), `applied`(patch 일 때 실제 반영 개수)
- 오류(스트림 시작 전, HTTP 400 JSON): `{"error": "문서나 요청 중 하나는 있어야 합니다"}`, 양식이 HWPX 가 아님, `from_run` 이 잘못되었거나 결과 없음
- 메모: 요약·질문은 LLM 1콜(수십 초~수 분). 초안은 + lint·generate·validate. 윤문은 조각 2,500자 묶음마다 1콜(2개 병렬)이라 문서 길이에 비례. 업로드 크기 제한 코드에 없음(본문 전체를 메모리로 읽음 — 큰 파일은 피할 것). 진행률 숫자는 polish 묶음 수만.

### POST api/polish_text
- 다른 도구(writer·notebook 등)가 부르는 글 윤문. 파일·기록 없음.
- 요청: `{"text": "마크다운 글", "model": "선택", "strength": "light|standard|strong"}`
- 응답 (한 번에)
```json
{"output": "윤문된 글", "changes": [{"n": 0, "before": "...", "after": "...", "ratio": 0.12}],
 "rejected": [{"n": 3, "before": "...", "after": "...", "reason": "숫자·고유 표기 변경 (사라짐: 12)"}],
 "units": 8, "strength": "standard", "model": "gemma4:31b", "log": ""}
```
- 오류: 빈 글 → 400 `{"error": "빈 글"}`, 그 밖 500
- 메모: 스트리밍 없음. 글 길이에 따라 수십 초 이상 걸릴 수 있음. 제목(`#`)·표 구분선·코드·HTML·10자 미만 조각은 건드리지 않음.

### GET api/forms
- 응답: 양식 메타 배열
```json
[{"id": "a443508d", "name": "국문 이력서 양식", "kind": "fill", "roles": {"table": 3},
  "front": [{"i": 0, "text": "..."}], "fields": [{"label": "성명", "hint": ""}], "boxes": ["사유"], "blocks": 12}]
```
- `kind`: `fill`(칸 채우기형) / `body`(본문 작성형, □○- 같은 단계 기호가 3개 이상인 양식)

### POST api/forms
- 요청: `{"name": "표시 이름(선택, 60자)", "file_name": "양식.hwpx", "file_b64": "<base64>"}`
- 응답: 새 양식 메타(위 모양)
- 오류: 400 `{"error": "양식은 HWPX 파일만 받습니다(.hwp). 한글에서 ..."}` / `"HWPX 문서가 아닙니다. ..."`

### POST api/forms/delete
- 요청 `{"id": "a443508d"}` → 응답 `{"ok": true}`. 잘못된 id → 400

### POST api/forms/kind
- 요청 `{"id": "a443508d", "kind": "fill"|"body"}` → 바뀐 메타. 잘못된 값 → 400 `{"error": "kind"}`

## 흐름 예
```js
const b64 = f => new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.readAsDataURL(f); });
const base = '/t/kordoc-local/';

// 1) 문서 올려 공문서 초안(HWPX) 만들기
const body = {task: 'draft', question: '이 문서로 개선방안 보고서', preset: '보고서',
              file_name: f.name, file_b64: await b64(f)};
const r = await fetch(base + 'api/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
if (!r.ok) throw new Error((await r.json()).error);
// SSE 읽기: humanize-kr-local 문서의 예와 같음 (data: 줄 → token/stage/done/error)
// done 이 오면: <a href="/t/kordoc-local/api/runs/${done.run_id}/${done.hwpx}">내려받기</a>

// 2) 방금 결과를 서식 그대로 윤문
await fetch(base + 'api/run', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({task: 'polish', from_run: done.run_id, strength: 'standard', preset: done.preset})});
```

## 화면만의 것 / 주의
- **양식(`api/forms`)은 모든 사용자가 함께 씀**(사용자별 폴더 아님). 지우면 모두에게서 사라짐. 실행 기록·결과 파일은 사용자별.
- 경로 비교가 정확히 일치해야 함 → `api/runs?x=1` 처럼 쿼리를 붙이면 화면 HTML 이 돌아옴.
- 화면이 직접 하는 것: 파일 → base64 변환(FileReader), 결과 탭(표기법·바뀐 곳·원문) 그리기. 서버는 계산된 값만 줌.
- 기안문·간이기안문의 기관명·결재라인 같은 고정 값은 서버 설정 파일(`gian_defaults.json`)에서 채움 — API 로 바꿀 수 없음.
- 정적 파일 경로 없음.
