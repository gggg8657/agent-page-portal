# CAD Agent API (`agent-cad-local`)
자연어(+선택: DXF 또는 참조 이미지) → LLM 이 Project IR(JSON) 작성 → 결정론 CAD 엔진이 STEP/DXF/SVG/GLB 와 검증 보고서를 만든다. 요청마다 프로젝트 안에 리비전(`R00`, `R01` …)이 쌓인다.
포털 경로 접두어: `/t/agent-cad-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/agent-cad-local/api/run`)

서버는 Python 표준 `http.server`(`app.py`) + `cad/` 모듈. GET 은 쿼리스트링을 떼고 경로를 맞추며, 모르는 GET 경로는 `404 {"error": "없음"}`. POST 는 쿼리스트링 없이 정확히 맞춰야 한다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | LLM 모델 목록·기본 모델 | 아니오 | 아니오 |
| POST | `api/upload` | DXF/이미지 업로드 → 업로드 id | 아니오 | 예 |
| POST | `api/run` | 요청 실행(새 프로젝트 또는 이어서 새 리비전), SSE | **예 (LLM·VLM + CAD 빌드, 수십 초~수 분)** | 예 |
| POST | `api/rebuild` | 고친 IR 로 LLM 없이 재빌드(새 리비전), SSE | 예 (CAD 빌드, LLM 없음; 조립은 VLM 시각검증 가능성 있음 — 코드 확인 필요) | 예 |
| GET | `api/projects` | 프로젝트 목록 | 아니오 | 예 |
| GET | `api/projects/<pid>` | 프로젝트 전체(project.json) | 아니오 | 예 |
| GET | `api/projects/<pid>/<R00\|input>/<파일>` | 산출물·원본 파일 내려받기 | 아니오 | 예 |
| POST | `api/projects/<pid>/hide` | 목록에서 숨김/해제 | 아니오 | 예 |
| GET | `api/kb` | 원자력 설비 지식 DB 원형 목록 | 아니오 | **공유** |
| GET | `api/kb/<id>` | 원형 하나 전체 | 아니오 | 공유 |
| POST | `api/kb/<id>` | 원형 수정 또는 새 원형 추가 | 아니오 | 공유 |
| POST | `api/kb/<id>/reset` | 사용자 수정 지우고 원본으로 | 아니오 | 공유 |
| GET | `api/gt` | 벤치마크용 GT 목록 | 아니오 | 공유(읽기 전용) |
| GET | `api/gt/<id>.png\|.jpg\|.json` | GT 이미지 / GT JSON | 아니오 | 공유 |
| GET | `api/library` | ICT 심볼 라이브러리·규칙 | 아니오 | 공유(읽기 전용) |
| GET | `/` 또는 `index.html` | 화면 | 아니오 | 아니오 |
| GET | `static/three.min.js`·`static/GLTFLoader.js`·`static/OrbitControls.js` | 3D 뷰어 라이브러리 | 아니오 | 아니오 |

## 공통
- **id 형식**: 프로젝트 `YYYYMMDD-xxxxxx`(16진 6자리), 리비전 `R00`·`R01`…, 업로드 `xxxxxxxxxxxx`(16진 12자리).
- **작업 종류(`task`)**: `part3d`(3D 부품) · `part2d`(2D 판재) · `ict`(ICT 배치도·개황도) · `dxf_edit`(기존 DXF 수정) · `dxf_analyze`(DXF 분석) · `assembly`(원자로·원자력 설비, 지식 DB) · `auto`(키워드/LLM/VLM 이 고름).
- **리비전 상태(`status`)**: `pass`(위반 없음) · `warnings` · `errors`(규칙 오류 있음) · `failed`(빌드 실패).
- 오류 응답: 일반 JSON 엔드포인트는 `{"error": "..."}`. GET 은 파일 없음 `404`, 그 밖 `500`. POST 는 예외를 모두 `400` 으로 돌려준다.

## 엔드포인트

