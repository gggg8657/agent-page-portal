# 팀 밸런스 맵 API (`saju-local`)
사주·MBTI·별자리·혈액형으로 팀원 간 협업 궁합·팀 맵·오늘의 운·사주 풀이 리포트·번개 모임을 다루는 워크숍용 도구. 포털 경로 접두어: `/t/saju-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/meta` → `/t/saju-local/api/meta`)

- 표준 라이브러리 `http.server` 기반. 요청·응답 본문은 모두 JSON(UTF-8). POST 는 `Content-Type` 헤더를 보지 않고 본문을 그대로 `json.loads` 함.
- LLM 은 문장 생성(오늘의 운, 성향 해설, 사주 풀이)에만 씀. LLM 이 없으면 규칙 문장으로 대체됨.
- 스트리밍(SSE) 없음. 긴 작업은 사주 풀이 리포트 하나뿐이며 시작 → 폴링 방식.

## 본인 확인 — `id` + `token` (중요)
이 도구는 **포털 계정(X-Agent-User)을 쓰지 않는다**. 모든 포털 사용자가 **같은 데이터(people.json, bungae.json)를 공유**하고, "누가 나인가"는 오직 등록 때 받은 `id` + `token` 쌍으로 구분한다.

1. **발급**: `POST api/register` 가 성공하면 응답에 `id`(16진 8자, `secrets.token_hex(4)`)와 `token`(16진 32자, `secrets.token_hex(16)`)을 돌려준다. 서버는 이 token 을 사람 기록 안에 평문으로 저장한다. **token 을 다시 받는 API 는 없다** — 잃어버리면 그 기록에 다시 접근할 방법이 없다(새로 등록해야 함).
2. **보관(지금 화면)**: `localStorage.tbm` 에 `{id, token, name, dept, alias, ...}` JSON 으로 저장한다. 다른 브라우저로 옮길 때는 `?id=<id>&token=<token>&name=..&dept=..&alias=..` 쿼리로 페이지를 열면 화면이 이를 `localStorage.tbm` 에 저장하고 주소창에서 쿼리를 지운다(`history.replaceState`).
3. **사용**: 본인 데이터가 필요한 호출은 GET 이면 쿼리 `id`·`token`, POST 이면 JSON 본문 `id`·`token` 으로 매번 보낸다. 서버는 `id` 로 사람을 찾고 저장된 token 과 **문자열이 정확히 같은지** 비교한다(쿠키·세션·헤더 없음).
4. **불일치**: 사람이 없거나 token 이 다르면 `400 {"error": "권한 없음"}` (POST 는 `400 {"error": "입력 오류: 권한 없음"}`). 화면은 시작할 때 `api/me` 가 실패하면 `localStorage.tbm` 을 지우고 미등록 상태로 돌아간다.
5. **필요한 곳**: `api/me`, `api/today`, `api/reading`, `api/match`, `api/bungae`(GET/POST), `api/bungae/join`, `api/bungae/leave`, `api/fortune`(GET/POST), `api/prefs`, `api/delete`, `report`.
   **필요 없는 곳**: `api/meta`, `api/status`, `api/register`, `api/graph`.
