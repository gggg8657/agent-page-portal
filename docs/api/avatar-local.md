# 아바타 API (`avatar-local`)
사진 + 대사 → 립싱크 mp4(SadTalker), 그리고 "가상 캐릭터 스튜디오"(말로 묘사 → 그림 후보 → 말하기/움직이기 영상). 목소리는 tts-local 만 씁니다(목소리 복제 없음).
포털 경로 접두어: `/t/avatar-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/run` → `https://<서버>/t/avatar-local/api/run`)

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- 포털이 `X-Agent-User` 를 붙여 사용자별 폴더(`WORKSPACE/u/<id>`)에 저장합니다. 클라이언트가 보낸 값은 무시됩니다.
- POST 는 `Content-Length` 와 올바른 JSON 본문이 있어야 합니다(깨진 JSON 은 `400 {"error":"잘못된 요청(JSON)"}`, JSON 객체가 아닌 본문(배열·문자열 등)은 `400 {"error":"요청 본문은 JSON 객체여야 합니다"}` — 모든 POST 경로 공통. 빈 본문은 `{}` 로 봄). 본문 상한 약 28MB(넘으면 413). 포털 프록시 제한 시간 1시간.
- `run_id` 형식: `YYYY-MM-DD-xxxx` (16진 4자리).
- 규칙: 사진 업로드(`api/run`)는 본인 또는 동의한 사람의 얼굴만 — `consent: true` 가 없으면 거절합니다. 스튜디오는 생성 캐릭터만 다루므로 사진 업로드가 없습니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/status` | 엔진 준비 상태 | 아니오 | 아니오 |
| GET | `api/studio` | 스튜디오 선택지(스타일·구도) | 아니오 | 아니오 |
| GET | `api/runs` | 갤러리(완성된 영상 목록, 최근 50) | 아니오 | 예 |
| GET | `api/runs/<run_id>/<파일>` | 영상·후보 그림 받기 | 아니오 | 예 |
| POST | `api/run` | 사진 + 대사 → 립싱크 영상 (SSE) | 예(TTS + SadTalker, 수십 초~수 분) | 예(저장) |
| POST | `api/imagine` | 묘사 → 그림 후보 1~6장 (SSE) | 예(LLM + 이미지 모델 GPU, 1~수 분) | 예(저장) |
| POST | `api/speak` | 고른 그림이 대사 말하기 (SSE) | 예(최고 품질: 5초당 약 25분 / 빠르게: 수십 초) | 예(저장) |
| POST | `api/move` | 고른 그림이 동작하는 5초 영상 (SSE) | 예(최고 품질 약 30~35분 / 빠르게 약 7분) | 예(저장) |
| POST | `api/runs/delete` | 항목 휴지통으로 | 아니오 | 예(이동) |
| DELETE | `api/runs/<run_id>` | 항목 하나 휴지통으로 | 아니오 | 예(이동) |
| POST | `api/runs/restore` | 휴지통 묶음 되살리기 | 아니오 | 예 |
| POST | `api/to_persona` | (숨긴 기능) 고른 그림을 persona 캐릭터로 | 예(LLM) | 예 |
| GET | 그 밖의 경로 | 화면 HTML | — | — |

## 엔드포인트

### GET api/status
- 응답: `{"sadtalker": true, "tts_server": true, "device": "cuda", "s2v": true}`
  - `sadtalker`: 립싱크 가중치·코드 있음, `tts_server`: tts-local 응답함(없으면 영상 생성 불가), `device`: `cpu`|`cuda`, `s2v`: 최고 품질 말하기(Wan2.2) 코드 있음.

### GET api/studio
- 응답: `{"styles": {"real":"실사","virtual":"버추얼 휴먼","3d":"3D 애니메이션","anime":"애니메","webtoon":"웹툰","illust":"일러스트"}, "shots": {"face":"얼굴","half":"상반신","full":"전신"}}` — 키를 `api/imagine` 의 `style`·`shot` 에 넣습니다.

### GET api/runs
- 응답: 배열(최신순, 최대 50). 항목은 두 종류(각 항목의 `meta.json` 그대로):
  - 사진 립싱크: `{"run_id","text","engine":"tts-local","device","ts"}` → 영상 `final.mp4`
  - 가상 캐릭터(`"kind":"character"`): `{"run_id","kind","desc","style","shot","prompt","negative","n","ts","candidates":["cand_0.png",…],"picked":0, "talk":{"text","engine","model"}?, "move":{"action","prompt","text","quality","model"}?}` → `talk` 가 있으면 `talk.mp4`, `move` 가 있으면 `move.mp4`
- 메모: 그림만 그리고 말하기/움직이기를 안 한 캐릭터는 목록에 안 나옵니다.

### GET api/runs/<run_id>/<파일>
- `<파일>`: `final.mp4` | `talk.mp4` | `move.mp4` | `cand_<0-9>.png`
- 응답: `video/mp4` 또는 `image/png`. `Content-Disposition` 없음. Range(부분 요청) 미지원 — 파일 전체를 한 번에 보냅니다.
- 오류: `404 {"error":"삭제됐거나 없는 파일입니다"}`.
- 메모: 같은 이름으로 다시 만들어지므로 화면은 `?<시각>` 을 붙여 캐시를 피합니다(쿼리는 무시됨).

### POST api/run  (SSE)
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `photo_b64` | 문자열 | 예 | 얼굴 사진 base64(데이터 URL 접두어 제외) |
| `photo_name` | 문자열 | 예 | 파일명. `.jpg` `.jpeg` `.png` 만 |
| `text` | 문자열 | 예 | 대사 |
| `consent` | 불리언 | 예 | 본인/동의 확인. false 면 오류 |

- 응답(SSE, `data: <JSON>\n\n`):
  - `{"stage":"tts","msg":"목소리 합성 (TTS 도구)"}` → `{"stage":"lipsync","msg":"립싱크 영상 생성 (CPU 는 수 분)"}`
  - `{"log":"…"}` — SadTalker 진행 줄(퍼센트 등, 120자까지)
  - 끝: `{"done":{"run_id":"…","text":"…","engine":"tts-local","device":"cuda","ts":"…"}}` → 영상 `api/runs/<run_id>/final.mp4`
  - 실패: `{"error":"ValueError: 본인(또는 동의한 사람)의 얼굴임을 확인해야 합니다"}` / `"RuntimeError: 사진에서 얼굴을 찾지 못했습니다…"` / `"RuntimeError: TTS 도구(tts-local)를 켜 주세요…"` 등
- 메모: 사진은 20MB 까지(요청 본문이 그 base64 크기 + 1MB 를 넘으면 스트림 전에 `413 {"error":"요청이 너무 큽니다 (사진 20MB 이하)"}`). 업로드 원본은 끝나면 지우고 `photo.<확장자>` 로 실행 폴더에 복사본을 남깁니다.

### POST api/imagine  (SSE)
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `desc` | 문자열 | 예 | 캐릭터 묘사(한국어 가능). 실존 인물이 아닌 성인 가상 인물로 그림 |
| `style` | 문자열 | 아니오 | `api/studio` 의 styles 키. 기본 `real` |
| `shot` | 문자열 | 아니오 | `face`·`half`·`full`. 기본 `half` |
| `n` | 정수 | 아니오 | 후보 수 1~6, 기본 4 |

- 응답(SSE):
  - `{"stage":"prompt","msg":"묘사를 그림 프롬프트로"}`, `{"stage":"draw","msg":"…"}`, 처음이면 `{"stage":"worker",…}`·`{"stage":"load",…}`
  - `{"image": 0, "run_id": "…"}` — 후보 한 장이 나올 때마다(번호 0부터). 바로 `api/runs/<run_id>/cand_<번호>.png` 로 보여줄 수 있음
  - 끝: `{"done": {...}}` = 캐릭터 meta(`run_id`, `kind`, `desc`, `style`, `shot`, `prompt`, `negative`, `n`, `ts`, `candidates`)
  - 실패: `{"error":"…"}` (실패하면 그 실행 폴더를 지웁니다)

### POST api/speak  (SSE)
- 요청(JSON): `run_id`(imagine 결과, 필수), `idx`(고른 후보 번호, 기본 0), `text`(대사, 필수), `quality`: `best`(기본, Wan2.2-S2V — 얼굴·상반신, 대사 5초마다 약 25분, GPU 약 64GB 여유 필요) | `fast`(SadTalker — 얼굴만, 수십 초)
- 응답(SSE): `{"stage":"tts"|"lipsync"|"s2v",…}`, `{"log":"…"}`, 최고 품질은 `{"step": 12, "total": 40, "clip": 1}`(5초 조각 `clip` 번째의 확산 단계), 끝 `{"done":{"run_id":"…","video":"talk.mp4","engine":"tts-local","model":"Wan2.2-S2V-14B"}}`
- 오류(스트림 안): `"ValueError: 가상 캐릭터가 아닙니다"`, `"ValueError: 대사가 비었습니다"`, `"RuntimeError: GPU 여유 메모리 부족 …"` 등.
- 메모: 다시 부르면 `talk.mp4` 를 덮어씁니다. 연결을 끊으면 서버가 생성 프로세스를 멈춥니다(최고 품질).

### POST api/move  (SSE)
- 요청(JSON): `run_id`(필수), `idx`(기본 0), `action`(어떻게 움직일지, 필수), `text`(선택 — 있으면 음성을 입힘, 입모양 동기화는 아님), `quality`: `best`(720p급, 40단계) | `fast`(480p급, 30단계)
- 응답(SSE): `{"stage":"prompt"|"video"|"tts",…}`, `{"step": i, "total": n}`(진행 단계), 끝 `{"done":{"run_id":"…","video":"move.mp4"}}`
- 메모: 결과는 16fps 5초. 대사가 영상보다 길면 영상을 반복해 대사 길이에 맞춥니다.

### POST api/runs/delete
- 요청(JSON): `{"run_ids": ["2026-10-07-c17d", …]}` (최대 200) 또는 `{"run_id": "…"}`
- 응답: `{"deleted": [...], "missing": [...], "batch": "20261013-101500-ab12"}` — `batch` 는 되살리기용(아무것도 안 지웠으면 null).
- 오류: 형식이 틀린 id 가 하나라도 있으면 아무것도 안 옮기고 `400 {"error":"잘못된 항목입니다"}`. 하나도 못 지우면(모두 없음) `404` + 같은 모양 본문.
- 메모: 폴더째(영상·음성·그림·meta) `<사용자 폴더>/.trash/<batch>/` 로 옮김. 완전 삭제 API 는 없습니다.

### DELETE api/runs/<run_id>
- `api/runs/delete` 에 id 하나를 넣은 것과 같습니다. (지금 화면은 POST 쪽을 씀)

### POST api/runs/restore
- 요청: `{"batch": "20261013-101500-ab12"}`
- 응답: `{"restored": ["…"]}` — 같은 이름이 이미 있으면 건너뜀.
- 오류: `400 {"error":"잘못된 묶음입니다"}` / `400 {"error":"휴지통에 없습니다"}`.

### POST api/to_persona  (숨긴 기능)
- 숨긴 도구(persona)로 캐릭터를 넘기는 연동입니다. 요청 `run_id`, `idx`, `name`, `personality` → persona 쪽 응답 JSON 그대로. 실패 `400 {"error":"…"}`. 새 화면에서는 쓰지 않아도 됩니다.

## 흐름 예
SSE 읽기 함수는 [meeting-local 문서](meeting-local.md#흐름-예)의 `sse()` 와 같은 방식(POST + `fetch` 스트림, `data: ` 줄, `\n\n` 구분).
```js
const base = '/t/avatar-local/';
// A) 사진 립싱크
const b64 = f => new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result.split(',')[1]); r.readAsDataURL(f); });
const done = await sse(base + 'api/run', { consent: true, text: '안녕하세요', photo_name: f.name, photo_b64: await b64(f) },
  ev => { if (ev.stage) show(ev.msg); if (ev.log) appendLog(ev.log); });