### GET api/models
- 응답: `{"models": ["gemma4:31b", "qwen3:8b"], "default": "gemma4:31b", "api": "ollama"}`
- LLM 서버 연결 실패여도 200: `{"models": [<기본 모델>], "default": "...", "error": "..."}`

### POST api/upload
- 요청: **본문 = 파일 바이트 그대로**(multipart 아님). 헤더 `X-Filename: <encodeURIComponent(파일명)>` (없으면 `upload.dxf`).
- 허용 확장자: `.dxf`, `.png`, `.jpg`, `.jpeg`, `.webp`. 최대 200 MB(서버가 본문 전체를 메모리로 읽은 뒤 검사).
- 응답: `{"upload": "a1b2c3d4e5f6", "name": "역사_평면.dxf"}`
- 오류: `400 {"error": "ValueError: DXF 또는 이미지(PNG/JPG)만 (DWG는 ODA File Converter로 DXF 변환 후)"}`, `"... 200 MB 초과"`
- 메모: 업로드만으로는 프로젝트에 붙지 않는다. 다음 `api/run` 의 `upload` 로 넘겨야 프로젝트 `input/` 에 복사된다. DWG 는 받지 않음.

### POST api/run
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `prompt` | 문자열 | 예 | 자연어 요청(비면 `400 {"error": "빈 요청"}`) |
  | `task` | 문자열 | 아니오 | 위 작업 종류. 기본 `auto`. **기존 프로젝트를 이어갈 때는 무시**되고 프로젝트의 task 를 씀 |
  | `project` | 문자열 또는 null | 아니오 | 이어서 수정할 프로젝트 id. 없으면 새 프로젝트 |
  | `upload` | 문자열 또는 null | 아니오 | `api/upload` 가 준 id. DXF(`dxf_edit`·`dxf_analyze`·`ict` 기존 도면) 또는 참조 이미지 |
  | `model` | 문자열 | 아니오 | LLM 모델(비우면 서버 기본) |
  | `gt` | 문자열 | 아니오 | GT id — 결과를 그 GT 로 자동 채점(벤치마크용). 첨부 이미지가 GT 원본과 같으면 자동으로 잡힘 |
  | `owner` | 문자열 | 아니오 | 브라우저별 임의 id. 목록 필터(`api/projects?owner=`)에만 쓰임 |
- 응답: `200 text/event-stream; charset=utf-8`, `Connection: close`. 각 이벤트는 `data: <JSON>\n\n` (이벤트 이름 없음).

  | 이벤트 모양 | 뜻 |
  |---|---|
  | `{"stage": "route", "msg": "작업 분류: 3D 부품 (키워드)", "task": "part3d"}` | 단계 시작. `stage` 값: `route`·`llm`·`vlm`·`build`·`repair` |
  | `{"stage": "...", "msg": "..."}` | 진행 단계 설명 |
  | `{"log": "LLM 12.3s, 2048자"}` | 부가 로그 |
  | `{"token": "..."}` | LLM 출력 조각(대부분 IR JSON 원문 — 화면은 글자 수만 셈) |
  | `{"project": "20261013-1a2b3c", "task": "part3d"}` | 새 프로젝트가 만들어짐(새 프로젝트일 때만) |
  | `{"done": {"project": "...", "rev": "R00", "status": "pass"}}` | 끝. 이어서 `api/projects/<pid>` 로 결과 조회 |
  | `{"error": "ValueError: 이 작업은 DXF 첨부가 필요합니다"}` | 실패(HTTP 상태는 이미 200) |
- 메모:
  - LLM 이 쓴 IR 이 규칙 위반이면 자동 수리 반복(기본 최대 3회, 서버 환경변수 `MAX_REPAIR`). 결과 상세는 `done` 뒤에 프로젝트 JSON 의 마지막 리비전에서 읽는다.
  - 걸리는 시간: 부품 수십 초, ICT·조립체(OpenCascade 서브프로세스 + VLM 시각검증) 수 분까지.
  - `EventSource` 는 POST 를 못 하므로 `fetch` + `getReader()` 로 읽는다(아래 흐름 예).
  - 요청 도중 연결을 끊어도 서버 작업은 계속되고 리비전은 저장된다(전송 오류는 무시).

