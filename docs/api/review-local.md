# 논문 사전 심사 API (`review-local`)
원고(PDF·DOCX·HWP·HWPX·TXT·MD·TEX)를 올리면 구조를 읽고 기계적 점검(형식·그림·인용·약어·숫자 등)을 바로 하고, 로컬 LLM 으로 심사위원식 리뷰(개요·발췌별 지적·교차 확인·종합 판정·예상 질문)를 합니다.
포털 경로 접두어: `/t/review-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/run` → `https://<서버>/t/review-local/api/run`)

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- 포털이 `X-Agent-User` 를 붙여 사용자별 폴더(`WORKSPACE/u/<id>/history/`)에 저장합니다.
- POST 본문은 JSON, `Content-Length` 필수. 응답에 `Cache-Control: no-store`.
- 이력 id 형식: `YYYYMMDD-HHMMSS-xxxx` (16진 4자리). 원고 하나 = 이력 하나(리뷰를 다시 돌리면 같은 이력을 덮어씀).

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | 아니오 | 아니오 |
| GET | `api/meta` | 원고 유형·깊이·언어 선택지, 기본 모델, 추출 도구 상태 | 아니오 | 아니오 |
| GET | `api/models` | LLM 모델 목록 | LLM 서버 조회 | 아니오 |
| GET | `api/history` | 내 이력 목록(최근 200) | 아니오 | 예 |
| GET | `api/history/<id>` | 이력 하나 전체(원고 전문·지적·리뷰) | 아니오 | 예 |
| GET | `api/export/<id>.<md\|txt\|docx>` | 리뷰 보고서 다운로드 | 아니오 | 예 |
| POST | `api/upload` | 원고 업로드 → 추출 + 기계적 점검 + 이력 생성 | 수 초~수십 초(추출) | 예(저장) |
| POST | `api/run` | LLM 리뷰 (SSE) | 예(LLM, 빠름 수 분 / 정밀 수십 분) | 예(저장) |
| POST | `api/history/delete` | 이력 삭제 | 아니오 | 예(삭제) |
| GET | 그 밖의 경로 | 화면 HTML | — | — |

## 엔드포인트

### GET api/meta
- 응답:
```json
{"mtypes": ["연구논문", "리뷰(총설)", "학회 논문", "단신·레터", "학위논문(장)", "기술보고서"],
 "depths": [{"id": "quick", "label": "빠름"}, {"id": "deep", "label": "정밀"}],
 "langs": [{"id": "auto", "label": "원고 언어"}, {"id": "ko", "label": "국문"}, {"id": "en", "label": "영문"}],
 "model": "<기본 모델>", "tools": {"pdftotext": true, "kordoc": true}}
```
  - `tools.kordoc`: HWP·HWPX·DOCX 추출 도구 유무, `tools.pdftotext`: PDF 추출 도구 유무.

### GET api/models
- 응답: 모델 이름 배열. `api/run` 의 `model` 에 넣을 수 있음.

### GET api/history
- 응답(최신순, 최대 200):
```json
[{"id": "20261007-062412-0942", "name": "paper.pdf", "title": "원고 제목", "ts": "2026-10-07T06:24:12",
  "verdict": "Major Revision", "reviewed": true, "n": {"major": 3, "minor": 5, "info": 2}}]
```
  - `reviewed`: LLM 리뷰를 돌렸는지. `verdict`: `Accept`·`Minor Revision`·`Major Revision`·`Reject` 또는 빈 문자열.

### GET api/history/<id>
- 응답: 이력 레코드(아래 "레코드 모양"). 업로드·리뷰 응답과 같은 모양.
- 오류: 형식이 틀리거나 없으면 `404 {"error":"없음"}`.

