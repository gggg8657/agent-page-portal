# 그림 생성 API (`image-local`)
말로 그리기(Qwen-Image)·말로 고치기(Qwen-Image-Edit) 서버. 포털 경로 접두어: `/t/image-local/` (아래 경로는 모두 이 뒤에 붙음 — 예: `/t/image-local/api/status`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/status` | GPU 작업 프로세스 상태 + 스타일·비율·품질 목록 | 아니오 | 아니오 |
| GET | `api/runs` | 내 그림 기록(끝난 것만, 최근 80개 폴더) | 아니오 | 예 |
| GET | `api/runs/<run_id>/<img_N.png \| src_N.png>` | 그림 파일(PNG). `?download=1` 이면 받기 | 아니오 | 예 |
| POST | `api/generate` | 글 → 그림 (SSE 스트림) | 예 — GPU, 선택적으로 LLM | 예 |
| POST | `api/edit` | 그림 + 지시 → 고친 그림 (SSE 스트림) | 예 — GPU | 예 |
| POST | `api/delete` | 그림 묶음 하나를 휴지통으로 | 아니오 | 예 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) HTML | — | — |

공통:
- 요청 본문은 모두 JSON(`Content-Type: application/json`). multipart 업로드는 없음 — 그림 업로드는 base64 로 JSON 에 넣는다.
- 사용자 구분: 포털이 붙이는 `X-Agent-User` 헤더 기준으로 작업 폴더가 나뉜다(`owner`·헤더 없음 = 서버 주인 폴더). 화면 쪽에서 따로 할 일은 없고 포털 로그인 쿠키만 있으면 됨.
- `run_id` 형식: `YYYY-MM-DD-xxxxxx`(16진 6자리). 그림 파일 이름은 결과 `img_0.png`~`img_3.png`, 고치기 원본 `src_0.png`~`src_2.png`.

## 엔드포인트

### GET api/status
- 요청: 없음
- 응답:
```json
{"worker": {"ok": true, "device": "아직 안 올림", "loaded": null, "mode": null},
 "styles": {"none": "그대로", "photo": "사진", "illust": "일러스트", "3d": "3D 렌더", "anime": "애니메",
            "watercolor": "수채화", "poster": "포스터·인포그래픽", "logo": "로고·아이콘", "product": "제품 사진"},
 "ratios": ["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"],
 "quality": {"fast": "빠르게", "normal": "보통", "high": "고품질"}}
```
  - `worker`: GPU 작업 프로세스가 꺼져 있으면 `null`(처음 그릴 때 자동으로 켜짐). 켜져 있으면 `device`(어느 GPU 인지 설명 문자열), `loaded`(`"generate"`·`"edit"`·`null` — 지금 GPU 에 올라 있는 모델), `mode`.
  - `styles`·`quality`: 키 → 화면 표시 이름. 요청에는 **키**를 보냄.
  - 비율별 실제 크기(서버 고정): 1:1=1328×1328, 4:3=1472×1104, 3:4=1104×1472, 16:9=1664×928, 9:16=928×1664, 3:2=1584×1056, 2:3=1056×1584.
  - 품질별 단계 수: fast 20 / normal 30 / high 50.

### GET api/runs
- 요청: 없음
- 응답: 메타 배열(최근 수정순, 그림이 다 그려진 묶음만)
```json
[{"run_id": "2026-10-07-24c0fb", "kind": "generate", "prompt": "고양이 우주비행사",
  "final_prompt": "An astronaut cat ...", "negative": "", "style": "illust", "ratio": "1:1",
  "quality": "normal", "seed": 123456789, "n": 2, "ts": "2026-10-07T10:12:00",
  "images": ["img_0.png", "img_1.png"]},
 {"run_id": "2026-10-07-a1b2c3", "kind": "edit", "prompt": "배경을 겨울 숲으로", "final_prompt": "배경을 겨울 숲으로",
  "negative": "", "quality": "normal", "seed": 42, "n": 1, "sources": ["src_0.png"], "ts": "...", "images": ["img_0.png"]}]
```
  - `kind`: `generate` | `edit`. `edit` 은 `style`·`ratio` 가 없고 `sources`(원본 파일 이름)가 있음.
  - `final_prompt`: 그림 모델에 실제로 보낸 문장(다듬기·스타일 꼬리 포함). 서버가 항상 붙이는 안전 네거티브는 메타에 들어가지 않음.

### GET api/runs/<run_id>/<파일>
- 파일 이름은 `img_<0-9>.png` 또는 `src_<0-9>.png` 만 허용.
- 응답: `image/png` 바이트. 쿼리에 `download` 가 있으면(`?download=1`) `Content-Disposition: attachment; filename*=UTF-8''<run_id>_<파일>` 를 붙임.
- 오류: 파일이 없으면 `404 {"error": "없음"}`. 형식이 안 맞는 경로는 404 가 아니라 화면 HTML 이 돌아옴(아래 주의).

### POST api/generate
- 요청 JSON:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `prompt` | string | 예 | 그릴 내용(한국어 가능). 그림에 넣을 글자는 따옴표로 감싸면 그대로 유지 |
| `style` | string | 아니오 | `api/status` 의 `styles` 키. 모르는 값이면 `none` |
| `ratio` | string | 아니오 | `api/status` 의 `ratios` 중 하나. 기본 `1:1` |
| `n` | int/string | 아니오 | 장수 1~4 (범위 밖은 잘림). 기본 1 |
| `quality` | string | 아니오 | `fast`·`normal`·`high`. 기본 `normal` |
| `seed` | int/string | 아니오 | 정수면 고정, 비어 있으면 무작위. 같은 seed·설정이면 같은 그림 |
| `negative` | string | 아니오 | 빼고 싶은 것(서버가 안전 네거티브를 항상 덧붙임) |
| `enhance` | bool | 아니오 | 기본 `true`. LLM 으로 영어 그림 프롬프트로 다듬음. LLM 실패 시 원문 그대로 진행 |

- 응답: **`text/event-stream`**, 상태 코드는 항상 200. 각 이벤트는 `data: <JSON>\n\n`. 이벤트 종류(순서대로 나올 수 있는 것):

| 이벤트 | 예 | 뜻 |
|---|---|---|
| 단계 메시지 | `{"stage":"prompt","msg":"요청을 그림 프롬프트로 다듬는 중"}` | `stage`: `worker`(GPU 프로세스 켜는 중)·`prompt`·`draw`·`load` |
| 그리기 시작 | `{"stage":"draw","msg":"그리는 중 (2장, 보통)","run_id":"…","final_prompt":"…"}` | 여기서 `run_id`·`final_prompt` 확정 |
| 진행 | `{"step":12,"total":30,"image":0,"run_id":"…"}` | `image` 는 0부터 몇 번째 장인지. 전체 진행률 = `(image + step/total) / n` |
| 한 장 저장 | `{"saved":0,"path":"img_0.png","run_id":"…"}` | `api/runs/<run_id>/<path>` 로 바로 보여 줄 수 있음. `path` 는 파일 이름만(서버 경로 없음) |
| 완료 | `{"done": {…메타…}}` | `api/runs` 항목과 같은 모양(`images` 포함) |
| 오류 | `{"error":"ValueError: 무엇을 그릴지 써 주세요"}` | 스트림 끝. 실패한 묶음 폴더는 지워짐 |

- 메모: 보통 품질 한 장 약 25초. GPU 에 모델이 안 올라 있으면(처음·10분 이상 안 씀·고치기 모델이 올라 있음) 30초~1분 더. GPU 가 부족하면 느린 방식(장당 약 1분). 서버 쪽 시간 제한은 2시간. GPU 메모리 부족이면 `error` 이벤트로 안내 문구가 옴.

### POST api/edit
- 요청 JSON:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `instruction` | string | 예 | 어떻게 고칠지(한국어·영어 그대로 모델에 들어감, LLM 다듬기 없음) |
| `refs` | string[] | 둘 중 하나 이상 | 갤러리 그림: `"<run_id>/img_0.png"` 또는 `"<run_id>/src_0.png"` |
| `uploads` | {name, b64}[] | 둘 중 하나 이상 | 새 그림: `name` 은 `.png`·`.jpg`·`.jpeg`·`.webp` 로 끝나야 함, `b64` 는 data URL 머리 없는 순수 base64 |
| `n` | int/string | 아니오 | 1~4 |
| `quality` | string | 아니오 | `fast`·`normal`·`high` |
| `seed` | int/string | 아니오 | 위와 같음 |
| `negative` | string | 아니오 | 위와 같음 |

- 원본은 `refs` 먼저, 그다음 `uploads` 순서로 합쳐 **앞의 3장까지만** 쓴다(그 뒤는 조용히 버림). 여러 장이면 "1번 사람에게 2번 옷을" 같은 지시 가능.
- 응답: `api/generate` 와 같은 SSE 형식(`final_prompt` 이벤트·`prompt` 단계는 없음). 완료 메타의 `sources` 로 원본 사본(`src_N.png`)을 볼 수 있음. 결과 크기는 약 100만 화소.
- 오류(SSE `error` 이벤트): `잘못된 그림 경로`·`없는 그림`(refs 문제), `그림 파일(png·jpg·webp)만`, `고칠 그림을 올리거나 갤러리에서 고르세요`, `어떻게 고칠지 써 주세요`.
- 메모: 업로드 크기 제한은 코드에 없음(JSON 본문 전체를 한 번에 읽음). 올린 파일은 임시 저장 후 작업이 끝나면 지워지고, 묶음 폴더에 `src_N.png` 사본만 남음.

### POST api/delete
- 요청: `{"run_id": "2026-10-07-24c0fb"}`
- 응답: `{"ok": true}` — 폴더를 지우지 않고 작업 폴더의 `.trash/` 로 옮김(관리자가 되살릴 수 있음).
- 오류: `400 {"error": "잘못된 ID"}`(형식 틀림), `400 {"error": "없는 그림"}`.

## 흐름 예

### 1) 그리고 진행률 보여 주기
```js
const BASE = '/t/image-local/';
const r = await fetch(BASE + 'api/generate', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ prompt: '안전 캠페인 포스터, 제목 "안전 먼저"', style: 'poster', ratio: '3:4', n: 2, quality: 'normal', enhance: true })
});
const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '', n = 2;
for (;;) {
  const { value, done } = await reader.read(); if (done) break;
  buf += dec.decode(value, { stream: true });
  let i; while ((i = buf.indexOf('\n\n')) >= 0) {
    const line = buf.slice(0, i); buf = buf.slice(i + 2);
    if (!line.startsWith('data: ')) continue;
    const ev = JSON.parse(line.slice(6));
    if (ev.error) throw new Error(ev.error);
    if (ev.step) setProgress(((ev.image || 0) + ev.step / ev.total) / n);
    if (ev.saved !== undefined) showImage(`${BASE}api/runs/${ev.run_id}/img_${ev.saved}.png`);
    if (ev.done) ev.done.images.forEach(f => showImage(`${BASE}api/runs/${ev.done.run_id}/${f}`));
  }
}
```
POST 라서 `EventSource` 는 못 쓰고 위처럼 `fetch` 스트림을 직접 읽는다.

### 2) 갤러리 그림을 고치기
```js
// 갤러리에서 고른 그림 + 내 PC 파일 하나
const file = input.files[0];
const b64 = (await new Promise(ok => { const rd = new FileReader(); rd.onload = () => ok(rd.result); rd.readAsDataURL(file); })).split(',')[1];
await fetch(BASE + 'api/edit', { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ instruction: '1번 사람에게 2번 옷을 입혀 줘', refs: ['2026-10-07-24c0fb/img_0.png'],
                         uploads: [{ name: file.name, b64 }], n: 1, quality: 'normal' }) });