### POST api/rebuild
- 요청: `{"project": "<pid>", "ir": { ...Project IR 객체... }}` (둘 다 필수)
- 응답: `api/run` 과 같은 SSE(`stage`·`log`·`done`·`error`). 새 리비전이 `source: "manual"`, `prompt: "(IR 직접 수정 → 재빌드)"` 로 쌓인다.
- 오류: `dxf_edit`·`dxf_analyze` 프로젝트면 `error` 이벤트 `"IR 재빌드는 부품/ICT/조립 작업만"`. 필드가 빠져도 스트림을 연 뒤 `error` 이벤트로 알림.
- 메모: IR 원문은 `api/projects/<pid>/<rev>/ir.json` 에서 받아 고쳐 보낸다. IR 스키마는 작업마다 다르며 `prompts/*.md` 와 `cad/*.py` 에 정의 — 코드 확인 필요.

### GET api/projects
- 쿼리: `owner=<브라우저 id>`(주면 그 owner 것만), `all`(값 무관, 있으면 owner 필터 무시). 둘 다 없으면 숨기지 않은 전부. 최대 80개, id 역순(최신 먼저).
- 응답:
  ```json
  [{"id": "20261013-1a2b3c", "task": "assembly", "title": "풀형 소듐냉각고속로 (SFR)", "created": "2026-10-13T05:00:56", "revs": 1, "status": "pass", "rev": "R00"}]
  ```
  `status`·`rev` 는 마지막 리비전 값(리비전 없으면 `null`).

### GET api/projects/<pid>
- 응답: `project.json` 전체
  ```json
  {"id": "...", "task": "assembly", "title": "...", "created": "...", "owner": "...", "input": "참조.png",
   "messages": [{"role": "user", "content": "...", "rev": "R00"}],
   "revisions": [{
     "rev": "R00", "ts": "...", "prompt": "...", "model": "...", "source": "llm", "attempts": 1, "status": "pass",
     "ir": true,
     "files": {"<파일명>": "<설명>", "report.md": "검증 보고서", "ir.json": "Project IR (편집 후 재빌드 가능)"},
     "previews": {"ga": "ga.svg", "flow": "flow.svg", "model3d": "<이름>_cutaway.glb"},
     "checks": [{"id": "...", "severity": "error|warning", "msg": "..."}],
     "metrics": {}, "assumptions": [], "notes": [], "calc": {}, "principles": []
   }]}
  ```
  | 리비전 필드 | 설명 |
  |---|---|
  | `files` | 이 리비전 폴더의 산출물 → 설명. 내려받기 경로는 아래 파일 엔드포인트 |
  | `previews` | 화면 미리보기용. `model3d` 는 GLB(3D 뷰어), 나머지 키는 SVG 파일명 |
  | `ir` | `true` 면 `ir.json` 이 있어 재빌드 가능 |
  | `checks`·`metrics`·`assumptions`·`notes`·`calc`·`principles` | 검증 결과·측정값·엔진 가정·LLM 메모·공학 사이징·적용 설계 원칙. 작업마다 들어가는 키가 다름 |
  | `answer` | `dxf_analyze` 의 답변 글 |
  | `error` | 빌드 실패 사유(`status: failed`) |
  | `visual`, `gt`, `vlm_summary`, `ops_log`, `diff`, `ir_diff` | 있을 때만: VLM 시각 비교, GT 채점, 이미지 판독 요약, DXF 수정 연산·변경, 직전 리비전 대비 IR 변경 |
- 오류: `404 {"error": "없음"}`, id 형식 틀리면 `404`(경로 불일치).