6. **주의**: token 이 URL 쿼리(GET)에 실리므로 서버·프록시 접근 기록, 브라우저 기록, `report` 링크 공유로 새어 나갈 수 있다. 새 화면에서도 token 을 남에게 보이는 곳(공유 링크 등)에 넣지 말 것. 관리자 owner 도 token 없이는 남의 본인 전용 API 를 못 부른다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/meta` | 성향 축·라벨·취미 목록 | - | 공용 |
| GET | `api/status` | LLM 연결 상태(30초 캐시) | LLM 서버에 목록 조회(3초 제한) | 공용 |
| POST | `api/register` | 등록 → `id`·`token` 발급 | 사주 계산만(빠름) | 새 기록 생성(공유 데이터) |
| GET | `api/me` | 내 전체 프로필 | - | id+token |
| POST | `api/prefs` | 취미·성향·원하는 상대 성향 저장 | - | id+token |
| POST | `api/delete` | 내 기록·풀이·번개 삭제 | - | id+token |
| GET | `api/today` | 오늘의 운(테마·별점·점심 등) | 첫 호출 시 LLM(최대 90초), 이후 날짜별 캐시 | id+token |
| GET | `api/reading` | 일하는 스타일 + 명리 해설 | 첫 호출 시 LLM 2회(각 최대 120초), 이후 저장 | id+token |
| GET | `api/match` | 궁합 상위 10명 | - | id+token |
| GET | `api/graph` | 팀 맵(노드·엣지·오행 균형) | - | **인증 없음**(공유) |
| GET | `api/fortune` | 사주 풀이 리포트 작업 상태 | - | id+token |
| POST | `api/fortune` | 사주 풀이 리포트 작성 시작 | LLM 15개 섹션, 2~3분(백그라운드) | id+token |
| GET | `report` | 리포트 HTML(보기/다운로드) | - | id+token |
| GET | `api/bungae` | 나에게 보이는 번개 목록 | - | id+token |
| POST | `api/bungae` | 번개 만들기 | - | id+token |
| POST | `api/bungae/join` | 번개 참여 | - | id+token |
| POST | `api/bungae/leave` | 번개 나가기(주최자면 번개 삭제) | - | id+token |
| GET | (그 밖의 모든 경로) | 화면 `ui.html` | - | - |

## 공통 오류
- GET: 입력/권한 오류 `400 {"error": "<메시지>"}`, 그 밖 `500 {"error": "<예외이름>: <메시지>"}`.
- POST: 입력/권한/필수 키 누락 `400 {"error": "입력 오류: <메시지>"}` (필수 키 누락이면 메시지가 키 이름, 예 `'year'`), 그 밖 `500`, 없는 경로 `404 {"error": "no route"}`.
- 정상 응답은 모두 200. 화면의 `api()` 도우미는 응답에 `error` 가 있으면 예외로 처리한다.

## 엔드포인트

### GET api/meta
- 요청: 없음
- 응답:
```json
{"axes": ["에너지","말","계획","주도","새로움","인원","승부","유머","술","일얘기"],
 "labels": {"에너지": ["활발·시끌","차분·조용"], "말": ["수다 담당","듣는 담당"], "...": ["왼쪽(-1)","오른쪽(+1)"]},
 "hobbies": ["볼링","등산","러닝","보드게임", "..."]}
```
- `labels[축] = [왼쪽(-1) 뜻, 오른쪽(+1) 뜻]`. 성향 값은 축마다 `-1`/`0`(상관없음)/`1`.

### GET api/status
- 응답: `{"llm": true, "model": "<모델 이름>", "model_found": true}` 또는 `{"llm": false, "model": "<모델>", "error": "<예외이름>"}`. 30초 동안 캐시.

### POST api/register
- 요청(JSON):

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| name | 문자열 | 예 | 실명(20자에서 자름). 같은 부서 사람에게 보임 |
| dept | 문자열 | 예 | 부서(30자에서 자름) |
| year, month, day | 정수 또는 숫자 문자열 | 예 | 양력 생년월일. year 는 1920~올해 |
| hour | 정수/문자열 | 아니오 | 0~23. 생략·`""`·`"unknown"` 이면 시주 없이 계산 |
| minute | 정수 | 아니오 | 기본 0 |
| mbti | 문자열 | 아니오 | `[EI][NS][TF][JP]` (대소문자 무관) |
| blood | 문자열 | 아니오 | `A`/`B`/`O`/`AB` |
| gender | 문자열 | 아니오 | `M`/`F`. 대운 계산에만 쓰고 저장 안 함 |
| consent_global | 불리언 | 아니오 | 회사 전체(부서 밖) 매칭·팀 맵에 별명으로 보일지. 기본 true |
| hobbies | 문자열 배열 | 아니오 | 최대 15개, 각 12자 |
| traits | 객체 `{축: -1/0/1}` | 아니오 | 내 성향 |
| wants | 객체 `{축: -1/0/1}` | 아니오 | 원하는 상대 성향(번개 필터) |

- 응답:
```json
{"id": "a1b2c3d4", "token": "<32자 16진>", "alias": "파란 고래",
 "pillars": ["갑자","병인","무진","경오"], "zodiac": "양",
 "profile": {"day_master": "무", "dm_el": "토", "elements": {...}, "ten_gods": {...}, "power": {...},
             "strength": "...", "yong": "...", "gyeok": "...", "tens": {...}, "daeun": {...}, "...": "..."}}
