# 파동이 스튜디오 API (`padong-local`)
KAERI 캐릭터 '파동이' 3D·2D 리그. 말(자연어)을 로컬 LLM 이 **동작 순서(steps)** 로 바꿔 주고, 바깥 프로그램이 명령을 넣으면 열려 있는 화면이 받아 실행합니다. 캐릭터를 그리고 움직이는 일은 전부 브라우저(ui.html)가 하고, 서버는 LLM·TTS 중계와 작은 명령 큐만 맡습니다.

포털 경로 접두어: `/t/padong-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/info` → `<서버>/t/padong-local/api/info`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `` (빈 경로) / `index.html` | 스튜디오 화면(ui.html) | - | - |
| GET | `v<버전>` (예 `v0.1`) | 예전 버전 화면 | - | - |
| GET | `api/info` | 동작·장면·표정 목록, 모델 이름 | - | - |
| GET | `api/versions` | 볼 수 있는 예전 버전 목록 | - | - |
| GET | `api/plugins` | 플러그인 JS 파일 목록 | - | - |
| GET | `api/weather` | 현재 날씨(대전 유성) | 바깥 API, 20분 캐시 | - |
| GET | `api/gpu` | 서버 GPU 사용률·온도 | 30초 캐시 | - |
| GET | `api/poll?since=<id>` | 바깥에서 넣은 명령 가져오기 | - | 예 (사용자별 큐) |
| GET | `static/<파일>` | 화면용 JS(three.js·플러그인·펫 스크립트) | - | - |
| GET | `ref/<파일>` | 화면 '원본 자료' 창의 그림 — **관리자(owner)만**, 다른 사용자는 403(정해진 31개 이름만, 그 밖은 404) | - | - |
| POST | `api/command` | 자연어 → steps | LLM, 약 5~15초 | - |
| POST | `api/pet` | 포털 펫의 대답·혼잣말 → steps (+도구 추천) | LLM | - (상태는 클라이언트가 보냄) |
| POST | `api/push` | 명령을 큐에 넣기(열린 화면이 실행) | `text` 면 LLM | 예 (사용자별 큐) |
| POST | `api/tts` | 대사 → WAV 음성 | TTS 도구(tts-local) 중계 | - |

## 공통
- 응답은 모두 `application/json; ensure_ascii=False`(한글 그대로), `Cache-Control: no-store`. 단 `static/` 의 three.js 계열 파일은 `max-age=86400`.
- 요청 본문은 JSON(`Content-Type: application/json`). 본문이 비면 `{}` 로 봅니다.
- 오류: 없는 경로 `404 {"error":"not found"}`. POST 본문이 JSON 이 아니거나 객체가 아니면 `400 {"error":"잘못된 요청 (…)"}`, 필드 형식이 틀리면(`text`·`voice` 가 문자열 아님, `history` 가 객체 배열 아님, `speed`·`context.hour`·`context.aff` 가 숫자 아님 등) `400 {"error":"잘못된 요청: <사유>"}`. LLM·TTS 쪽 실패만 `502 {"error":"<예외이름>: <메시지>"}`.
- 사용자 구분: 포털이 붙여 주는 `X-Agent-User` 헤더(없으면 `owner`). `api/push`·`api/poll` 큐만 이 값으로 나뉩니다. 큐는 메모리에만 있어 서버를 다시 켜면 비워집니다.
- 인증·권한: 포털 로그인을 통과했으면 대부분의 엔드포인트를 쓸 수 있습니다. 예외로 `ref/<파일>`(원본 자료)은 관리자(owner)만 — 다른 사용자는 403.

### steps 형식 (여러 엔드포인트가 공통으로 주고받음)
```json
{"steps": [
  {"do": "jump", "times": 2},
  {"do": "wave", "expr": "happy", "say": "안녕하세요!", "times": 1},
  {"play": "curtain", "times": 1}
]}
```
| 필드 | 형식 | 설명 |
|---|---|---|
| `do` | 문자열 | 동작 이름. `api/info` 의 `actions` 키 + 플러그인 동작 중 하나 |
| `play` | 문자열 | 여러 단계 장면. `do` 대신 씀. steps 전체에서 **하나만** 허용(두 번째부터는 버려짐) |
| `expr` | 문자열 | 표정. `api/info` 의 `exprs` 중 하나 |
| `say` | 문자열 | 소리 내어 말할 대사(최대 200자로 자름) |
| `times` | 정수 | 반복 1~5 (범위 밖이면 잘림, 없으면 1) |

