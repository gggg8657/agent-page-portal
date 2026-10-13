# sketch API (`sketch-local`)
손그림 사진을 비전 모델이 읽어 구조(JSON)로 만들고, 확인·질문을 거쳐 논문 figure 풍 Mermaid 로 다시 그린다(TikZ 변환 포함). 그림(SVG·PNG)은 **브라우저의 mermaid.js** 가 그린다.
포털 경로 접두어: `/t/sketch-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/sketch-local/api/read`)

서버는 Python 표준 `http.server` 단일 파일(`app.py`). 경로는 **정확히 일치**해야 하며 GET 에 쿼리스트링을 붙이면 화면 HTML 이 돌아온다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | LLM 모델 목록 + 기본 텍스트/비전 모델 | 아니오 (LLM 서버에 목록·능력 질의) | 아니오 |
| GET | `api/presets` | 스타일·유형 프리셋 설명 | 아니오 | 아니오 |
| POST | `api/read` | 이미지 → 구조(reading) | **예 (비전 LLM, 수십 초)** | 아니오 (저장 안 함) |
| POST | `api/plan` | 답변 반영 + 확인 질문 + 스타일/유형 추천 | 아니오 (규칙 기반, 즉시) | 아니오 |
| POST | `api/draw` | reading → Mermaid (SSE 스트리밍), 다듬기 겸용 | **예 (LLM)** | 아니오 (저장 안 함) |
| POST | `api/repair` | 파싱 오류 소스 LLM 수정 1회 | **예 (LLM)** | 아니오 |
| POST | `api/tikz` | Mermaid → LaTeX TikZ 코드 | **예 (LLM)** | 아니오 |
| POST | `api/save` | 작업 묶음 저장(새로 또는 덮어쓰기) | 아니오 | 예 |
| GET | `api/runs` | 내 저장 목록(최근 50) | 아니오 | 예 |
| GET | `api/runs/<id>` | 저장 묶음 하나(JSON) | 아니오 | 예 |
| GET | `api/runs/<id>/sketch.png` | 저장된 원본 스케치 이미지 | 아니오 | 예 |
| GET | `static/mermaid.min.js` | 동봉 Mermaid | 아니오 | 아니오 |
| GET | 그 밖의 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 공통 자료형: reading
`api/read` 가 만들고, `api/plan`·`api/draw`·`api/tikz`·`api/save` 가 받는 구조.
```json
{
  "elements": [{"id": "n1", "label": "웹 서버", "kind": "box", "uncertain": false}],
  "edges": [{"from": "n1", "to": "n2", "label": "SQL", "direction": "->", "uncertain": false}],
  "groups": [{"label": "백엔드", "members": ["n1", "n2"]}],
  "handwriting_notes": ["캐시는 나중에"],
  "guessed_type": "architecture",
  "confidence_notes": ["연결 참조 불명: A → ?"]
}
```
- `id`: 영숫자·`_` 만, 숫자로 시작 안 함(아니면 서버가 `n<순번>` 으로 바꿈).
- `kind`: 비전 모델이 준 소문자 문자열(예: `box`, `actor` …). 값 목록은 코드에서 고정하지 않음 — 프롬프트(`goal-prompt.md` "읽기" 절) 참고.
- `direction`: `->`·`<-`·`<->`·`--`·`?`(모름). `?` 면 `uncertain: true`.
- 사용자가 고친 reading 을 그대로 다시 보내면 된다(서버는 `api/plan`·`api/draw` 에서 다시 정규화하지 않음 — 필드가 빠지면 500).

## 엔드포인트

### GET api/models
- 응답:
  ```json
  {"models": ["qwen2.5vl:7b", "qwen3:8b"], "text": "qwen3:8b", "vision": "qwen2.5vl:7b", "vision_models": ["qwen2.5vl:7b"]}
  ```
  | 필드 | 설명 |
  |---|---|
  | `models` | LLM 서버의 전체 모델 이름 |
  | `text` | 서버 기본 텍스트 모델(그리기·수정·TikZ) |
  | `vision` | 서버 기본 비전 모델(읽기) |
  | `vision_models` | Ollama `capabilities` 에 `vision` 이 있는 모델. OpenAI 호환 서버이거나 조회 실패면 `null` |
- 오류: LLM 서버 연결 실패 시 `500 {"error": "..."}`
- 메모: 모델이 많으면 첫 호출 때 모델마다 `/api/show` 를 불러 몇 초 걸릴 수 있음(이후 캐시).

### GET api/presets
- 응답: `{"styles": {<이름>: <설명>}, "types": {<이름>: <설명>}}`
  - 스타일 이름: `논문-파스텔`·`IEEE-모노`·`Nature-Okabe-Ito`·`Distill-블루오렌지`·`손그림-스케치`·`플랫-컬러풀`·`다크-발표용`·`UML-클래식`
  - 유형 이름: `자동`·`아키텍처`·`시퀀스`·`파이프라인`·`상태도`·`ER`·`마인드맵`·`블록`
  - 객체 키 순서가 화면 드롭다운 순서.

