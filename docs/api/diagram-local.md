# diagram API (`diagram-local`)
말로 설명하면 LLM 이 Mermaid 다이어그램 소스를 만들어 준다. 그림(SVG·PNG)은 서버가 아니라 **브라우저의 mermaid.js** 가 그린다.
포털 경로 접두어: `/t/diagram-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/diagram-local/api/runs`)

서버는 Python 표준 `http.server` 로 만든 단일 파일(`app.py`). 경로는 **정확히 일치**해야 하며 쿼리스트링(`?…`)을 붙이면 매칭되지 않고 화면 HTML 이 돌아온다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | 쓸 수 있는 LLM 모델 이름 목록 | 아니오 (LLM 서버에 목록만 질의) | 아니오 |
| POST | `api/diagram` | 대화 → Mermaid 소스 생성 (SSE 스트리밍), 결과 저장 | **예 (LLM, 수십 초~수 분)** | 예 (기록 저장) |
| POST | `api/repair` | 파싱 오류 난 소스를 LLM 으로 1회 고침 | **예 (LLM)** | 아니오 (저장 안 함) |
| GET | `api/runs` | 내 최근 기록 50개 요약 | 아니오 | 예 |
| GET | `api/runs/<id>` | 기록 하나 전체 | 아니오 | 예 |
| GET | `static/mermaid.min.js` | 동봉된 Mermaid 라이브러리 | 아니오 | 아니오 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/models
- 요청: 없음
- 응답: 문자열 배열. LLM 서버(Ollama `/api/tags` 또는 OpenAI 호환 `/models`)의 모델 이름 그대로.
  ```json
  ["gemma4:31b", "qwen3:32b", "qwen3:8b"]
  ```
  임베딩 모델(`bge-m3` 등)도 섞여 나오므로 화면에서 걸러도 된다.
- 오류: LLM 서버에 닿지 않으면 `500 {"error": "URLError: ..."}`
- 메모: 서버 기본 모델은 화면 HTML 안의 `%MODEL%` 자리에 JSON 문자열로 박혀 나간다(별도 API 없음). 새 화면에서 기본값이 필요하면 배열 첫 항목을 쓰거나 `model` 을 비워 보내면 서버 기본 모델이 쓰인다.

### POST api/diagram
- 요청: JSON (`Content-Type: application/json`)

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `messages` | `[{role, content}]` | 예 | 대화 전체. `role` 은 `user`·`assistant` 만 남기고, `content` 가 빈 항목은 버림 |
  | `type` | 문자열 | 아니오 | `auto`(기본)·`flowchart`·`sequence`·`class`·`er`·`state`·`gantt`·`mindmap`·`timeline`·`pie`. `auto` 가 아니면 마지막 user 메시지 끝에 `[유형] flowchart TD 로 그린다.` 같은 힌트가 붙음 |
  | `model` | 문자열 | 아니오 | 비우면 서버 기본 모델 |

  고쳐 그리기(대화 이어가기): 지금 화면은 두 번째 요청부터 user 메시지를 `"[현재 소스]\n<현재 Mermaid>\n\n[요청]\n<새 요청>"` 형태로 만들어 보낸다. 서버는 이 형식을 강제하지 않고 그대로 LLM 에 넘긴다.
- 응답: `200`, `Content-Type: text/event-stream; charset=utf-8`. 이벤트는 `data: <JSON>\n\n` 줄 하나씩이며 `event:` 이름은 없음.
  ```
  data: {"token": "flowchart TD\n"}
  data: {"token": "  A[로그인] --> B"}
  ...
  data: {"done": {"id": "2026-10-13-ab12", "source": "flowchart TD\n  A[로그인] --> B[토큰 발급]", "sanity": null}}
  ```
  | 이벤트 | 설명 |
  |---|---|
  | `{"token": "..."}` | LLM 이 낸 글자 조각(코드펜스·`<think>` 가 섞일 수 있는 원문) |
  | `{"done": {id, source, sanity}}` | 끝. `source` 는 코드펜스·`<think>` 를 걷어낸 최종 Mermaid. `sanity` 는 서버 1차 검사 결과(문제없으면 `null`, 있으면 `"괄호 불일치 []"`·`"첫 줄이 다이어그램 선언이 아님: ..."`·`"큰따옴표 홀수"`·`"빈 출력"` 같은 문자열). `id` 는 저장된 기록 id |
  | `{"error": "..."}` | 생성 중 실패. 스트림은 이걸로 끝남(HTTP 상태는 이미 200) |
- 오류: 남은 메시지가 없으면 스트림 시작 전에 `400 {"error": "빈 입력"}` (일반 JSON).
- 메모:
  - `done` 시점에 `messages + [{role:"assistant", content: source}]` 가 사용자 작업 폴더에 `<id>.json` 으로 저장된다(id 형식 `YYYY-MM-DD-xxxx`, 16진 4자리).
  - 진짜 문법 검사는 브라우저의 `mermaid.render()` 가 한다. `sanity` 가 `null` 이어도 렌더가 실패할 수 있다.
  - 걸리는 시간은 모델·길이에 따라 수십 초~수 분(LLM 타임아웃 3600초).
  - 이 서버는 POST 경로 중 `api/repair` 가 아닌 것은 **모두** `api/diagram` 으로 처리한다(경로 오타도 생성이 돌아감). 본문이 올바른 JSON 이 아니면 응답 없이 연결이 끊긴다.