서버는 들어온/LLM 이 만든 steps 를 정리합니다(clean_steps): 목록에 없는 `do`/`play`/`expr` 는 버리고, `do`·`say`·`play` 가 하나도 없는 단계는 빼고, 최대 8단계.

## 엔드포인트

### GET api/info
- 응답:
```json
{"actions": {"idle": "가만히 숨쉬기", "wave": "손 흔들기(Hello!)", "...": "..."},
 "plays": {"curtain": "커튼 뒤로 사라졌다 짠 하고 다시 등장", "nap": "...", "hwacance": "...", "exit_return": "...", "minimi": "..."},
 "exprs": ["smile", "basic", "happy", "wink", "excited", "closed", "sad", "surprised", "angry", "curious", "love", "sleepy", "shock", "focus", "workout"],
 "model": "<LLM 모델 이름>"}
```
- 메모: `actions`/`plays` 는 **기본 목록만**입니다. 플러그인이 더하는 동작·장면은 여기 없지만 서버의 steps 검사·LLM 프롬프트에는 들어갑니다(아래 `api/plugins`). 포털 펫은 이 주소가 응답하는지로 "도구가 켜져 있는지"를 판단합니다. 내부 TTS 서버 주소는 주지 않습니다.

### GET api/versions
- 응답: `{"versions": ["0.0", "0.1", "0.2", ...]}` — 각 값 `v` 에 대해 `GET v<v>` 로 그 버전 화면이 열립니다(없으면 404).

### GET api/plugins
- 응답: `{"files": ["behav_coffee.js", "outfit_xxx.js", ...]}` — `static/` 안의 `outfit_*.js`·`behav_*.js` 파일 이름. 화면은 이 파일들을 `static/<이름>` 에서 ES 모듈로 불러옵니다. 각 파일 맨 앞 `export const META = {...}` 의 `plays`·`acts`·`outfits` 를 서버가 읽어 허용 동작·장면 목록에 더합니다(파일이 바뀌면 자동 반영).

### GET api/weather
- 응답: `{"ok": true, "temp": 19.5, "rain": false, "snow": false, "code": 1}`
  - `temp` 기온(℃), `rain`/`snow` 비·눈 여부, `code` WMO 날씨 코드. 공개 날씨 API(Open-Meteo)에 고정 좌표만 보내 받으며 20분 캐시.
- 실패: `200 {"ok": false, "error": "..."}` (상태 코드는 200 그대로, 실패 결과도 캐시 시간 동안 유지).

### GET api/gpu
- 응답: `{"ok": true, "gpus": [{"i": 0, "util": 0, "temp": 47, "mem": 1267, "memTotal": 95830}], "hot": [], "idle": true}`
  - `util` %, `temp` ℃, `mem`/`memTotal` MiB. `hot` = 사용률 ≥90% 또는 온도 ≥80℃ 인 GPU, `idle` = 모두 사용률 <5%. 30초 캐시.
- 실패: `200 {"ok": false, "error": "..."}`

### GET api/poll
- 쿼리: `since`(정수, 생략 시 0) — 마지막으로 받은 명령 id.
- 응답: `{"items": [{"id": 1760000000001, "steps": [...], "text": "원래 문장(있을 때만)"}], "last": 1760000000001}`
  - `items` = `id > since` 인 명령, `last` = 큐의 마지막 id(큐가 비면 `since` 그대로).
- 메모: 큐는 사용자당 최근 50개만 보관. 화면은 처음에 `since=0` 으로 `last` 만 받아 두고(예전 명령은 실행하지 않음) 1.2초마다 폴링합니다. 펫 화면(`?pet=1`)은 폴링하지 않습니다.

### POST api/command
- 요청:
| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `text` | 문자열 | 예 | 시킬 말. 예 `점프 두 번 하고 손 흔들며 반갑다고 해` |
| `history` | 배열 | 아니오 | 이전 대화 `[{"role":"user"|"assistant","content":"..."}]`. 최근 8개, 각 800자까지 씀 |
- 응답: `{"steps": [...]}` (위 steps 형식, 0~8개)
- 오류: `text` 가 비면 `400 {"error":"빈 명령"}`, LLM 실패 `502`.
- 메모: 스트리밍 없음, 한 번에 응답(LLM 제한시간 120초, 보통 5~15초). 지금 화면은 assistant 기록으로 `JSON.stringify({steps})` 를 넣습니다. LLM 이 실패하거나 빈 steps 를 주면 화면이 **브라우저 쪽 낱말 규칙**으로 대신 동작을 만듭니다(서버에는 없음).