### GET api/projects/<pid>/<rev 또는 input>/<파일명>
- 파일명은 URL 인코딩(한글 파일명 많음). `/` 포함·`.` 으로 시작하는 이름은 404.
- 쿼리 `dl`(값 무관)을 붙이면 `Content-Disposition: attachment; filename*=UTF-8''<이름>` 로 내려받기, 없으면 바로 보기.
- Content-Type: `.svg` image/svg+xml, `.dxf` application/dxf, `.step` application/step, `.stl` model/stl, `.glb` model/gltf-binary, `.csv` text/csv, `.md` text/markdown, `.json` application/json, `.png`/`.jpg`/`.webp` 이미지, 그 밖은 추측 또는 octet-stream.
- 보통 들어 있는 파일: `report.md`(검증 보고서, 마크다운), `ir.json`, 미리보기 SVG, STEP/DXF/GLB/CSV, 조립체는 `ga_render.png` 등. 정확한 목록은 리비전의 `files`.
- `input/<파일>` 은 프로젝트에 붙은 원본(DXF·이미지). 수정 작업에서도 원본은 덮어쓰지 않는다.

### POST api/projects/<pid>/hide
- 요청: `{"hidden": true}` (기본 true, `false` 면 다시 보임)
- 응답: `{"id": "...", "hidden": true}`
- 메모: 삭제가 아니라 목록에서만 뺌. 삭제 API 는 없다.

### GET api/kb
- 응답: `[{"id": "hot_cell", "name": "핫셀 (차폐 셀 시설)", "kind": "facility", "summary": "...", "edited": false, "user_added": false, "n_principles": 4}]`

### GET api/kb/<id>
- `<id>`: 영소문자·숫자·`_`.
- 응답: 원형 전체(원본 + 사용자 수정 병합). 주요 필드 `id, name, kind, summary, aliases, principles, checks, design, source, sizing, components, connections, loops, coolant`, 수정된 경우 `history: [{ts, author, note, fields}]`. 세부 구조는 `kb/archetypes/*.json` 참고.
- 오류: 없는 id 는 `500 {"error": "KeyError: ..."}` (404 아님).

### POST api/kb/<id>
- 요청: `{"patch": {<필드>: <새 값>}, "author": "수정자", "note": "수정 사유"}`
  - 고칠 수 있는 필드: `name, summary, aliases, principles, checks, design, source, sizing, components, connections, loops, kind, coolant`. 다른 키가 있으면 400.
  - `principles` 는 비어 있지 않은 문자열 배열이어야 함.
  - 없는 id 로 보내면 **새 원형 추가**(`patch.name` 필수, id 는 영소문자·숫자·`_` 2~40자). 화면의 "복제해 새 원형" 이 이렇게 한다.
  - `design` 은 지금 화면이 바뀐 값만 `{"<키>": {"v": <숫자>}}` 로 보냄(병합 규칙은 `cad/kb.py` `_merge` — 코드 확인 필요).
- 응답: 병합된 원형 전체(GET 과 같은 모양). 다음 `api/run` 부터 바로 반영.
- 오류: `400 {"error": "ValueError: 수정할 수 없는 필드: [...]"}` 등.

### POST api/kb/<id>/reset
- 요청: 본문 `{}` (`author` 는 받지만 현재 쓰지 않음)
- 응답: 원본 원형 전체. 사용자가 새로 추가한 원형이면 `{"ok": true}`(그 원형은 사라짐).
- 메모: 수정본 파일은 지우지 않고 `.bak` 으로 이름을 바꿔 둔다.

### GET api/gt, api/gt/<id>.png|.jpg|.json
- `api/gt`: `[{"id": "msr_3loop_synth", "name": "...", "image": "msr_3loop_synth.png"}]`
- `api/gt/<id>.png`(또는 `.jpg`): GT 원본 이미지. 확장자와 무관하게 GT JSON 의 `image` 파일을 `image/png` 로 보냄.
- `api/gt/<id>.json`: GT 정답 JSON.
- 메모: 벤치마크용. 화면은 GT 를 고르면 이미지를 받아 `api/upload` 로 다시 올린 뒤 `api/run` 에 `gt` 와 함께 보낸다.

### GET api/library
- 응답: `{"symbols": [...ICT 심볼...], "rules": {...ICT 규칙...}, "part_rules": {...부품 규칙...}}` (`library/ict.json`, `rules/*.json` 그대로). 지금 화면은 부르지 않음.