### GET api/export/<id>.<md|txt|docx>
- 응답: 보고서 파일. `Content-Disposition: attachment; filename="review.<확장자>"; filename*=UTF-8''<원고이름>_review.<확장자>`
  - `md` → `text/markdown; charset=utf-8`, `txt` → `text/plain; charset=utf-8`, `docx` → `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- 메모: 리뷰 전이면 기계적 점검만 담깁니다. 화면은 `location.href` 로 이동해 받습니다.

### POST api/upload
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `name` | 문자열 | 예 | 파일명. 확장자로 형식 판단(`.pdf .docx .hwp .hwpx .txt .md .tex`) |
| `data` | 문자열 | 예 | 파일 바이트 base64(데이터 URL 접두어 제외) |
| `mtype` | 문자열 | 아니오 | 원고 유형(`api/meta` 의 mtypes). 구조 점검 기준이 달라짐 |

- 응답: 레코드(`review` 는 null, `issues` 는 기계적 점검 결과만).
- 오류: `400 {"error":"지원하지 않는 형식: .xyz — PDF·DOCX·HWP·HWPX·TXT"}`, `400 {"error":"추출된 글자가 거의 없습니다. 스캔(이미지) PDF 이면 …"}`, `400 {"error":"파일이 비어 있습니다"}`, 본문이 크면 `413 {"error":"파일이 너무 큽니다 (최대 60MB)"}`(base64 본문 약 84MB 초과 시, 포털을 거쳐도 413).
- 메모: 원본 파일은 추출 뒤 바로 지우고, 추출된 텍스트·구조만 이력에 남습니다.

### POST api/run  (SSE)
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `id` | 문자열 | 예 | `api/upload` 가 준 이력 id |
| `journal` | 문자열 | 아니오 | 대상 저널·분야(그 눈높이로 심사) |
| `mtype` | 문자열 | 아니오 | 원고 유형. 업로드 때와 다르면 기계적 점검을 다시 함 |
| `depth` | 문자열 | 아니오 | `quick`(기본: 발췌 2만 자 × 최대 5개, 발췌당 지적 4) · `deep`(8천 자 × 최대 24개, 발췌당 6) |
| `lang` | 문자열 | 아니오 | 설명 언어 `auto`(원고 언어)·`ko`·`en` |
| `model` | 문자열 | 아니오 | LLM 모델. 비우면 서버 기본 |

- 응답: `text/event-stream`, 이벤트마다 `data: <JSON>\n\n`.
  - `{"stage":"wait","msg":"다른 리뷰가 LLM 을 쓰는 중 — 순서를 기다립니다"}` — 리뷰는 서버 전체에서 한 번에 하나
  - `{"stage":"overview","msg":"원고 개요 파악 (기여·주장)","step":1}`
  - `{"stage":"note","msg":"원고가 길어 본문의 약 62%(발췌 5개)를 …"}`
  - `{"stage":"chunk","msg":"발췌 2/5 심사: 2. Methods","step":3,"steps":7,"reset":true}` — `reset` 이면 토큰 표시 칸을 비움
  - `{"stage":"chunk_done","msg":"발췌 2: 지적 3개 확인, 1개 제외(인용 불일치·일반론)"}`
  - `{"stage":"cross","msg":"교차 확인: …","reset":true}`, `{"stage":"synth","msg":"종합 판정·예상 질문","step":7,"reset":true}`
  - `{"token":"…"}` — LLM 출력 원문 토큰(진행 표시용, 가공 전)
  - 끝: `{"done": <레코드>}`
  - 실패: `{"error":"…"}` (HTTP 200 스트림 안)
- 메모: 진행률은 `step`/`steps` 로 대략 계산. 작업 id 폴링 방식은 없고, 도중에 연결을 끊으면 다음 이벤트를 쓸 때 작업이 멈추고 리뷰 결과는 저장되지 않습니다(연결을 끝까지 유지). 끝난 뒤 `api/history/<id>` 로 다시 읽을 수 있습니다.

### POST api/history/delete
- 요청: `{"id": "20261007-062412-0942"}` → 응답 `{"ok": true}` (없는 id 여도 ok).
- 오류: 형식이 틀리면 `400 {"error":"잘못된 이력 id"}`.

## 레코드 모양 (upload·run·history/<id> 공통)
```json
{"id": "20261007-062412-0942", "ts": "2026-10-07T06:24:12", "options": {"mtype": "연구논문", "depth": "quick", "lang": "auto"},
 "doc": {"name": "paper.pdf", "via": "pdftotext", "lang": "ko", "title": "원고 제목", "text": "원고 전문…",
         "pages": [0, 3120, 6402], "npages": 3, "captions": [{"type": "…", "num": "1", "label": "Fig. 1", "pos": 1234, "len": 40, "text": "…"}],
         "ref_style": "numeric", "outline": [{"title": "서론", "kind": "intro", "label": "서론", "start": 120, "end": 2400, "page": 1, "level": 1, "chars": 2280}],
         "nrefs": 24},
 "issues": [{"id": "M1", "sev": "major", "cat": "방법", "title": "요지", "quote": "원고 원문 인용", "pos": 1532, "len": 48,
             "where": "2. 방법", "page": 1, "why": "문제", "fix": "수정 제안", "src": "llm", "match": 0.97}],
 "review": {"summary": {"contrib": ["…"], "claims": ["…"], "design": ["…"]},
            "verdict": {"label": "Major Revision", "raw": "판정 원문", "reasons": [{"text": "…#M1…", "refs": ["M1"]}]},
            "questions": [{"q": "예상 질문", "tip": "답변 준비", "refs": ["M2"]}],
            "dropped": [{"quote": "…", "why": "…", "reason": "원고에서 인용을 찾지 못함", "title": "…", "where": ""}],
            "chunks": [{"titles": ["2. Methods"], "start": 2400, "end": 22000}], "coverage": 62,
            "lang": "ko", "model": "…", "elapsed": 312, "options": {…}, "depth": "빠름", "ts": "…"}}
