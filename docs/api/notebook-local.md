# 노트북 API (`notebook-local`)
문서를 출처로 올려 두고 출처 인용 채팅·노트·스튜디오(요약·FAQ·팟캐스트 대본+음성)를 하는 소스 기반 노트북.
포털 경로 접두어: `/t/notebook-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/notebook-local/api/notebooks`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/models` | 모델 목록·기본 모델·임베딩 모델·TTS 연결 여부 | 아니오 | 아니오 |
| GET | `api/notebooks` | 노트북 목록 | 아니오 | 예 |
| POST | `api/notebooks` | 노트북 만들기·이름 바꾸기·삭제 (`op`) | 아니오 | 예 |
| GET | `api/nb/<nb_id>` | 노트북 하나의 출처·노트 | 아니오 | 예 |
| POST | `api/nb/<nb_id>/source` | 출처 추가(SSE) / 포함 토글 / 삭제 (`op`) | 추가는 예 — 파싱·임베딩·LLM 요약 | 예 |
| POST | `api/nb/<nb_id>/chat` | 출처 인용 질문 답변 (SSE) | 예 — 임베딩+LLM | 예(기록은 저장 안 함) |
| POST | `api/nb/<nb_id>/note` | 노트 저장·삭제·출처로 넣기 (`op`) | `to_source` 만 예 — LLM 요약 | 예 |
| POST | `api/nb/<nb_id>/studio` | 요약·FAQ·가이드·타임라인·브리핑·팟캐스트 생성 (SSE) | 예 — LLM(+TTS), 수 분 | 예(노트로 저장) |
| GET | `api/nb/<nb_id>/audio/<파일>.mp3` | 팟캐스트 음성 | 아니오 | 예 |
| GET | `api/polish_ok` | 윤문 기능(kordoc-local) 연결 여부 | 아니오 | 아니오 |
| POST | `api/polish` | 글 윤문 (kordoc-local 로 넘김) | 예 — LLM | 아니오 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/models
- 응답
```json
{"models": ["gemma4:31b", "qwen3:8b", "bge-m3:latest"], "default": "gemma4:31b", "embed": "bge-m3", "tts": true}
```
- `tts`: TTS 서버가 설정돼 있으면 true(팟캐스트 음성 생성 가능). 실제로 살아 있는지는 확인 안 함.
- 오류: LLM 서버 연결 실패 → 500 `{"error": "..."}`

### GET api/notebooks
- 응답: `[{"id": "f4012157", "name": "이름", "created": "2026-10-07T06:24:44"}]` (최근 것 먼저)

### POST api/notebooks
| `op` | 다른 필드 | 하는 일 |
|---|---|---|
| `create`(기본) | `name`(없으면 "새 노트북") | 새 노트북 |
| `rename` | `id`, `name` | 이름 바꾸기 |
| `delete` | `id` | 노트북과 출처·청크·노트·음성 파일 모두 삭제 |
- 응답: 언제나 `{"ok": true}` — **새로 만든 노트북의 id 는 돌려주지 않음**. 만든 뒤 `api/notebooks` 를 다시 읽어야 함.
- 오류: 필드 빠짐 등 → 500 `{"error": "KeyError: 'id'"}`

### GET api/nb/<nb_id>
- 응답
```json
{"notebook": {"id": "f4012157", "name": "...", "created": "..."},
 "sources": [{"id": "1a2b3c4d", "name": "보고서.hwpx", "kind": "hwpx", "summary": "출처 요약",
              "included": 1, "created": "...", "chars": 15320, "chunks": 22, "embedded": 22}],
 "notes": [{"id": "9f8e7d6c", "nb": "f4012157", "title": "FAQ · 2026-10-07", "body": "마크다운", "created": "..."}]}
