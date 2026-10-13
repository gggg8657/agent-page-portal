# 회의록 API (`meeting-local`)
회의 녹음 → 전사(faster-whisper) + 화자 분리 → 화자 이름 지정 → 로컬 LLM 으로 구간별 인사이트·회의록(Markdown, 선택 HWPX).
포털 경로 접두어: `/t/meeting-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/run` → `https://<서버>/t/meeting-local/api/run`)

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- 포털이 `X-Agent-User` 를 붙여 사용자별 폴더(`WORKSPACE/u/<id>`)에 저장합니다. 클라이언트가 보낸 값은 무시됩니다.
- POST 는 `Content-Length` 가 반드시 있어야 합니다(청크 업로드 미지원). 포털 프록시 제한 시간 1시간.
- `run_id` 형식: `YYYY-MM-DD-xxxx` (16진 4자리).

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/runs` | 내 실행 목록(최근 50) | 아니오 | 예 |
| GET | `api/runs/<run_id>` | 실행 하나 전체(전사·화자·회의록·인사이트) | 아니오 | 예 |
| GET | `api/runs/<run_id>/clip?s=<초>&e=<초>` | 화자 확인용 짧은 wav(최대 10초) | ffmpeg(짧음) | 예 |
| GET | `api/runs/<run_id>/<파일>` | 결과 파일 다운로드 | 아니오 | 예 |
| GET | `api/models` | LLM 서버의 모델 이름 목록 | LLM 서버 조회 | 아니오 |
| GET | `api/polish_ok` | 윤문(kordoc-local) 쓸 수 있는지 | 아니오 | 아니오 |
| POST | `api/run` | 1단계: 업로드 + 전사 + 화자 분리 (SSE) | 예(GPU ASR, 녹음 길이의 10~30%) | 예(저장) |
| POST | `api/finalize/<run_id>` | 2단계: 이름 적용 + 인사이트 + 회의록 (SSE) | 예(LLM, 수 분) | 예(저장) |
| POST | `api/polish` | 저장된 회의록 윤문(덮어씀) | 예(kordoc LLM, 수십 초~) | 예(수정) |
| POST | `v1/audio/transcriptions` | OpenAI 호환 STT(화자 분리 없음) | 예(GPU ASR) | 임시 파일만 |
| GET | 그 밖의 경로 | 화면 HTML | — | — |

## 엔드포인트

### GET api/runs
- 응답(최신순):
```json
[{"run_id": "2026-10-06-c359", "file": "00_meeting.mp3", "duration": 140.0, "n_speakers": 3, "status": "done", "ts": "2026-10-06T03:16:21"}]
```
  - `status`: `transcribed`(1단계까지) | `done`(회의록까지). `duration`: 초. `file`: 저장된 업로드 이름(앞에 `00_`).

### GET api/runs/<run_id>
- 응답: 1단계 결과(아래 `api/run` 의 `done`)와 같은 모양 + 2단계 뒤에는 `minutes`, `insights`, `names`, `meta`, `hwpx` 가 더 붙음. `utts` 의 `speaker` 는 지정한 이름으로 바뀌어 나옵니다.
- 오류: 없으면 `404 {"error":"없음"}`.

### GET api/runs/<run_id>/clip?s=<시작초>&e=<끝초>
- 응답: `audio/wav`. 길이는 최대 10초로 잘림. 서버에 캐시(`clip_<s>_<e>.wav`).
- 메모: 쿼리는 정확히 `s=…&e=…` 순서, 숫자(소수점 허용)만. 순서가 다르면 화면 HTML 이 돌아옵니다.

### GET api/runs/<run_id>/<파일>
- `<파일>`: `minutes.md` · `minutes.hwpx` · `insights.json` · `transcript.txt` · `transcript.json`
- 응답: `Content-Type: application/octet-stream`, `Content-Disposition: attachment; filename*=UTF-8''<run_id>_<파일>`.
- 오류: 아직 없으면(예: 1단계만 끝남, HWPX 미생성) `404 {"error":"없음"}`.

### GET api/models
- 응답: 문자열 배열, 예 `["qwen3:8b", "gemma4:31b"]`. 서버 설정 LLM(Ollama 또는 OpenAI 호환)의 모델 목록. 회의록 생성에 쓰는 모델은 서버 설정(`LLM_MODEL`) 고정이고 요청으로 바꿀 수 없습니다(화면도 이 API 를 쓰지 않음).
- 오류: LLM 서버가 안 되면 `500 {"error":"URLError: ..."}`.

### GET api/polish_ok
- 응답: `{"ok": true}` — 옆의 kordoc-local 이 떠 있으면 true. false 면 윤문 버튼을 숨기세요.

### POST api/run  (SSE)
- 요청(JSON, 파일은 base64):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `file_b64` | 문자열 | 예 | 녹음 파일 바이트의 base64(데이터 URL 접두어 `data:...;base64,` 는 빼고) |
| `file_name` | 문자열 | 아니오 | 원래 파일명. 확장자로 형식 판단: `.m4a .mp3 .wav .mp4 .webm .ogg .flac .aac .mov` |
| `speakers` | 정수 | 아니오 | 화자 수. 0 또는 생략 = 자동 |

- 응답: `Content-Type: text/event-stream`. 각 이벤트는 `data: <JSON>\n\n` 한 줄(이벤트 이름·id 없음).
  - 진행: `{"stage":"convert","msg":"ffmpeg 16kHz 변환"}` → `{"stage":"asr","msg":"전사 (large-v3)"}` → `{"stage":"diar","msg":"화자 분리"}`
  - 끝: `{"done": {...}}`
  - 실패: `{"error":"ValueError: 지원하지 않는 형식: ..."}` (스트림 안에서 옴, HTTP 상태는 200)
- `done` 내용:
```json
{"run_id": "2026-10-06-c359", "file": "00_meeting.mp3", "duration": 140.0, "ts": "2026-10-06T03:16:21",
 "n_speakers": 2, "status": "transcribed", "log": "[ffmpeg] 140.0s\n...",
 "utts": [{"start": 0.5, "end": 6.2, "speaker": "화자1", "text": "..."}],
 "speakers": {"화자1": {"name": "", "secs": 72.4, "n": 11,
              "samples": [{"start": 12.0, "end": 20.0, "text": "발췌 80자까지"}]}}}