```
- `issues`: 기계적 점검(`src:"rule"`, id `R1…`, `sev` 는 `major`·`minor`·`info`)과 LLM 지적(`src:"llm"`, id `M1…`(major)·`m1…`(minor))이 함께 들어 있습니다. `pos`·`len` 은 `doc.text` 안의 글자 위치 — 원문 하이라이트에 씁니다. `page` 는 1쪽짜리 문서면 null.
- `doc.pages`: 각 쪽이 시작하는 `text` 위치(쪽 번호 계산용). `captions` 의 세부 필드는 `manuscript.py` 기준(코드 확인 필요).
- `review` 는 리뷰 전 null. `reasons[].refs`·`questions[].refs` 는 지적 id 로 연결.

## 흐름 예
```js
const base = '/t/review-local/', post = (p, b) => fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
const b64 = await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.readAsDataURL(file); });
const rec = await post('api/upload', { name: file.name, data: b64, mtype: '연구논문' }).then(async r => { const j = await r.json(); if (!r.ok) throw new Error(j.error); return j; });

const r = await post('api/run', { id: rec.id, depth: 'quick', lang: 'auto', journal: '' });
const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', done = null;
for (;;) {
  const { value, done: end } = await rd.read(); if (end) break;
  buf += dec.decode(value, { stream: true });
  let i; while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data: ')) continue;
    const ev = JSON.parse(line.slice(6));
    if (ev.error) throw new Error(ev.error);
    if (ev.stage) showStep(ev.msg, ev.reset); if (ev.token) appendRaw(ev.token);
    if (ev.done) done = ev.done;
  }
}
location.href = base + `api/export/${done.id}.docx`;   // 보고서 받기
```

## 화면만의 것 / 주의
- 원문 보기에서 지적 위치 하이라이트·쪽 계산·목차 이동, 심각도 필터, 지적 개수 집계는 화면이 `doc.text`·`pos`·`len`·`outline` 으로 직접 합니다.
- 60MB 사전 확인, 옵션 기억(localStorage)은 화면 기능입니다.
- `api/meta` 는 LLM 서버 주소를 내보내지 않습니다(모델 이름만). LLM 연결 실패 오류 문장에도 주소는 없습니다.
- 코드에 `cross`(교차 확인 끄기) 옵션 분기가 있지만 `api/run` 이 받는 옵션 목록에 없어 API 로는 끌 수 없습니다(항상 켜짐).
- `/static/` 경로는 없습니다.