### POST api/read
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `image_b64` | 문자열 | 예 | 이미지 base64 (데이터 URL 접두어 `data:...;base64,` **없이**) |
  | `vision_model` | 문자열 | 아니오 | 비우면 서버 기본 비전 모델 |
- 응답: `{"reading": <reading>, "raw": "<비전 모델 원문>"}`
- 오류: `500 {"error": "ValueError: JSON 없음: ..."}`(모델이 JSON 을 안 냄), `KeyError: 'image_b64'` 등.
- 메모: 업로드는 multipart 가 아니라 JSON 안의 base64. 서버 쪽 크기 제한 코드는 없음 — 지금 화면은 긴 변 1600px 로 줄인 PNG 를 보낸다(새 화면도 줄여 보내기를 권장). 수십 초 걸림.

### POST api/plan
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `reading` | 객체 | 예 | 현재 reading(사용자가 고친 것) |
  | `mode` | 문자열 | 아니오 | `원본`(기본)·`단순화`·`인사이트` — 추천 스타일에만 영향 |
  | `answers` | 객체 또는 null | 아니오 | 질문 답 `{"<key>": "<값>"}`. 키 형식 `edge:<번호>`(값 `->`·`<-`·`<->`·`--`) 또는 `elem:<id>`(값 = 새 라벨) |
- 응답:
  ```json
  {"reading": {...},
   "questions": [{"key": "edge:0", "q": "'A' 와 'B' 사이 화살표 방향은?", "options": ["->", "<-", "<->", "--"], "default": "->"},
                 {"key": "elem:n3", "q": "'캐시' 로 읽었는데 맞나요? (종류: box)", "default": "캐시"}],
   "recommend": {"type": "아키텍처", "style": "IEEE-모노", "why": "비전 모델 추정 'architecture' → 아키텍처. 모드 '단순화' 기본 스타일 IEEE-모노."}}
  ```
  - `questions`: 불확실 항목만 최대 3개. `options` 가 있으면 선택형, 없으면 글 입력. `answers` 를 보낸 호출에서는 항상 `[]`.
  - `reading`: 답을 반영한 결과(답한 항목은 `uncertain: false`).
- 메모: LLM 을 쓰지 않는 규칙 기반이라 즉시 응답. 화면은 질문이 있으면 답을 받아 한 번 더 `api/plan` 을 부르고, 그때 나온 `recommend` 를 쓴다.

### POST api/draw
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `reading` | 객체 | 예 | reading |
  | `mode` | 문자열 | 아니오 | `원본`(기본)·`단순화`·`인사이트` |
  | `style` | 문자열 | 아니오 | 스타일 이름(모르는 값이면 `논문-파스텔`) |
  | `type` | 문자열 | 아니오 | 유형 이름(모르는 값이면 `자동`) |
  | `max_nodes` | 정수 | 아니오 | `단순화` 모드 노드 상한(기본 8) |
  | `messages` | `[{role, content}]` 또는 null | 아니오 | 다듬기용 이어지는 대화. 서버가 reading 을 담은 첫 user 메시지 뒤에 붙인다 |
  | `model` | 문자열 | 아니오 | 텍스트 모델 |
- 응답: `200 text/event-stream`, `data: <JSON>\n\n` 형식(이벤트 이름 없음)
  ```
  data: {"token": "flowchart TB\n"}
  ...
  data: {"done": {"source": "---\nconfig:\n  theme: \"base\"\n...\n---\nflowchart TB\n ...", "insight": "", "sanity": null}}
  ```
  | 이벤트 | 설명 |
  |---|---|
  | `{"token": "..."}` | LLM 원문 조각 |
  | `{"done": {source, insight, sanity}}` | `source` = 스타일 frontmatter(`---\nconfig: ...\n---`) + 본문 + (flowchart/state 일 때) 프리셋 `classDef` 줄들. `insight` = `인사이트` 모드에서 LLM 이 `=====` 뒤에 쓴 해설(그 외 빈 문자열). `sanity` = 서버 1차 검사(`null` 이면 통과) |
  | `{"error": "..."}` | 실패 |
- 오류: 스트림 시작 후 실패는 `error` 이벤트(HTTP 200). `api/draw` 는 사전 검증이 없어 `reading` 이 빠져도 스트림을 연 뒤 `error` 이벤트로 알린다.
- 메모: 저장하지 않는다(필요하면 `api/save`). 다듬기는 지금 화면이 `messages` 에 `{assistant: 현재 소스(frontmatter 뺀 것)}, {user: "[현재 소스]\n...\n\n[요청]\n<요청>"}` 을 쌓아 보낸다.

### POST api/repair
- 요청: `{"source": "...", "error": "<mermaid 파싱 오류>", "model": "..."}`
- 응답: `{"source": "...", "sanity": null}` — 원래 소스가 프리셋 frontmatter 로 시작했으면 같은 스타일로 다시 꾸며 준다.
- 오류: `500 {"error": "..."}`

### POST api/tikz
- 요청: `{"source": "<Mermaid>", "reading": <reading 또는 null>, "model": "..."}`
- 응답: `{"tikz": "\\begin{tikzpicture} ..."}` (LaTeX 코드 문자열. 코드펜스는 걷어냄)
- 오류: `500 {"error": "..."}`