## 흐름 예

### 1) 새 부품 만들기 → 결과 보기
```js
const BASE = '/t/agent-cad-local/';
async function sse(path, body, onev) {
  const r = await fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
  for (;;) {
    const { done, value } = await rd.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    let i; while ((i = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 2);
      if (line.startsWith('data: ')) onev(JSON.parse(line.slice(6)));
    }
  }
}
let result;
await sse('api/run', { prompt: '100x60x10 블록에 M5 관통홀 4개', task: 'auto', project: null, upload: null, model: '' }, ev => {
  if (ev.stage) console.log(`[${ev.stage}] ${ev.msg}`);
  else if (ev.error) console.error(ev.error);
  else if (ev.done) result = ev.done;
});
const P = await fetch(`${BASE}api/projects/${result.project}`).then(r => r.json());
const R = P.revisions.find(r => r.rev === result.rev);
const url = f => `${BASE}api/projects/${P.id}/${R.rev}/${encodeURIComponent(f)}`;
svgBox.innerHTML = await fetch(url(Object.values(R.previews).find(f => f.endsWith('.svg')))).then(r => r.text());
reportMd = await fetch(url('report.md')).then(r => r.text());
// 내려받기 링크: url(name) + '?dl=1'
```

### 2) DXF 첨부 후 수정 요청 → 이어서 대화
```js
const up = await fetch(BASE + 'api/upload', { method: 'POST',
  headers: { 'X-Filename': encodeURIComponent(file.name) }, body: await file.arrayBuffer() }).then(r => r.json());
await sse('api/run', { prompt: '대합실에 CCTV 4대 추가', task: 'dxf_edit', upload: up.upload }, onev);  // done.project 기억
await sse('api/run', { prompt: '하나는 출입구 쪽으로 옮겨', project: pid }, onev);                     // 같은 프로젝트, R01
```

### 3) IR 직접 고쳐 재빌드
`GET api/projects/<pid>/R00/ir.json` → 편집 → `api/rebuild` 에 `{project, ir}` (SSE) → `done.rev` 로 새 리비전 조회.

## 화면만의 것 / 주의
- **3D 보기**: 화면이 `static/three.min.js` + `GLTFLoader.js` + `OrbitControls.js` 로 `previews.model3d` GLB 를 띄운다. SVG 미리보기 확대·이동, 보고서(`report.md`) 마크다운 표시, 요약 한 줄(치수·질량·케이블 길이 등 `metrics` 조합)도 화면 몫.
- **owner(브라우저 id)**: 화면이 `localStorage.cadOwner` 에 임의 UUID 를 만들어 `api/run` 의 `owner` 와 `api/projects?owner=` 에 쓴다. 이것은 목록 필터일 뿐 권한이 아니다. 실제 사용자 분리는 포털의 `X-Agent-User` 헤더 → 작업 폴더 `WORKSPACE/u/<id>/projects`, `uploads` 로 이루어진다.
- **지식 DB 는 모든 사용자 공유**: 수정본은 사용자 폴더가 아니라 `WORKSPACE/kb_overrides/` 한 곳에 저장되고, 로그인한 누구나 `api/kb/<id>` POST 로 고칠 수 있다(owner 전용 검사 없음). 수정 이력(`history`)에 `author` 를 남긴다.
- 숨김(`hide`)도 owner 검사가 없다(같은 사용자 폴더 안에서는 누구 프로젝트든 숨길 수 있음).
- GT·라이브러리·규칙 파일은 도구 폴더(읽기 전용) 공유.
- 산출물은 모두 **DRAFT — 엔지니어 검토 전 제작·시공 불가** 표기(`report.md`).
- 화면 HTML 의 `%MODEL%` 자리에 기본 모델이 박혀 나간다(별도로는 `api/models` 의 `default`).
- 정적 파일은 위 세 개만 허용(`Cache-Control: max-age=86400`). `static/fonts/` 는 서버 내부 래스터용이라 내보내지 않는다.