### POST api/repair
- 요청: JSON

  | 이름 | 형식 | 필수 | 설명 |
  |---|---|---|---|
  | `source` | 문자열 | 사실상 예 | 깨진 Mermaid 소스 |
  | `error` | 문자열 | 사실상 예 | 브라우저 mermaid 가 낸 파싱 오류 메시지 |
  | `model` | 문자열 | 아니오 | 비우면 서버 기본 모델 |
- 응답: 일반 JSON(스트리밍 아님)
  ```json
  {"source": "flowchart TD\n  A --> B", "sanity": null}
  ```
- 오류: `500 {"error": "RuntimeError: ..."}` (LLM 실패 등)
- 메모: 기록을 저장하지 않는다. 화면은 렌더 실패 시 자동으로 1회만 부르고, 고친 소스로 대화의 마지막 assistant 메시지를 바꿔 끼운다(서버에는 반영 안 됨 — 다음 `api/diagram` 요청의 `messages` 에 실려 갈 뿐).

### GET api/runs
- 응답: 최근 수정 순 최대 50개
  ```json
  [{"id": "2026-10-13-ab12", "type": "flowchart", "model": "qwen3:8b", "head": "결재 절차", "ts": "2026-10-13T09:12:30"}]
  ```
  `head` = 첫 user 메시지 앞 60자, `ts` = 저장 시각(초 단위 ISO, 서버 지역시간).
- 오류: 없음(폴더가 없으면 `[]`).

### GET api/runs/<id>
- `<id>` 는 `YYYY-MM-DD-xxxx` 형식만 받음(정규식 불일치면 화면 HTML 이 돌아옴).
- 응답: 저장 파일 그대로
  ```json
  {"id": "...", "type": "auto", "model": "...", "messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
   "source": "flowchart TD ...", "head": "...", "ts": "..."}
  ```
- 오류: 없으면 `404 {"error": "없음"}`

### GET static/mermaid.min.js
- 동봉 Mermaid (`application/javascript`). 새 화면에서도 CDN 대신 이걸 `<script src="static/mermaid.min.js">` 로 쓸 수 있다.

## 흐름 예

### 1) 새로 그리기 (스트리밍 읽기)
```js
const BASE = '/t/diagram-local/';
const messages = [{ role: 'user', content: '사용자가 로그인하면 인증서버가 토큰을 발급' }];
const r = await fetch(BASE + 'api/diagram', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ messages, type: 'sequence', model: '' }),
});
if (!r.ok) throw new Error((await r.json()).error);          // 400 빈 입력
const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
for (;;) {
  const { value, done } = await reader.read(); if (done) break;
  buf += dec.decode(value, { stream: true });
  let i; while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data: ')) continue;
    const ev = JSON.parse(line.slice(6));
    if (ev.token) showProgress(ev.token);
    if (ev.error) throw new Error(ev.error);
    if (ev.done) {
      messages.push({ role: 'assistant', content: ev.done.source });
      const { svg } = await mermaid.render('m' + Date.now(), ev.done.source); // 브라우저가 그림
      view.innerHTML = svg;
    }
  }
}
```
`EventSource` 는 POST 를 못 하므로 위처럼 `fetch` + `getReader()` 로 읽는다.

### 2) 렌더 실패 시 자동 수정
```js
try { await mermaid.render('m1', src); }
catch (e) {
  const j = await fetch(BASE + 'api/repair', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: src, error: e.message, model }) }).then(r => r.json());
  if (j.source) await mermaid.render('m2', j.source);
}
```

### 3) 기록 불러오기
`GET api/runs` → 항목 클릭 시 `GET api/runs/<id>` → `messages` 로 대화 복원, `source` 를 렌더.

## 화면만의 것 / 주의
- **그림 그리기·내보내기는 전부 브라우저**: Mermaid 렌더, SVG 다운로드(`diagram.svg`), PNG 다운로드(캔버스로 2배 크기 래스터화 → `diagram.png`), 소스 복사, 소스 직접 수정 시 즉시 다시 그리기. 서버에는 SVG/PNG 를 만드는 API 가 없다.
- 소스 편집창에서 손으로 고친 내용은 서버에 저장되지 않는다(다음 생성 요청 때 `[현재 소스]` 로 실려 갈 뿐).
- 다크 테마에서 내보낼 때 화면은 Mermaid `default` 테마로 한 번 더 그려 밝은 바탕으로 내보낸다(화면 로직).
- 사용자 구분: 포털이 붙이는 `X-Agent-User` 헤더로 작업 폴더를 나눈다(`WORKSPACE/u/<id>/`). 헤더가 없거나 `owner` 면 기본 작업 폴더. 기록은 사용자 사이에 공유되지 않는다. 삭제 API 는 없다.
- 권한 구분(owner 전용 기능)은 없음.
- 정적 파일: `static/mermaid.min.js` 하나뿐. 응답에는 `X-Author` 헤더가 붙는다.