```
  - `profile` 은 `saju.analyze()` 결과 전체(필드가 많음, 코드 `saju.py` 참고). 화면은 이를 `me` 에 그대로 합쳐 둔다.
  - 생년월일시 원본·성별은 저장하지 않는다(4주 8자·분석 결과·출생 연도만 저장).
- 오류(400, `입력 오류: ...`): `이름·부서는 필수`, `연도 범위`, `시각 범위`, `MBTI 형식`, `혈액형`, `성별`, 필수 키 누락.

### GET api/me?id=&token=
- 응답: 공개 필드(`id, alias, dept, age_band, day_master, dm_el, elements, ten_gods, strength, mbti, zodiac, blood, dominant, lacking, hobbies, traits, gyeok, yong, power`) + `name` + 명리 상세(`pillars, tens, support_ratio, deukryeong, rooted, gyeok_god, hui, gi, yong_why, johu, gongmang, gongmang_hit, relations, daeun, unseong_day, dm_yin, lmt, hour_known, birth_year` 중 있는 것) + `wants`, `consent_global`.
- 화면은 시작 시 이 호출로 저장된 token 이 아직 유효한지 확인한다.

### POST api/prefs
- 요청: `{"id", "token", "hobbies": [...], "traits": {...}, "wants": {...}}` (register 와 같은 규칙으로 정리됨, 없는 축은 0)
- 응답: `{"ok": true, "hobbies": [...], "traits": {...}, "wants": {...}}`

### POST api/delete
- 요청: `{"id", "token"}`
- 응답: `{"ok": true}`. 사람 기록, 사주 풀이 캐시, 내가 주최한 번개를 지우고 다른 번개 참여자 목록에서도 뺀다. (`today-<날짜>.json` 의 오늘의 운 캐시 항목은 남는다.)

### GET api/today?id=&token=
- 응답(규칙 계산 + 문장):
```json
{"date": "2026-10-13", "day": "갑자", "day_god": "편재", "theme": "재성", "theme_work": "실행·성과",
 "branch_rel": "육합", "unseong": "제왕", "stars": 4, "day_el": "목", "menu": "비빔밥",
 "year": {"ganzi": "..", "god": "..", "rel": "..", "unseong": "..", "yong": false, "gi": false},
 "month": {"...": "..."}, "daeun": {"...": "..."} ,
 "text": "오늘 업무 팁", "research": "연구 운", "collab": "협업 운", "menu_why": "점심 이유", "llm": true}
```
- 메모: 하루 중 첫 호출에 LLM 을 부른다(최대 90초). LLM 결과만 `today-<날짜>.json` 에 캐시하고, LLM 실패 시 규칙 문장(`llm: false`)을 주며 캐시하지 않는다(다음 호출에 다시 시도).

### GET api/reading?id=&token=
- 응답: `{"reading": "<일하는 스타일>\n\n§\n\n<명리 해설>"}` — 화면은 `"\n\n§\n\n"` 로 나눠 두 카드로 보여 준다.
- 메모: 처음 한 번 LLM 2회(각 최대 120초) 후 사람 기록에 저장. LLM 이 없으면 한 줄 규칙 문장(구분자 없음)을 주고 저장하지 않는다.

### GET api/match?id=&token=&scope=&age_band=&dept=&mbti=&dm_el=&blood=
- `scope`: `dept`(내 부서, 실명 공개) 또는 그 외/생략(`all`, 전체 공개 동의자만, 별명만). 나머지 필터는 값이 같은 사람만 남김(빈 값은 무시).
- 응답: 점수 내림차순 최대 10명
```json
[{"id": "..", "alias": "느긋한 수달", "dept": "..", "age_band": "30대", "mbti": "INTJ", "dm_el": "수",
  "elements": {...}, "...": "공개 필드", "name": "(scope=dept 일 때만)", "score": 78, "why": ["일간 천간합 ..."]}]
```
- 오류(400): 전체 범위에서 후보가 3명 미만 → `조건에 맞는 사람이 3명 미만이라 익명 보호를 위해 결과를 보여주지 않습니다`, 후보 0명 → `아직 등록된 팀원이 없습니다`.

### GET api/graph?dept=
- 인증 없음.
- `dept` 있음: 그 부서 전원(동의 무관), 노드에 `name`(실명) 포함. `dept` 없음/빈 값: 전체 공개 동의자만, 실명 없음.
- 응답: `{"nodes": [공개 필드...], "edges": [{"a": id, "b": id, "score": 0~100, "why": [...]}], "balance": {"목": n, "화": n, "토": n, "금": n, "수": n}, "depts": ["부서", ...]}` — 엣지는 모든 쌍(n²/2).

### GET api/fortune?id=&token=
- 응답:
```json
{"ready": false, "gender": null, "model": null, "ts": null,
 "running": true, "done": 6, "total": 15, "error": null, "guess_gender": "M"}