```
- `included`: 1 이면 채팅·스튜디오에 쓰임. `embedded` < `chunks` 면 그 출처는 BM25 로만 검색됨.
- 노트 `body` 끝에 `<!-- audio: <파일>.mp3 -->` 가 붙어 있으면 팟캐스트 음성이 있는 노트.
- 없는 노트북이면 `notebook: null` 과 빈 목록(404 아님).

### POST api/nb/<nb_id>/source
- `nb_id` 는 16진 id 여야 함(아니면 500 `{"error": "ValueError: 잘못된 id"}`).
- `op: "toggle"` + `id` → 포함/제외 뒤집기, `{"ok": true}`
- `op: "delete"` + `id` → 출처 삭제, `{"ok": true}`
- `op: "add"`(기본) — **SSE**. 요청 필드:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `file_name`, `file_b64` | string | 둘 중 하나 | 문서 이름과 base64 내용. 확장자 `.hwp .hwpx .hml .pdf .docx .xlsx .xls .png .jpg .jpeg .webp .md .txt` |
| `text` | string | 둘 중 하나 | 붙여넣은 글(파일이 없을 때) |
| `name` | string | 아니오 | 글 출처의 이름(파일이면 `file_name` 이 이름) |
| `model` | string | 아니오 | 출처 요약 모델 |

- 이벤트: `{"stage": "parse"|"embed"|"summary", "msg": "..."}` → `{"done": {"id", "name", "kind", "chunks", "summary", "embedded": bool}}` 또는 `{"error": "..."}`
- 메모: 파싱(kordoc) → 800자 청크 → 임베딩 → LLM 요약 1콜(앞 6,000자). 큰 문서는 수십 초~수 분. 임베딩 서버가 없으면 BM25 전용으로 저장. 요약이 실패해도 출처는 저장되고 `summary` 에 `(요약 실패: ...)`.

### POST api/nb/<nb_id>/chat
- 요청: `{"question": "질문", "history": "이전 대화 텍스트(선택)", "model": "선택"}`
  - `history` 는 문자열 하나. 화면은 최근 6줄(`Q:`·`A:` 식)을 줄바꿈으로 이어 보냄. 서버는 대화를 저장하지 않음.
- 이벤트 순서
  1. `{"cites": [{"n": 1, "id": "청크id", "source": "출처 이름", "heading": "제목 경로", "text": "청크 본문"}], "mode": "hybrid"|"bm25"|"none"}` — 검색된 상위 8개
  2. `{"token": "..."}` 여러 번 — 답변 스트리밍(본문에 `[1]` 같은 인용 번호)
  3. `{"done": {"question", "answer", "cites", "used": [1, 3], "mode", "model"}}` — `used` = 답에 실제로 나온 인용 번호
  - 실패 시 `{"error": "..."}`
- 메모: LLM 1콜, 수 초~수십 초.

### POST api/nb/<nb_id>/note
| `op` | 다른 필드 | 응답 |
|---|---|---|
| `save`(기본) | `id`(있으면 덮어쓰기, 없으면 새로), `title`, `body` | `{"ok": true}` (새 id 는 안 돌려줌) |
| `delete` | `id` | `{"ok": true}` |
| `to_source` | `id`, `model` | 출처 추가 결과(`source` add 의 `done` 과 같은 모양) — **SSE 아님, 한 번에**. LLM 요약이 돌아 오래 걸릴 수 있음 |

### POST api/nb/<nb_id>/studio
- 요청: `{"kind": "summary"|"faq"|"guide"|"timeline"|"briefing"|"podcast", "model": "선택"}`
- 이벤트: `{"stage": "llm", "msg": "...", "reset": true}` → `{"token": "..."}`… → (podcast + TTS) `{"stage": "tts", "msg": "음성 합성 3/40"}`… → `{"done": {"note_id", "title", "body", "audio": "<note_id>.mp3"|null, "tts": bool, "truncated": bool}}`
- 메모: 포함된 출처 본문을 합쳐 최대 20,000자(출처마다 균등 분할)만 넣음 → `truncated`. 결과는 자동으로 노트로 저장됨. 팟캐스트 대본은 `A: …`/`B: …` 줄 형식이고 줄마다 TTS 를 불러 이어붙임(수 분). TTS 실패는 `stage: tts` 메시지로만 알리고 `audio: null`.
- 오류: 포함된 출처 없음 → `{"error": "ValueError: 포함된 출처가 없습니다"}`

### GET api/nb/<nb_id>/audio/<파일>.mp3
- 응답: `audio/mpeg` 바이너리. `<audio src>` 로 바로 재생. 없으면 404 `{"error": "없음"}`.

### GET api/polish_ok · POST api/polish
- `api/polish_ok` → `{"ok": true|false}` (kordoc-local 이 살아 있는지)
- `api/polish` 요청 `{"text": "...", "strength": "light|standard|strong"}` → kordoc-local `api/polish_text` 응답 그대로(`output`, `changes`, `rejected`, ...). 자세한 모양은 [kordoc-local.md](kordoc-local.md).
- 오류: 500 `{"error": "RuntimeError: 빈 글"}` 식(kordoc 의 400 도 500 으로 바뀌어 옴)

## 흐름 예
```js
const base = '/t/notebook-local/';
const post = (u, b) => fetch(base + u, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)});
async function sse(u, b, on) {   // done 을 돌려주는 SSE 읽기
  const r = await post(u, b), rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
  for (;;) { const {value, done} = await rd.read(); if (done) break; buf += dec.decode(value, {stream: true});
    let i; while ((i = buf.indexOf('\n\n')) >= 0) { const l = buf.slice(0, i); buf = buf.slice(i + 2);
      if (!l.startsWith('data: ')) continue; const ev = JSON.parse(l.slice(6));
      if (ev.error) throw new Error(ev.error); if (ev.done) return ev.done; on(ev); } }
}
// 1) 노트북 만들고 id 찾기
await post('api/notebooks', {op: 'create', name: '예산 검토'});
const nb = (await fetch(base + 'api/notebooks').then(r => r.json()))[0].id;
// 2) 문서 출처 추가
await sse(`api/nb/${nb}/source`, {file_name: f.name, file_b64: b64}, ev => console.log(ev.msg));
// 3) 질문
let ans = '';
const done = await sse(`api/nb/${nb}/chat`, {question: '예산 총액은?'}, ev => { if (ev.token) ans += ev.token; });
console.log(done.answer, done.cites.filter(c => done.used.includes(c.n)));
```

## 화면만의 것 / 주의
- 경로는 정확히 일치해야 함 → 쿼리를 붙이면 화면 HTML 이 돌아옴.
- 화면이 하는 것: 채팅 기록(`history`) 관리, 답변의 `[n]` 을 인용 카드로 바꾸기, 노트 본문의 `<!-- audio: ... -->` 를 읽어 플레이어 붙이기.
- 화면의 노트 편집 창은 본문에서 audio 주석을 지운 채 보여 주므로, **편집 후 저장하면 노트와 음성 연결이 끊김**(파일은 남음). 새 화면에서는 주석을 보존해 저장할 것.
- 노트북 데이터는 사용자 폴더의 SQLite(`notebook.db`)에 따로 저장. 다른 사용자 것은 안 보임.
- 정적 파일 경로 없음.