### POST api/pet
포털·모든 도구 화면 아래를 돌아다니는 펫(`static/pet.js`)이 쓰는 대화 API.
- 요청:
| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `event` | 문자열 | 아니오 | `chat`(말 걸기, 기본) · `idle`(혼잣말) · `greet`(하루 첫 인사) · `wake`(졸다 깸) |
| `text` | 문자열 | 아니오 | 사용자 말(300자까지). 없으면 `(<event>)` 로 보냄 |
| `history` | 배열 | 아니오 | `api/command` 와 같은 형식 |
| `context` | 객체 | 아니오 | `dir`(지금 보는 도구 dir, 기본 `portal`), `title`(페이지 제목), `name`(펫 이름, 20자), `aff`(친밀도 정수), `hour`(0~23), `occasion`(기념일 이름), `outfit`(입은 옷 이름), `speech`(옷에 맞는 말투 설명) |
- 응답:
```json
{"steps": [{"do": "point", "say": "회의록 도구 버튼 눌러 봐요!", "times": 1}],
 "open": [{"dir": "<도구 dir>", "name": "<도구 이름>", "desc": "<설명>", "icon": "🎙️", "url": "/t/<도구 dir>/"}]}
```
  - `open`: `event=chat` 일 때만, 사용자가 하려는 일에 맞는 포털 도구 후보(최대 3개, 지금 보는 도구는 제외). 아니면 `[]`. 대사에 dir 이 섞이면 서버가 도구 이름으로 바꿉니다.
  - LLM 이 꺼져 있고 `chat` 이면 낱말 매칭으로 도구를 찾아 `{"steps":[...], "open":[...], "offline": true}` 를 줍니다. 찾은 게 없으면 `502`.
- 메모: 포털 도구 목록은 포털 `/api/tools` 를 60초 캐시(숨긴 도구 제외). 펫 상태(이름·친밀도·대화 기록)는 서버에 저장하지 않으며 클라이언트가 매번 보냅니다.

### POST api/push
바깥 프로그램 → 열린 화면. 넣은 사람(`X-Agent-User`)의 화면만 움직입니다.
- 요청: 둘 중 하나
  - `{"steps": [...]}` — 그대로(정리만 해서) 큐에
  - `{"text": "춤추고 고맙다고 해"}` — 서버가 LLM 으로 steps 를 만들어 큐에(대화 기록 없이). 원문도 `text` 로 같이 저장
- 응답: `{"id": 1760000000001}`
- 오류: 둘 다 없으면 `400 {"error":"steps 또는 text 필요"}`, LLM 실패 `502`.
- 메모: 화면이 안 열려 있어도 큐에는 쌓입니다(최근 50개). 단 화면은 열릴 때 그 전 명령을 건너뛰므로, 열려 있는 동안 넣은 것만 실행됩니다.

### POST api/tts
- 요청: `{"text": "안녕하세요", "voice": "KR", "speed": 1.1}` — `text` 300자까지, `voice` 기본 `KR`, `speed` 기본 1.1.
- 응답: WAV 바이트(`Content-Type` 은 TTS 도구가 준 값, 보통 `audio/wav`). 파일 이름 헤더 없음.
- 오류: TTS 도구가 꺼져 있으면 `502 {"error":"URLError: ..."}`.
- 메모: 포털의 TTS 도구(OpenAI 호환 `/v1/audio/speech`)로 넘기기만 합니다.

### GET 화면 · 정적 파일
- `` / `index.html`: 스튜디오. 쿼리 `?pet=1`(펫 모드: 무대만, 바깥 페이지와 postMessage), `mode=2d|3d|c2d`(펫 보기), `date=YYYY-MM-DD`(그 날짜로 기념일 옷 시험), `outfit=auto|none|<옷 id>`.
- `v<버전>`: 예전 화면(`api/versions` 참고).
- `static/<파일>`: `[\w.-]+` 이름만. `.js` → `text/javascript`, `.png`/`.jpg`/`.gif` 는 이미지, 그 밖은 `text/plain`. 하위 폴더 없음.

## 흐름 예