// 응답 읽기는 1) 과 같음
```

## 화면만의 것 / 주의
- 진행률 계산, "같은 설정으로 다시"(메타의 `prompt`·`style`·`ratio`·`quality`·`negative` 를 입력칸에 되채움), 원본 보기 버튼은 화면이 메타로 직접 만든다 — 별도 API 없음.
- 화면은 고치기 원본을 3장까지만 받고 png·jpeg·webp 만 거른다(서버도 3장 제한·확장자 검사).
- `api/runs/…` 에서 파일 이름 형식이 틀린 경로, 그리고 모르는 GET 경로는 404 가 아니라 화면 HTML(200)이 돌아온다. 응답 `Content-Type` 으로 구분할 것.
- `api/generate`·`api/edit` 는 실패해도 HTTP 200 이고 오류는 스트림 안 `error` 이벤트로만 온다.
- 모르는 POST 경로는 `404 {"error": "not found"}`. 본문이 JSON 객체가 아니면 `400 {"error": "잘못된 요청"}`.
- 진행 이벤트·`error` 문구에는 서버 폴더 경로가 들어가지 않는다(`saved` 의 `path` 는 파일 이름만).
- 그림 모델과 고치기 모델은 GPU 에 하나만 올라가므로 번갈아 부르면 바꿀 때마다 30초~1분 더 걸린다. 동시에 여러 요청이 오면 GPU 작업 프로세스가 잠금으로 한 번에 하나씩 처리한다 — 기다리는 동안은 `draw` 단계 메시지 뒤로 이벤트가 오지 않는다(대기 순번 알림 없음).
- 공유 데이터 없음, owner 전용 API 없음. 정적 파일 경로(`/static/…`) 없음 — 화면은 HTML 한 장.