```
  - `ready`: 완성된 리포트 글이 있는지. `running/done/total`: 진행 중인 작업(서버 메모리, 재시작하면 사라짐). `error`: 마지막 작업 오류 또는 `"n개 섹션 실패: ..."`. `guess_gender`: 화면 기본 선택값용 추정치.

### POST api/fortune
- 요청: `{"id", "token", "gender": "M"|"F"|"", "regen": false}`
- 응답: `GET api/fortune` 과 같은 상태 객체(즉시 반환).
- 메모: 백그라운드 스레드로 15개 섹션을 LLM 으로 작성(대략 2~3분). 이미 실행 중이거나, 같은 성별 리포트가 있고 `regen` 이 거짓이면 새로 시작하지 않는다. 진행은 `GET api/fortune` 을 폴링(화면은 몇 초 간격). 완료 시 사용자별 캐시 파일에 저장.

### GET report?id=&token=[&download=1]
- 응답: `text/html` 리포트 페이지(같은 부서 사람 정보 포함, 풀이 글이 없으면 글 없이 구성). `download=1` 이면 `Content-Disposition: attachment; filename*=UTF-8''사주리포트_<실명>.html`.
- 화면은 `window.open('report?id=..&token=..')` 로 새 탭에 연다. 오류는 JSON(`400 {"error": "권한 없음"}`).

### GET api/bungae?id=&token=
- 응답: 배열. 시작 3시간이 지난 번개는 빠짐. 내가 참여하지 않은 번개는 **취미 태그가 내 취미에 있고, 양방향 성향 조건이 맞고, 정원이 남은 것만** 보인다.
```json
[{"id": "a1b2c3", "host": "<주최자 id>", "title": "..", "place": "..", "hobby": "볼링",
  "at": "2026-10-13T18:30", "cap": 4, "joined": true, "mine": false, "score": 72, "why": [...],
  "reach": null, "host_name": "별명 · 부서",
  "members": [{"id": "..", "name": "참여했으면 실명, 아니면 별명", "dept": ".."}]}]
```
  - `at` 은 KST, 타임존 표기 없음. `reach`: 내가 주최한 번개일 때 이 번개를 볼 수 있는 사람 수(아니면 null). 정렬: 내 것 → 참여한 것 → 점수.

### POST api/bungae
- 요청: `{"id", "token", "title"(40자), "place"(40자), "hobby"(12자), "minutes": 5~10080(기본 60, 지금부터 몇 분 뒤), "cap": 2~30(기본 4)}`
- 응답: 만든 번개 객체 `{"id", "host", "title", "place", "hobby", "at", "cap", "members": [주최자 id]}`
- 오류: `제목·장소·취미 태그는 필수`, `시간은 5분~7일`.

### POST api/bungae/join · POST api/bungae/leave
- 요청: `{"id", "token", "bid": "<번개 id>"}`
- 응답: `{"ok": true}`
- join 오류: `없는 번개`, `취미·성향 조건이 맞지 않는 번개`, `정원 마감`. leave: 주최자가 나가면 번개 자체가 삭제됨.

## 흐름 예

공통 도우미(지금 화면과 같음):
```js
const api = async (u, b) => {
  const r = await fetch('/t/saju-local/' + u, b ? {method: 'POST', body: JSON.stringify(b)} : {});
  const j = await r.json(); if (j.error) throw new Error(j.error); return j;
};
```

1) 등록 → token 보관 → 오늘의 운
```js
const r = await api('api/register', {name: '홍길동', dept: '개발팀', year: 1990, month: 3, day: 15,
                                    hour: 'unknown', mbti: 'INTJ', consent_global: true});
localStorage.tbm = JSON.stringify({id: r.id, token: r.token, alias: r.alias});
const me = JSON.parse(localStorage.tbm);
const t = await api(`api/today?id=${me.id}&token=${me.token}`);   // 첫 호출은 LLM 때문에 수십 초 걸릴 수 있음
```

2) 사주 풀이 리포트 작성 → 폴링 → 열기
```js
await api('api/fortune', {id: me.id, token: me.token, gender: 'F'});
let s; do { await new Promise(z => setTimeout(z, 3000));
  s = await api(`api/fortune?id=${me.id}&token=${me.token}`);   // done/total 로 진행 표시
} while (s.running);
if (s.ready) window.open(`/t/saju-local/report?id=${me.id}&token=${me.token}`);
```

## 화면만의 것 / 주의
- 레이더(오각형) 그림, 별점 표시, 번개 시간 "n분 뒤" 표시, 오행 색 등은 화면이 계산한다.
- `localStorage.tbm` 관리(저장·복구·삭제)와 `?id=&token=` 쿼리 가져오기는 화면 몫이다.
- 데이터는 포털 사용자와 무관하게 **모두 공유**된다(같은 부서엔 실명·성향, 전체엔 동의자만 별명). 본인 전용은 8자·풀이 글·오늘의 운·리포트뿐이며 token 으로만 보호된다.
- 화면(`ui.html`)은 서버가 내보낼 때 저작권 표기를 덧붙인다. 정적 파일 경로(`/static/…`) 없음. 알 수 없는 GET 경로는 404 가 아니라 화면 HTML 을 돌려준다.
- 모든 응답에 `X-Author` 헤더가 붙는다.