### 1) 말로 시키고 화면에서 움직이기 (스튜디오 화면이 하는 일)
```js
const history = [];
const r = await fetch('/t/padong-local/api/command', {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: '점프하고 손 흔들며 반갑다고 해', history }) });
const j = await r.json();
if (!r.ok) throw new Error(j.error);
history.push({ role: 'user', content: '점프하고 손 흔들며 반갑다고 해' },
             { role: 'assistant', content: JSON.stringify({ steps: j.steps }) });
for (const s of j.steps) {
  // 여기서 내 캐릭터로 s.do / s.play / s.expr 를 times 번 재생
  if (s.say) {
    const wav = await fetch('/t/padong-local/api/tts', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: s.say }) });
    if (wav.ok) new Audio(URL.createObjectURL(await wav.blob())).play();
  }
}
```

### 2) 바깥에서 명령 넣고, 화면이 폴링해서 실행
```bash
curl -XPOST <서버>/t/padong-local/api/push -H 'Content-Type: application/json' \
  -d '{"steps":[{"do":"dance","times":2},{"say":"고마워요","expr":"love"}]}'
```
```js
let since = (await (await fetch('/t/padong-local/api/poll?since=0')).json()).last || 1;
setInterval(async () => {
  const j = await (await fetch('/t/padong-local/api/poll?since=' + since)).json();
  for (const it of j.items) runSteps(it.steps);   // 내 화면의 실행 함수
  since = j.last || since;
}, 1200);
```

## 화면만의 것 / 주의
- **동작·표정·장면·옷의 실제 모양과 애니메이션은 모두 ui.html 안**(three.js·캔버스)에 있습니다. API 는 이름(`do`/`play`/`expr`)만 주므로, 새 프론트엔드는 이 이름들을 자기 캐릭터 동작에 연결해야 합니다. 동작 이름을 늘릴 때는 화면(ACT)과 서버(ACTIONS) 둘 다 바꿔야 합니다.
- 기념일 옷 고르기, 낱말 규칙(LLM 없을 때), 립싱크(TTS 음량으로 입 움직임), 브라우저 음성(Web Speech) 대체, .glb·PNG·표정 시트 내보내기는 전부 브라우저에서 계산합니다(API 없음).
- 브라우저 콘솔 API(화면 안): `padong.do('wave',2)`, `padong.say('안녕')`, `padong.expr('wink')`, `padong.command('춤춰')`, `padong.play(id)`, `padong.run(steps)`, `padong.stop()`.
- **포털 펫**: 포털 페이지가 `static/pet.js` 를 넣고, 그 스크립트가 `?pet=1` 화면을 iframe 으로 띄워 `postMessage({padong:1, cmd:...})`(`run`·`play`·`stop`·`expr`·`look`·`mode`·`sound`·`outfit` 등)로 조종합니다. iframe 은 `{padong:1, ev:...}`(`ready`·`move`·`bubble`·`done`·`click`·`dblclick`·`drag`·`menu`·`script`·`stage` 등)로 알립니다. 같은 출처(origin)끼리만 받습니다. 펫은 `walk`/`run`/`fly` 를 `jump` 로 바꿔 실행하고(이동은 바깥 페이지가 담당), 날씨·GPU 는 `api/weather`·`api/gpu` 로, 긴 작업 완료 알림은 포털의 `/api/events` 로 받습니다. 펫 상태(켜짐·이름·위치·친밀도·대화)는 브라우저 localStorage 에만 있습니다.
- `ref/<파일>`: 화면 '원본 자료' 창이 부르는 `cha_padong_{intro,full01,turn,pose01,full02,pose02,pic01,pic02,pic03,credit}.jpg`·`cha_padong_turn.gif`·`wall_01.jpg`~`wall_20.jpg` 만 내줍니다(서버의 허용 목록 `REF_FILES`). 폴더에 다른 파일을 넣어도 나가지 않고, `../` 같은 경로는 404. 비공개 원본이라 **관리자(owner)만** 받을 수 있고, 다른 사용자는 403 입니다. `api/info` 의 `ref`(참/거짓)로 볼 수 있는지 알려 주며, 화면은 거짓이면 '원본 자료' 버튼을 숨깁니다.
- 공유 데이터: `api/weather`·`api/gpu`·포털 도구 목록 캐시는 모든 사용자가 같이 씁니다. 사용자별로 나뉘는 건 push/poll 큐뿐입니다.