### POST api/save
- 요청: JSON 묶음. 서버가 해석하는 필드는 아래뿐이고 **나머지 필드도 그대로 `bundle.json` 에 저장**된다.

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `id` | 문자열 또는 null | 아니오 | `YYYY-MM-DD-xxxx` 형식이면 그 묶음을 덮어씀. 없거나 형식이 틀리면 새 id 발급 |
  | `image_b64` | 문자열 | 아니오 | 있으면 base64 디코드해 `sketch.png` 로 저장(묶음 JSON 에서는 빠짐) |
  | `source` | 문자열 | 아니오 | 있으면 `diagram.mmd` 로도 저장 |

  지금 화면이 보내는 필드: `id, image_b64, reading, mode, style, type, source, insight, messages, tikz`.
- 응답: `{"id": "2026-10-13-ab12"}`
- 오류: `500 {"error": "..."}`
- 메모: 서버가 `ts`(저장 시각)를 덧붙인다. 삭제 API 는 없다.

### GET api/runs
- 응답(최근 수정 순 최대 50):
  ```json
  [{"id": "2026-10-13-ab12", "ts": "2026-10-13T09:12:30", "mode": "원본", "style": "IEEE-모노", "type": "아키텍처", "head": "사용자, 웹 서버, DB"}]
  ```
  `head` = reading 요소 라벨 앞 3개. reading 없이 저장된 묶음은 `mode` 등이 `null`, `head` 가 `""`.

### GET api/runs/<id>
- 응답: 저장된 `bundle.json` 그대로(위 save 필드 + `id`, `ts`; `image_b64` 는 없음).
- 오류: `404 {"error": "없음"}`. id 형식이 틀리면 화면 HTML 이 돌아옴.

### GET api/runs/<id>/sketch.png
- 응답: 이미지 바이트, `Content-Type: image/png`. 저장 때 받은 base64 를 그대로 디코드한 것이라 실제 형식은 보낸 이미지에 따름(지금 화면은 PNG 로 보냄).
- 오류: 이미지 없이 저장했으면 `404 {"error": "없음"}`

## 흐름 예

### 1) 사진 → 그림 (지금 화면 순서)
```js
const BASE = '/t/sketch-local/';
const post = (p, b) => fetch(BASE + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }).then(r => r.json());

// ① 이미지 → base64 (긴 변 1600px 로 줄인 PNG)
const image_b64 = canvas.toDataURL('image/png').split(',')[1];
// ② 읽기
let { reading } = await post('api/read', { image_b64, vision_model: '' });
// ③ (사용자가 reading 을 고침) → 계획
let p = await post('api/plan', { reading, mode: '단순화', answers: null });
if (p.questions.length) {                           // 질문이 있으면 답을 모아 한 번 더
  const answers = Object.fromEntries(p.questions.map(q => [q.key, q.default]));
  p = await post('api/plan', { reading: p.reading, mode: '단순화', answers });
}
reading = p.reading;
// ④ 그리기 (SSE: fetch + getReader, diagram-local 문서의 읽기 코드와 같음)
const r = await fetch(BASE + 'api/draw', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ reading, mode: '단순화', style: p.recommend.style, type: p.recommend.type, max_nodes: 8 }) });
// ... data: 줄을 읽다가 ev.done.source 를 mermaid.render() 로 그림
// ⑤ 저장
const { id } = await post('api/save', { id: null, image_b64, reading, mode: '단순화', style: p.recommend.style, type: p.recommend.type, source });
```

### 2) 저장본 다시 열기
`GET api/runs` → `GET api/runs/<id>` (reading·source 등) → 이미지는 `<img src="api/runs/<id>/sketch.png">`.

## 화면만의 것 / 주의
- **그림 렌더·내보내기는 브라우저**: Mermaid 렌더, SVG(`diagram.svg`)·PNG(`diagram.png`, 2배 래스터) 다운로드, 소스 복사, TikZ 결과 복사. 서버에 SVG/PNG 생성 API 는 없다.
- 이미지 축소(1600px)와 PNG 변환은 화면이 한다. reading 의 요소/연결 편집 텍스트 형식(`n1 -> n2 : 라벨 (?)`)도 화면 전용 — API 는 JSON 만 받는다.
- 모드 버튼(원본/단순화/인사이트)·질문 UI 도 화면 몫. API 는 `mode` 문자열만 받는다.
- 렌더 실패 시 `api/repair` 자동 1회 호출도 화면 로직.
- 사용자 구분: 포털의 `X-Agent-User` 헤더로 저장 폴더를 나눈다(`WORKSPACE/u/<id>/<run id>/`). `owner`/헤더 없음은 기본 폴더. 공유 데이터·owner 전용 기능 없음.
- 저장본을 다시 열면 지금 화면은 `image_b64` 를 다시 채우지 않는다 → 그 상태로 다시 저장하면 이미지 없이 보내지만, 같은 id 폴더라 이전 `sketch.png` 는 남는다.
- 정적 파일: `static/mermaid.min.js` 하나. 모든 응답에 `X-Author` 헤더.
