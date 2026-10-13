# 음성 합성 API (`tts-local`)
한국어 텍스트를 로컬 MeloTTS 로 읽어 wav/mp3 로 돌려줍니다. OpenAI 호환 `v1/audio/speech` 도 있습니다.
포털 경로 접두어: `/t/tts-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/tts` → `https://<서버>/t/tts-local/api/tts`)

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- 포털이 `X-Agent-User` 헤더를 붙여 사용자별 폴더(`WORKSPACE/u/<id>`)에 저장합니다. 클라이언트가 이 헤더를 보내도 무시됩니다.
- 요청 본문은 `Content-Length` 가 있어야 합니다(청크 업로드 미지원).

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/voices` (= `v1/audio/voices`) | 화자 목록·기본 화자·엔진·장치 | 첫 호출 시 모델 로드(수십 초) | 아니오 |
| GET | `v1/models` | OpenAI 호환 모델 목록 | 아니오 | 아니오 |
| GET | `api/runs` | 내 합성 기록(최근 50개) | 아니오 | 예 |
| GET | `audio/<run_id>.<wav\|mp3>` | 저장된 오디오 받기 | 아니오 | 예 |
| POST | `api/tts` | 합성 + 저장, 메타 JSON 반환 | 예(문장 수에 비례, TTS 모델) | 예(저장) |
| POST | `v1/audio/speech` | OpenAI 호환: 오디오 바이트 그대로 반환(저장 안 함) | 예 | 아니오 |
| GET | 그 밖의 모든 경로 | 화면 HTML(`ui.html`) | — | — |

## 엔드포인트

### GET api/voices
- 응답:
```json
{"voices": ["KR"], "default": "KR", "engine": "melo", "device": "cpu"}
```
  - `voices`: 쓸 수 있는 화자 id, `default`: 서버 기본 화자(`TTS_DEFAULT_VOICE`), `device`: 모델이 올라간 곳(`cpu`/GPU 이름, 아직 안 올렸으면 "아직 안 올림").
- 메모: 이 호출이 모델을 메모리에 올립니다(처음 한 번 수십 초). 오래 안 쓰면 서버가 모델을 내립니다.

### GET v1/models
- 응답: `{"object":"list","data":[{"id":"melo","object":"model","owned_by":"local"}]}`

### GET api/runs
- 응답: 배열(최신순, 최대 50개)
```json
[{"run_id": "2026-10-07-6778", "text": "오늘의 주요 일정을 안내해 드립니다.", "voice": "KR", "speed": 1.0,
  "format": "mp3", "seconds": 4.22, "ts": "2026-10-07T09:33:40"}]
```
  - `run_id`: `YYYY-MM-DD-xxxx`(16진 4자리), `seconds`: 오디오 길이(초), `ts`: 만든 시각.

### GET audio/<run_id>.<wav|mp3>
- 응답: 오디오 바이트. `Content-Type: audio/wav` 또는 `audio/mpeg`. `Content-Disposition` 없음(파일명은 화면이 `<run_id>.<format>` 으로 붙임).
- 오류: 없으면 `404 {"error":"없음"}`. 확장자는 합성 때 고른 형식과 같아야 합니다.

### POST api/tts
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `text` (또는 `input`) | 문자열 | 예 | 읽을 텍스트. 줄바꿈·문장부호(. ! ? 。 … 등) 뒤 공백으로 문장을 나눠 차례로 합성 |
| `voice` | 문자열 | 아니오 | 화자 id. 기본 서버 기본값. 모르는 id 면 첫 화자 |
| `speed` | 숫자 | 아니오 | 0.5~2.0, 기본 1.0 |
| `format` (또는 `response_format`) | `wav`\|`mp3` | 아니오 | 기본 `wav`. mp3 는 서버에 ffmpeg 필요 |

- 응답: `api/runs` 항목 하나와 같은 메타 JSON. 오디오는 `audio/<run_id>.<format>` 로 받습니다.
- 오류(POST 공통, OpenAI 모양):
  - `400 {"error":{"message":"빈 입력","type":"invalid_request_error"}}` — 빈 텍스트, speed 범위 밖, 형식 오류, 발음할 글자 없음
  - `500 {"error":{"message":"RuntimeError: ...","type":"server_error"}}` — ffmpeg 없음 등
  - 없는 경로: `404 {"error":"없음"}`
- 메모: 스트리밍 없음. 합성이 끝날 때까지 응답을 붙잡습니다(서버 전역 잠금 — 동시에 요청하면 줄 섬). 짧은 문장 수 초, 첫 호출은 모델 로드로 수십 초 더.

### POST v1/audio/speech
- 요청: OpenAI 형식 `{"model":"melo","input":"안녕하세요","voice":"KR","response_format":"mp3","speed":1.0}` (`model` 은 무시). 필드 규칙은 `api/tts` 와 같음.
- 응답: 오디오 바이트(`audio/wav` 또는 `audio/mpeg`). 기록에 저장하지 않습니다.
- 오류: `api/tts` 와 같음.

## 흐름 예
화면이 하는 일: 화자 목록 → 합성 → 재생/다운로드 → 기록 갱신.
```js
const base = '/t/tts-local/';
const { voices, default: def } = await fetch(base + 'api/voices').then(r => r.json());
const r = await fetch(base + 'api/tts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: '안녕하세요. 반갑습니다.', voice: def, speed: 1.0, format: 'mp3' }) });
const m = await r.json();
if (m.error) throw new Error(m.error.message || m.error);
audioEl.src = base + `audio/${m.run_id}.${m.format}`;   // <audio> 재생, <a download> 로 받기
```
저장 없이 바로 바이트만 원하면:
```js
const blob = await fetch(base + 'v1/audio/speech', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ input: '안녕하세요', voice: 'KR', response_format: 'wav' }) }).then(r => r.blob());
```

## 화면만의 것 / 주의
- 진행률 API 없음. 화면은 "합성 중" 문구만 띄웁니다.
- 다운로드 파일명(`<run_id>.<format>`)은 화면이 `<a download>` 로 정합니다.
- 오류 모양이 둘입니다: GET 은 `{"error":"문자열"}`, POST 는 `{"error":{"message","type"}}`. 화면은 `j.error.message || j.error` 로 둘 다 처리합니다.
- 알 수 없는 GET 경로는 404 가 아니라 화면 HTML(200)을 돌려줍니다.
- 기록 삭제 API 는 없습니다. 정적 파일 경로(`/static/`)도 없습니다.