video.src = base + `api/runs/${done.run_id}/final.mp4`;

// B) 스튜디오: 그림 후보 → 하나 골라 말하기
const ch = await sse(base + 'api/imagine', { desc: '30대 연구원, 단정한 셔츠', style: 'real', shot: 'half', n: 4 },
  ev => { if (ev.image !== undefined) imgs[ev.image].src = base + `api/runs/${ev.run_id}/cand_${ev.image}.png?${Date.now()}`; });
const r = await sse(base + 'api/speak', { run_id: ch.run_id, idx: 2, text: '반갑습니다', quality: 'fast' },
  ev => ev.step && show(`${ev.step}/${ev.total}`));
video.src = base + `api/runs/${r.run_id}/${r.video}?${Date.now()}`;
```

## 화면만의 것 / 주의
- 경과 시간, "N번째 5초 조각" 문구, 후보 선택(`idx`) 상태는 화면이 관리합니다. 작업 id 로 폴링하는 API 는 없습니다 — 진행은 SSE 연결로만 받으므로 연결을 유지해야 합니다.
- 갤러리 다중 선택, "되살리기" 링크(마지막 `batch` 기억)는 화면 기능입니다.
- 실행 하나의 meta 만 따로 주는 API 는 없습니다(`api/runs` 목록에서 찾기). 그림만 그린 캐릭터는 목록에 안 나오므로 `imagine` 의 `done` 을 화면이 들고 있어야 합니다.
- `/static/` 정적 파일 경로는 없습니다.
- 오류는 두 가지: 일반 응답 `{"error":"..."}` + 상태 코드, SSE 는 200 스트림 안의 `{"error":"..."}` 이벤트.