```
  - `utts`: 발화(초 단위). `speakers`: 화자 라벨(`화자1`, `화자2`…, 모르면 `화자?`)별 발화 시간·횟수·대표 발췌 3개(클립 재생용 시작/끝).
- 메모: 음성 파일 하나 500MB 까지(서버 환경변수 `MAX_AUDIO_MB`). 요청 본문이 그 base64 크기를 넘으면 스트림 전에 `413 {"error":"파일이 너무 큽니다 (음성 500MB 이하)"}`(`v1/audio/transcriptions` 도 같은 상한). 본문 전체를 메모리로 읽음. 진행률(%)은 없고 단계 이름만 옵니다. ASR 단계 동안은 이벤트가 한동안 없을 수 있습니다.

### POST api/finalize/<run_id>  (SSE)
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `names` | 객체 | 아니오 | `{"화자1":"김부장","화자2":"이과장"}`. 빈 값·빠진 화자는 라벨 그대로 |
| `meta` | 객체 | 아니오 | `{"title":"…","date":"…","place":"…"}` 회의 정보(자유 문자열) |

- 응답(SSE, `api/run` 과 같은 형식):
  - `{"stage":"segment","msg":"주제 분할"}`
  - `{"stage":"insight","msg":"인사이트 1/3 — <구간 제목>"}` (구간마다)
  - `{"stage":"minutes","msg":"회의록 생성","reset":true}` — 화면은 이때 회의록 칸을 비움
  - `{"token":"…"}` — 회의록 Markdown 토큰 스트리밍(이어 붙이면 됨)
  - 끝: `{"done": {...}}` = 1단계 `done` + `"status":"done"`, `names`, `meta`, `hwpx`(파일명 또는 null), `minutes`(Markdown 문자열), `insights`
  - 실패: `{"error":"..."}`
- `insights` 항목: `{"title":"구간 제목","start":0.5,"end":310.2,"speakers":["김부장","이과장"],"insight":"텍스트"}`
- 메모: 같은 run 에 다시 부르면 덮어씁니다(이름 바꿔 재생성 가능). `hwpx` 는 서버에 kordoc-local 이 있을 때만 생성. 수 분 걸림.

### POST api/polish
- 요청(JSON): `{"run_id":"2026-10-06-c359","strength":"standard"}` — `strength`: `light` | `standard`(기본) | `strong`. 본문의 `text` 는 무시(저장된 `minutes.md` 를 윤문).
- 응답:
```json
{"output": "윤문한 회의록 Markdown", "units": 24, "strength": "standard", "model": "…",
 "changes": [{"n": 3, "before": "…", "after": "…", "ratio": 0.12}],
 "rejected": [{"n": 7, "before": "…", "after": "…", "reason": "…"}],
 "hwpx": "minutes.hwpx", "log": "…"}
```
  - `changes`: 바꾼 조각, `rejected`: 숫자·날짜·고유 표기가 달라져 원문을 유지한 조각. `minutes.md` 를 덮어쓰고(처음 원문은 `minutes_orig.md` 에 보관, 다운로드 API 는 없음) HWPX 를 다시 만듭니다.
- 오류: `400 {"error":"잘못된 실행 ID"}` / `400 {"error":"회의록이 아직 없습니다"}`, kordoc 실패 `502 {"error":"..."}`.

### POST v1/audio/transcriptions
- 요청: `multipart/form-data` 의 `file` 필드, 또는 JSON `{"file":"<base64>"}`. 다른 필드(model, language 등)는 무시.
- 응답: `{"text":"전사된 텍스트"}`. 화자 분리·저장 없음(임시 파일은 지움).
- 오류: `500 {"error":"ValueError: file 없음"}` 등(모두 500).

## 흐름 예
```js
const base = '/t/meeting-local/';
// SSE 를 POST 로 받기 (EventSource 는 GET 만 되므로 fetch 스트림을 직접 읽음)
async function sse(url, body, onEv) {
  const r = await fetch(base + url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '', done = null;
  for (;;) {
    const { value, done: end } = await rd.read(); if (end) break;
    buf += dec.decode(value, { stream: true });
    let i; while ((i = buf.indexOf('\n\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 2);
      if (!line.startsWith('data: ')) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.error) throw new Error(ev.error);
      if (ev.done) done = ev.done;
      onEv?.(ev);
    }
  }
  return done;
}
const b64 = f => new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.readAsDataURL(f); });

// 1) 전사 + 화자 분리
const run = await sse('api/run', { file_name: file.name, file_b64: await b64(file), speakers: 0 }, ev => ev.stage && log(ev.msg));
// 화자 목소리 확인: <audio src={base + `api/runs/${run.run_id}/clip?s=${s.start}&e=${s.end}`}>
// 2) 이름 지정 + 회의록
let md = '';
const fin = await sse(`api/finalize/${run.run_id}`, { names: { '화자1': '김부장' }, meta: { title: '주간회의' } },
  ev => { if (ev.reset) md = ''; if (ev.token) md += ev.token; });
// 3) 다운로드 링크: base + `api/runs/${fin.run_id}/minutes.md` (hwpx 는 fin.hwpx 가 있을 때만)
```

## 화면만의 것 / 주의
- 시간 표시(`mm:ss`), 단계 문구 이어 붙이기, 경과 초는 화면이 계산합니다.
- 윤문 결과의 어절 단위 비교 표시(바뀐 곳 보기)는 화면이 `changes[].before/after` 로 직접 계산합니다.
- 실행 삭제 API 는 없습니다. 정적 파일 경로(`/static/`)도 없습니다.
- 오류는 두 가지로 옵니다: 일반 응답은 `{"error":"..."}` + 상태 코드, SSE 는 200 스트림 안의 `{"error":"..."}` 이벤트.
- GET 경로는 쿼리를 떼지 않고 그대로 비교합니다. `api/runs?x=1` 처럼 쿼리를 붙이면 화면 HTML 이 돌아옵니다.
- POST 본문이 JSON 이 아니면(`api/run`, `api/finalize`) 서버가 응답 없이 연결을 끊습니다(코드에서 예외 처리 밖). 반드시 올바른 JSON 을 보내세요.
