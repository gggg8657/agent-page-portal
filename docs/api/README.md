# 에이전트 포털 API 안내 — 새 화면(프론트엔드)을 붙이려는 분께

포털의 모든 도구는 **화면(`ui.html`)과 처리(`app.py`)가 나뉘어 있고, 둘 사이를 JSON HTTP API 로 주고받습니다.**
지금 화면도 이 API 만 씁니다. 따라서 화면을 새로 만들어도 같은 API 를 부르면 기능은 그대로 쓸 수 있습니다.

- 이 문서: 공통 규칙, 로그인, 연결 방법, 포털 자체 API, [보안 모델](#9-보안-모델--누가-무엇을-믿나), [버전](#10-버전호환)
- 도구별 API: 아래 [도구 목록](#도구-목록)의 각 파일
- 기계가 읽는 명세: `docs/api/openapi/*.json`(OpenAPI 3.1, 포털 + 도구 25개) — [8. OpenAPI](#8-openapi)
- 지금 서버가 어느 계약 버전인지: `GET /api/version`(로그인 전에도)

> © 2026 gggg8657 · 내부 시험용 · 배포용 아님. 포털 코드는 All rights reserved, 각 도구 라이선스는 도구 저장소의 LICENSE·THIRD_PARTY_NOTICES.md 를 따릅니다.
> 새 화면에서도 화면 아래쪽의 저작자 표기("© 2026 gggg8657 · 내부 시험용 · 배포용 아님")는 그대로 남겨 주세요.

## 1. 구조

```
브라우저(새 화면)
   │  fetch('/t/<도구>/api/…')   JSON 요청 · JSON 응답 (일부는 스트리밍·파일)
   ▼
포털  http://<서버>:8700
   │  ① 로그인 확인(쿠키 agsid)  ② X-Agent-User: <계정 id> 를 붙임  ③ 그대로 전달(최대 1시간)
   ▼
도구 app.py  http://127.0.0.1:<포트>   ← 서버 안에서만 열림. 바깥에서는 반드시 포털을 거침(포털 서명 X-Agent-Auth 가 없으면 401)
   │  필요하면
   ▼
로컬 LLM(vLLM, OpenAI 호환 · 임베딩은 Ollama) · GPU 모델
```

- **주소 규칙**: 도구 문서의 경로 `api/xxx` 는 포털에서 `/t/<도구 dir>/api/xxx` 입니다.
  예: 사주 도구의 `api/graph` → `GET /t/saju-local/api/graph?dept=…`
- **도구 포트로 직접 붙지 마세요.** 도구는 127.0.0.1(컨테이너면 내부망)에만 열려 있고, 누가 보냈는지는 포털이 붙이고 서명한 `X-Agent-User`·`X-Agent-Auth` 로만 정해집니다.
  화면에서 `X-Agent-User`·`X-User`·`X-Agent-Auth` 헤더를 보내도 포털이 지우고, 서명 없이 도구에 직접 가면 401 입니다.
- **쿠키는 도구로 가지 않습니다**(포털 세션 쿠키 포함 전부). 도구가 포털 쿠키(`ag…`)를 덮는 `Set-Cookie` 를 보내도 포털이 버립니다.
- **사용자별 데이터**: 도구는 `X-Agent-User` 로 사람마다 따로 저장합니다(기록·업로드 등). 일부는 일부러 공유합니다(주간보고 제출함, kordoc 양식, SQL 데모 DB, CAD 지식 보정, 사주 팀 지도) — 도구별 문서에 표시.

## 2. 로그인

| 호출 | 설명 |
|---|---|
| `POST /api/login` `{"id": "아이디", "pin": "PIN", "remember": true}` | 성공 `{"ok": true, "id": …}` + 쿠키 `agsid` (HttpOnly, SameSite=Lax — 바꾸기는 아래 3절). `remember`(로그인 유지, 없으면 `true`): `true` = 30일, 쓰는 동안 하루에 한 번 연장 · `false` = 쿠키에 Max-Age 없음(브라우저 닫으면 끝) + 서버 만료 12시간, 연장 없음. 틀리면 400 `{"error", "code": "bad_credentials"}`. 같은 아이디를 같은 접속지에서 10분에 5번 틀리면 **429** `code: "locked"`(10분), 한 접속지가 아이디를 바꿔 가며 10분에 30번 넘게 틀리면 429 `code: "ip_locked"`, 로그인이 몰리면 429 `code: "busy"` — 모두 `Retry-After`(초) 머리 |
| `POST /api/signup` `{"id", "name", "pin", "dept"}` | 가입. id 는 영문 소문자·숫자·`_.-` 2~32자, PIN 은 숫자 4~8자리. 부서는 `GET /api/depts` 목록 중 하나(부서장 승인 뒤 반영). 계정 저장소가 포털 밖(`AUTH_BACKEND=external`)이면 403 `code: "unsupported"` + 안내. 한 접속지에서 한 시간에 5번까지(넘으면 429 `code: "rate_limited"`) |
| `GET /api/auth` | 로그인 전에도: `{"signup", "password_change", "guide"}` — 지금(포털 내부 DB)은 둘 다 `true`. `false` 면 가입·PIN 바꾸기 화면을 숨기고 `guide` 를 보여 주세요 |
| `GET /login` | 로그인 화면(랜딩). 이미 로그인했으면 `next`(같은 사이트 경로만, 아니면 `/`)로 302 |
| `POST /api/logout` | **새 화면은 이것**: 세션 삭제 + 쿠키 지움 → `{"ok": true}`(로그인 안 돼 있어도 200) |
| `GET /logout` | (예전 화면용) 세션 삭제 후 `/login` 으로 302. 다른 사이트에서 건 링크(`Sec-Fetch-Site: cross-site`)로는 로그아웃하지 않고 `/` 로 |
| `GET /api/version` | 로그인 전에도: `{"api": "2026.10.14", "build": "<포털 git describe>", "auth": {…/api/auth 와 같음}, "cors": false}` — [10. 버전](#10-버전호환) |
| `GET /api/landing` | 로그인 전에도: 랜딩의 분류·도구 수 `{"groups": [{name, n, names}], "tools"}` |
| `GET /api/me` | 지금 사용자 `{"id", "name", "role", "dept", "owner", "onboarded", …}` — `onboarded`: 첫 로그인 둘러보기를 봤거나 건너뜀 |

- **현재 계정 저장소는 임시**(포털 내부 DB)이며, 운영 시 **사내 챗봇 계정 DB 로 대체 예정**입니다(같은 아이디·비밀번호). 그때도 세션 쿠키·`/api/me`·부서·권한은 그대로이고, 가입·PIN 변경만 막힙니다(`GET /api/auth`). 외부 저장소가 응답하지 않으면 `/api/login` 이 503.
- 로그인 안 된 요청: API 는 **401** `{"error": "로그인이 필요합니다", "login": "/login"}`, 화면 요청(`Accept: text/html` 인 GET)은 `/login?next=…` 로 302.
- 쿠키가 HttpOnly 라 JS 에서 읽을 수 없습니다. **같은 출처에서 `fetch(…, {credentials: "same-origin"})`(기본값)로 부르면 브라우저가 알아서 붙입니다.**
- 서버 안(127.0.0.1)에서 쿠키 없이 + 비밀 헤더 `X-Local-Owner: <토큰>` 을 붙여 부르면 "서버 주인(owner)"으로 처리됩니다(관리·스크립트용). 토큰은 서버의 `_data/.local_owner_token`(포털이 처음 뜰 때 만듦, 권한 600) — 예: `curl -H "X-Local-Owner: $(cat ~/agent-page/_data/.local_owner_token)" …`. 헤더가 없으면 127.0.0.1 이어도 일반 비로그인(401)이라, SSH 포트포워딩으로 붙은 브라우저도 로그아웃하면 다시 로그인해야 합니다(포털 = FastAPI `fastportal`, 2026-10-13~. `PORTAL_LOCAL_OWNER=1` 이면 예전처럼 헤더 없이 주인).

## 3. 새 화면을 연결하는 방법

기본 설정에서 포털은 **CORS 헤더를 내보내지 않고**, 로그인 쿠키는 **SameSite=Lax** 입니다. 그래서 다른 주소(다른 포트·도메인)에 올린 화면에서 포털 API 를 바로 부르면 막히거나 로그인이 안 됩니다. 두 가지 방법이 있습니다.

| | A. 같은 출처(권장) | B. 다른 출처(CORS) |
|---|---|---|
| 배치 | 앞단 프록시(nginx 등) 하나가 `/` = 새 화면 정적 파일, `/api`·`/t`·`/logout` = 포털 | 새 화면이 `https://front.example` 처럼 따로, 포털은 `https://portal.example` |
| 포털 설정 | 없음(앞단 프록시가 `Host` 를 바꾸면 `PORTAL_ALLOWED_ORIGINS=<화면 출처>`) | `PORTAL_CORS_ORIGINS=https://front.example`(쉼표로 여럿), 쿠키를 보내려면 `PORTAL_COOKIE_SAMESITE=None` + **HTTPS**(`TLS_CERT·TLS_KEY` 또는 앞단에서 HTTPS 를 끝내면 `PORTAL_COOKIE_SECURE=1`) — 없으면 포털이 기동을 거부 |
| 화면 코드 | `fetch('/api/…')` 그대로 | `fetch('https://portal.example/api/…', {credentials: 'include'})`. 작업 알림 SSE 는 `new EventSource(url, {withCredentials: true})` |
| 도구 화면 iframe | 됨 | 됨(`frame-ancestors` 에 CORS 출처가 들어감) — 단 브라우저의 서드파티 쿠키 차단 설정에 따라 iframe 안 로그인이 안 될 수 있음 |
| 주의 | — | 다른 사이트(도메인이 다름)면 브라우저가 서드파티 쿠키를 막을 수 있음. 가능하면 같은 상위 도메인(같은 사이트)으로 |

**요청 위조 방지(CSRF) — 두 방법 모두**: 상태를 바꾸는 요청(POST·PUT·DELETE·PATCH)은 브라우저가 붙이는 `Origin`(없으면 `Referer`)이 포털과 같은 출처이거나 허용 목록(`PORTAL_CORS_ORIGINS`·`PORTAL_ALLOWED_ORIGINS`)에 있어야 합니다. 아니면 403 `{"code": "csrf"}`. `Origin: null`(sandbox·파일에서 연 화면)도 거부. curl·스크립트처럼 이런 헤더가 없는 요청은 통과합니다.

### 개발할 때 — 개발 서버 프록시
자기 PC 에서 개발 서버를 띄우고, `/api`·`/t`·`/login` 요청을 포털로 넘기게 합니다. 브라우저 입장에서는 같은 출처라 쿠키·CORS 문제가 없습니다.

```js
// vite.config.js
export default {
  server: {
    proxy: {
      '/api':   { target: 'http://<서버>:8700', changeOrigin: true },
      '/t':     { target: 'http://<서버>:8700', changeOrigin: true },   // 스트리밍(SSE)도 그대로 통과
      '/logout':{ target: 'http://<서버>:8700', changeOrigin: true },
    },
  },
};
```

```js
// 로그인 → 도구 API 호출
await fetch('/api/login', {method: 'POST', body: JSON.stringify({id: 'myid', pin: '1234'})});
const me = await (await fetch('/api/me')).json();
const g  = await (await fetch('/t/saju-local/api/graph?dept=' + encodeURIComponent('우리팀'))).json();
```

### 배포할 때
다 만든 화면은 포털과 같은 출처에서 나가야 합니다. 방법은 관리자(gggg8657)와 정합니다.
- 도구의 `ui.html` 을 새 화면으로 바꾸기(도구 저장소에 PR), 또는
- 포털에 새 화면용 정적 경로를 추가하기 — React 화면(agent-page-web)은 이 방식으로 **운영 중(`:8700/app`)**: 포털(fastportal)에 `WEB_DIST=<빌드 폴더>`(·`WEB_PREFIX`, 기본 `/app`)를 주면 `/app/…` 에서 같은 출처로 내보냄(SPA 폴백, `assets/` 긴 캐시, `index.html` no-cache). 로그인 화면·자산은 로그인 없이, 그 밖의 화면 주소는 로그인 전이면 `/app/login?next=` 로 302, API 는 401. 빌드는 `WEB_BASE`(기본 `/app/`, 라우터 `basename` 도 같게). `/` 까지 React 로 바꾸는 `WEB_ROOT`(준비만, 꺼짐)·배포·되돌리기는 포털 README 'FastAPI 포털' 과 agent-page-web `CUTOVER.md`.
- React 화면이 부르는 포털 API: 셸이 `/api/me`·`/api/tools`·`/api/gpu`(홈, `llm` 대기열 포함)·`/api/jobs`·`/api/jobs/stream`(작업 알림 센터 🔔 — SSE, 끊기면 30초 폴링)·`/api/jobs/read`·`/api/account/notices`·`/api/account/theme`(테마 6종 저장)·`/api/account/onboarded`(둘러보기), 계정·승인·관리 화면이 `/api/account*`·`/api/pin`·`/api/approvals*`·`/api/admin*` — 예전 `account.html`·`theme/tour.js`·`help.html` 과 같은 요청(동등성 러너 `tests/parity/shell.spec.ts`·`jobs.spec.ts` 로 비교).

다른 서버에 따로 올려야 한다면 포털에 CORS·토큰 방식을 따로 열어야 하니 먼저 말해 주세요.
도구 자체가 다른 컨테이너·서버에서 돌 때(혼합형)는 `tools.json` 항목에 `"host"` 를 넣으면 포털이 `http://<host>:<port>` 로 프록시합니다(시작·중지는 409, 그쪽이 늘 켜 둠 — 도구는 `BIND=0.0.0.0`). 화면에서 보는 주소(`/t/<도구>/…`)·헤더 규칙은 그대로입니다.

### 포털이 도구 화면에 자동으로 넣는 것
도구의 **첫 화면 HTML**(`/t/<도구>/`)에는 포털이 사용법 `?` 버튼, 화면 테마(boot.js·CSS), 저작자 표기, 파동이 펫을 끼워 넣습니다. API 응답(JSON·파일)은 손대지 않습니다.
새 화면을 따로 띄우면 이것들은 붙지 않으니, 필요하면 `GET /api/guide/<도구>`(사용법 마크다운)를 직접 가져다 쓰세요.

## 4. 공통 규칙

- **요청**: 대부분 `POST` + JSON 본문(`Content-Type: application/json`). 파일 업로드는 도구마다 다릅니다(JSON 안 base64 또는 multipart) — 도구별 문서 참고.
- **메서드**: `/t/<도구>/…` 는 GET·POST·PUT·DELETE·PATCH·HEAD·OPTIONS 를 모두 도구로 넘깁니다. 포털 자체 API(`/api/…`)는 GET·POST 만(다른 메서드는 405 + `Allow`). `OPTIONS` 는 포털이 바로 204(CORS 사전 요청).
- **본문 상한**: 도구로 가는 본문 600MB(`PORTAL_MAX_BODY`), 포털 API JSON 1MB — 넘으면 413. 길이를 모르는(청크) 업로드도 받습니다(8MB 넘으면 포털이 디스크 임시 파일에 모은 뒤 길이를 붙여 넘김).
- **응답**: JSON. 파일 내려받기는 해당 `Content-Type` 과 `Content-Disposition` 으로 옵니다.
- **오류**: 본문 `{"error": "사람이 읽을 수 있는 한국어 설명"}` + 상태 코드. 포털이 만든 오류에는 기계가 구분할 `code` 가 붙습니다(`bad_request`·`bad_credentials`·`locked`·`ip_locked`·`busy`·`rate_limited`·`unsupported`·`unavailable`·`csrf`·`method_not_allowed`·`starting`·`server_error` 등) — **문구가 아니라 `code`·상태 코드로 분기**하세요(문구는 바뀔 수 있음). 도구 오류는 `{"error": "…"}`(tts 의 OpenAI 호환 `/v1/*` 만 `{"error": {"message", "type"}}`), 서버 경로·내부 주소는 감춰서 나옵니다. 도구의 없는 API 경로(`/t/<도구>/api/<없음>`)는 404 JSON.

  | 코드 | 뜻 |
  |---|---|
  | 400 | 입력이 잘못됨(본문이 JSON 객체가 아님, 필수 값 없음 등) |
  | 401 | 로그인 필요 |
  | 403 | 권한 없음(관리자·부서장 전용) |
  | 404 | 없는 경로·도구 |
  | 405 | 포털 API 에 GET·POST 말고 다른 메서드 |
  | 409 | 이미 실행 중인 도구를 다시 시작하려 함(관리자만 가능), 방금 시작을 눌러 켜는 중(`code: "starting"`, 60초) |
  | 413 | 본문이 상한보다 큼 |
  | 429 | 로그인 잠금·몰림·가입 제한 — `Retry-After` 초 뒤 다시 |
  | 502 | **도구가 꺼져 있음** → `POST /api/start {"dir": "<도구>"}` 로 켠 뒤 다시 시도 |

- **오래 걸리는 작업**: 포털은 요청 하나를 최대 1시간 기다립니다. 영상·구조 예측처럼 긴 작업은 도구마다 ① 스트리밍(SSE·줄 단위 JSON)으로 진행 상황을 흘리거나 ② 작업 id 를 받고 상태를 폴링합니다 — 도구별 문서의 "오래 걸림" 표시를 보세요.
  **작업 알림 센터**: 도구가 알린 작업(대기·진행·완료·실패·취소)과 1분 넘게 걸린 POST(포털 자동 기록)가 `portal.db` 의 작업 표에 남고, `GET /api/jobs/stream`(SSE)으로 바로 옵니다 — 화면의 🔔·토스트. 예전 `GET /api/events` 도 같은 표에서 그대로 동작(파동이 펫의 "작업 끝났어요!").
- **스트리밍**: 포털은 응답을 버퍼링하지 않고 그대로 흘립니다. `fetch` 의 `response.body.getReader()` 나 `EventSource` 로 읽으면 됩니다.
- **GPU·LLM**: 이 표시가 있는 호출은 서버 GPU 를 씁니다. 여러 사람이 동시에 부르면 줄을 서니, 버튼 연타를 막아 주세요. `GET /api/gpu` 로 GPU 여유와 LLM 대기열(`llm.waiting`)을 볼 수 있습니다.

## 5. 포털 자체 API

| 메서드·경로 | 하는 일 | 권한 |
|---|---|---|
| `GET /api/tools` | 도구 목록 `{"tools": [{dir, name, title, desc, group, tags, port, up(켜짐), url, usage}], "rank", "posts", "me"}` | 로그인 |
| `GET /api/me` | 지금 사용자 | 로그인 |
| `GET /api/gpu` | GPU 사용 현황 `{"gpus": [{index, name, used, total, util, procs: [{tool, mem}]}], "t", "llm"}` — `llm` = LLM 대기열 `{ok, model, running(생성 중), waiting(대기), kv_cache(0~1)}`(vLLM 일 때만, 아니면 null · 5초 캐시) | 로그인 |
| `GET /api/jobs?unread=1&limit=50&since=<seq>` | 작업 알림 센터 목록 `{"jobs": [Job], "unread", "last"}` — Job = `{id, tool, name, user, title, status(queued·running·done·failed·canceled), progress, started, finished, link(/t/<도구>/…), message, seq, unread}`. 본인 것만(주인은 전부), `unread=1` 이면 안 읽은 끝난 작업 + 도는 작업 | 로그인 |
| `GET /api/jobs/stream` | 실시간(SSE, `EventSource`): `hello {last, unread}` → 바뀔 때마다 `job`(id: seq) · 다른 탭에서 읽으면 `read {ids}` · 25초마다 `: ping`. 다시 붙으면 `Last-Event-ID`(또는 `?since=`) 뒤 바뀐 것부터. 연결마다 스레드를 쓰지 않음 | 로그인 |
| `POST /api/jobs/read` `{"ids": [...]}` 또는 `{"all": true}` | 읽음 표시(보는 사람마다 따로) → `{ok, read, unread}` | 로그인 |
| `GET /api/events?since=<id>` | (예전 모양) 끝난 내 작업 `{"events": [{id, dir, name, ok, sec, t, title, link}], "last"}` — 작업 표의 done·failed, id = seq | 로그인 |
| `POST /api/internal/jobs` | **도구 전용**(화면 X) — 아래 '도구가 작업 상태를 알리는 법' | 127.0.0.1 + 도구 토큰 |
| `GET /api/guide/<도구>` | 도구 사용법(마크다운 텍스트) | 로그인 |
| `GET /api/readme/<도구>` | 도구 README(마크다운 텍스트) | 로그인 |
| `GET /api/depts` | 부서 목록 | 누구나 |
| `POST /api/start` `{"dir"}` | 꺼진 도구 켜기(켜진 도구 재시작은 관리자만, 같은 도구를 60초 안에 다시 누르면 409 `starting`). 숨긴 도구는 관리자만(일반 사용자는 404) | 로그인 |
| `POST /api/stop` `{"dir"}` | 도구 끄기 | 관리자 |
| `POST /api/open` · `/api/like` `{"dir"}` | 사용·좋아요 기록(순위에 반영). 좋아요는 계정당 도구마다 1회 — 두 번째부터는 `{"ok":true,"liked":true,"already":true}` 로 무시(취소 없음) | 로그인 |
| `POST /api/dwell` `{"visit", "seq", "screen", "tool"?, "seconds"}` | 활성 탭의 짧은 화면 체류 구간. 로그인 uid로 저장하고 중복 방문 순번은 무시. 화면이 보이고 최근 60초 안에 입력한 경우만 기록(구간은 최대 30초) | 로그인 |
| `GET /api/lists` | 내 즐겨찾기·최근 사용 `{"fav": [dir…], "recent": [dir…]}` — 계정에 저장(브라우저 캐시와 무관) | 로그인 |
| `POST /api/lists` `{"kind": "fav"\|"recent", "items": [dir…]}` | 그 목록을 통째로 바꿈. 등록된 도구만·중복 제거·최대 fav 100·recent 10. 응답은 정리된 목록 | 로그인 |
| `GET /api/admin/dwell?days=30&uid=&screen=&tool=` | 관리자 화면 체류 집계(7·30·90일). 계정·화면·도구별 체류/방문과 도구 열기 버튼 수·끝난 작업 실행 시간을 별도 제공 | 관리자 |
| `POST /api/post` · `/api/post_like` | 노하우 게시판 글쓰기·좋아요 | 로그인 |
| `POST /api/account/theme` `{"theme"}` | 화면 테마 저장(`classic`·`trendy`·`v2` × `light`·`dark`·`auto`) | 로그인 |
| `POST /api/pin` `{"pin", "new"}` | PIN 바꾸기(다른 기기 세션은 끊김, 지금 세션의 '로그인 유지'는 그대로). 외부 계정 저장소면 403 + 안내 | 로그인 |
| `POST /api/account/notices` | 승인·반려 알림(안 본 것) + 내가 처리할 승인 수 `{"notices", "approvals"}` 을 받고 본 것으로 표시. `GET /api/account/notices?peek=1` 은 표시하지 않고 보기만(예전 화면의 `GET`(peek 없음)은 표시까지 함 — 호환용) | 로그인 |
| `POST /api/account/onboarded` `{"done": true}` | 첫 로그인 둘러보기 마침·건너뜀 기록(`/api/me` 의 `onboarded`) | 본인 |
| `/api/account…` · `/api/approvals…` · `/api/admin…` | 계정 설정·부서 승인·관리. `GET /api/admin` 의 `auth_backend` `{name, label, signup, password_change, guide}` = 계정 저장소(지금 `local` "임시(포털 내부 DB)") | 본인·부서장·관리자 |
| `POST /api/account/withdraw` `{"reason"?}` | 탈퇴 요청 — 바로 지우지 않고 관리자 승인 목록(kind `withdraw`)으로. 승인하면 비활성화, 본인은 `/api/account/cancel` 로 취소 | 본인 |
| `POST /api/admin/account` `{"op", "uid", "reason"?, "confirm"?}` | 삭제 대신: `deactivate`(로그인 불가·세션 끊김·부서장 회수·대기 요청 취소, 되살릴 수 있음) · `reactivate` · `anonymize`(**되돌릴 수 없음** — 비활성 계정만, `reason` 필수, `confirm` = uid 그대로. 이름·부서·PIN 을 지우고 화면·API 에는 `deleted-…` 로만, 아이디는 내부 키로 남아 감사 기록·도구 데이터의 만든 사람 연결 유지·재가입 불가). 자동 정리 `PORTAL_ANON_AUTO=1`(기한 `PORTAL_ANON_AFTER_DAYS`, 기본 365일·기본 꺼짐) | 관리자 |
| `POST /api/admin/dept` `{"op": "archive" \| "restore", "name"}` | 부서 보관·되살리기 — 소속 인원(비활성·익명화 제외)·대기 요청이 있으면 400 + 이유. 보관하면 가입·변경 목록에서 빠짐(`/api/admin` 의 `archived`) | 관리자 |
| `GET /api/community/summary?t=agent:<id>,tool:<dir>` · `/api/community/reviews?kind=&id=` · `/api/community/requests?kind=&id=` \| `?box=received\|sent` · `/api/community/ranking` | **모두의 에이전트 커뮤니티**: 카드용 평균 별점·리뷰 수(수만), 대상 리뷰·요청(볼 수 있는 대상만 — 도구=숨김 아님, 흐름=만든 사람, 에이전트=빌더가 보여 주는 것), 내 요청함, 이달의 제작자(질문+10×복제+5×리뷰+20×반영, 계정 id 없음) | 로그인 |
| `POST /api/community/review` `{kind,id,stars 1~5,body≤500}` · `/review/delete` · `/review/hide`(관리자) | 리뷰(대상마다 1개, 자기 것 금지 403, 남의 리뷰 지우기 403). 10분에 쓰기 20번 | 로그인·관리자 |
| `POST /api/community/request` `{kind,id,type improve\|bug\|feature,title≤80,body≤2000}` — 도구 요청은 관리자 알림함 + tools.json author 계정이 받고, 관리자의 받은 요청에 도구 요청 전부가 보임. 숨긴 도구는 관리자도 403 — · `/request/status` `{id,status open\|review\|done\|hold,note}` · `/request/vote` `{id,on}` · `/request/delete` · `/request/hide`(관리자) | 개선·요청. 상태는 대상 주인·관리자만(요청자도 403), 주인·요청자에게 작업 알림(tool `community`), +1 은 한 사람 1표·내 요청 금지. 화면은 텍스트로만 그림 | 로그인·주인·관리자 |
| `GET /api/flows/stats` | 모두의 에이전트 참여 현황(수만): `{flows, flow_makers, flow_runs_7d, tool_opens_7d, active_users_7d, dev_tools}` — 빌더 쪽 수는 `/t/agent-builder-local/api/stats` | 로그인 |
| `GET /api/flows` · `/api/flows/adapters` · `/api/flows/<id>` · `/api/flows/runs/<run_id>` | **모두의 에이전트 — 기존 에이전트 연결**: 내 흐름·최근 실행, 연결할 수 있는 단계(어댑터 — 서버에 정의한 10개만: 회의록 만들기·공지문 초안·글 다듬기·공문체 윤문·공문 양식 초안·메일 초안·원고 검토·다이어그램·SQL 질의·표→그래프), 실행 기록(진행·단계별 결과·멈춘 이유). 남의 흐름·기록은 403(관리자도) | 본인 |
| `POST /api/flows/save` `{id?, name, steps: [{adapter, inputs: {칸: {from: prev\|step\|const\|ask, step?, value?}}}]}` · `/api/flows/delete` `{id}` | 흐름 저장(단계 2~6개, 계정당 50개)·지우기 | 본인 |
| `POST /api/flows/run` `{id, inputs: {"<단계>.<칸>": 글 \| {name, b64}}}` | 실행 시작 → `{run: {id}}`. 포털이 순서대로 도구 API 를 부름(X-Agent-User = 실행자 + 포털 서명, 프록시와 같음). 사용자당 동시 1개, 단계별 시간 제한, 실패 단계에서 멈춤. 진행은 작업 알림(tool `flows`, link `/create?tab=make&m=flow&run=…`). 본문 상한 40MB(`FLOW_RUN_MAX_BYTES`) | 본인 |
| `POST /api/idea/plan` `{text≤2000, route?: existing\|builder\|flow\|newtool, id?}` | **모두의 에이전트 — 아이디어로 만들기**(LLM, 수십 초): 하고 싶은 일 → 가장 가벼운 길 `route`·이유·대안 1~2개 + 그 길의 결과만 — `existing`(추천 도구·공유 에이전트 + 바로 열기 링크) · `builder`(이름·설명·지시문·예시 질문 3개·필요한 자료·템플릿 → 화면이 빌더 `POST /t/agent-builder-local/api/agents` 로 내 초안) · `flow`(어댑터 안에서만 단계·매핑, 서버 검증 — 틀리면 한 번 다시 묻고 그래도 틀리면 기본값으로 고치거나 `valid:false`+`error`) · `newtool`(**데모** — `<이름>-local` 파일 트리·코드·문법/위험 패턴·패키지 검사(폐쇄망 목록에 없으면 `missing`). 실행·설치·등록·제출 안 함). `route` 를 주면 그 길만 다시(`id` 로 같은 기록). 사용자당 동시 1개(409 `busy`)·10분에 10번(429 `rate_limited`, `IDEA_RATE_N`)·LLM 실패 503 `llm`. 사용자 글은 구분자로 감싼 데이터로만 다루고 응답은 스키마 검증·길이 상한 | 본인 |
| `GET /api/idea/recent` · `/api/idea/<id>` · `POST /api/idea/made` `{id, kind: agent\|flow, ref}` · `/api/idea/delete` `{id}` · `/api/idea/zip` `{id}` | 최근 아이디어 20개(글·길·만든 결과 id)·저장된 결과 다시 열기(LLM 안 부름)·만든 초안/흐름 id 기록·지우기·새 도구 뼈대 zip(메모리에서 만들어 내려줌, 서버가 정한 파일 이름만). 남의 기록 403 | 본인 |
| `POST /api/dev/submit` (본문 zip) | **모두의 에이전트 — 개발자 업로드**: `<이름>-local` 폴더 하나 zip(50MB·파일 2000개). zip slip·절대 경로·링크 거절 → 형식 검사 → 격리 컨테이너 자가검증(Docker `--network none`·읽기 전용·CPU/메모리 상한·GPU 없음, 이름 `dongjukim-devcheck-…`: 문법·selftest.py·`/healthz`) → 관리자 승인 대기. 이미지가 없으면 '건너뜀'으로 표시하고 관리자 확인 필요. 권한(로그인·개발자)·검사 중 제출을 **본문보다 먼저** 확인해 아니면 본문을 읽지 않고 403(검사 중이면 400) | 개발자 |
| `GET /api/dev/submissions` · `/api/dev/submissions/<id>` · `…/<id>/file?path=` | 제출 목록·상세(검사·검증 로그·파일 목록)·파일 미리보기. 개발자는 자기 것만. 목록의 `approve: {enabled, reason}` = 승인 잠금 여부 | 개발자·관리자 |
| `POST /api/admin/dev/decide` `{id, ok, note?, confirm_skipped?}` | 승인 → 도구 폴더 설치 + tools.json 등록 + guides/<dir>.md + 포트 배정 + 서명 모드 시작 / 거절(사유 필수). 감사 기록. **승인 잠금(임시)**: 업로드 도구의 실행 격리가 끝날 때까지 포털 설정 `PORTAL_DEV_APPROVE=0`(기본)이면 승인(ok=true)은 409 `approve_locked` "실행 격리 준비 중 — 지금은 승인할 수 없습니다". 거절·업로드·형식 검사·격리 검증은 그대로. 풀려면 `PORTAL_DEV_APPROVE=1` 로 포털 재시작 | 관리자 |
| `POST /api/admin/developer` `{uid, op: grant\|revoke}` | 개발자 역할 부여·회수(`/api/me`·`/api/admin` users 의 `developer`). 감사 기록 | 관리자 |
| `GET /api/pkg-index?q=` · `/api/pkg-index/check?names=a,b` | **폐쇄망 pip 패키지 찾기**: 이름 접두·부분 일치(PEP 503 정규화, 최대 50개) / 이름별 `{name, found, versions}`. 응답에 목록 `source`·`updated`·`count`, 그리고 포털 설정 `PORTAL_PIP_INDEX_URL` 이 있을 때만 `index_url`(화면이 `pip install --index-url <내부 PyPI 주소> 이름` 안내) | 로그인 |
| `POST /api/admin/pkg-index?source=<파일 이름>` (본문 = 목록 글자, 8MB) | 폐쇄망 패키지 목록을 통째로 바꿈. 이름만 / `이름==버전` / `이름 버전,버전` / pip list 표 / `pip index versions` 출력을 읽음. 관리자 확인을 본문보다 먼저(아니면 403). 감사 `pkg_index_update`. 포털은 기동할 때 표가 비어 있으면 `_data/pkg-index/*.txt` 의 가장 최근 파일을 읽음. 개발자 업로드 형식 검사가 이 목록으로 requirements*.txt·pyproject.toml·setup.sh 의 pip install 을 확인해 **경고**(목록에 없음 "폐쇄망에서 설치 불가", 없는 버전 "버전 없음", git·URL·로컬 경로 설치) — 목록이 비면 건너뛰고 안내만 | 관리자 |

### 도구가 작업 상태를 알리는 법(개발 가이드)
오래 걸리는 작업(대기열·폴링·스트리밍)을 하는 도구는 상태를 포털에 알려 주세요 — 사용자는 다른 화면에 있어도 🔔·토스트·브라우저 알림으로 받고, '결과 보기'로 그 결과 화면에 바로 갑니다.
1. `agent-page-portal/sdk/portal_jobs.py`(표준 라이브러리만, 한 파일)를 도구 저장소에 복사.
2. 상태가 바뀌는 곳에서 한 줄:
   ```python
   import portal_jobs
   portal_jobs.notify(user, "running", id=job_id, title="유비퀴틴 구조 예측", link="/#" + job_id)    # user = 요청의 X-Agent-User
   portal_jobs.notify(user, "done", id=job_id, message="pLDDT 87")                                   # done | failed | canceled | queued | running (+ progress=0~100)
   ```
   `id` 는 도구의 작업 id(같은 id 로 보내면 고침), `link` 는 **도구 안 경로**(`/` 로 시작 — `/#<id>`, `/?job=<id>` 등, 그 결과를 여는 주소 — 포털이 `/t/<도구>` 를 붙이고 React 셸은 `/app/tool/<도구>/…` 로 엶). 끝난 작업을 다시 진행 상태로 보내면 무시됩니다.
3. 아무 설정도 필요 없습니다 — 포털이 도구를 켤 때 환경변수 `PORTAL_JOBS_URL`(`http://127.0.0.1:<포털 포트>/api/internal/jobs`)·`PORTAL_JOBS_TOKEN`(그 도구 전용, 로그·화면에 찍지 말 것)·`PORTAL_JOBS_TOOL` 을 넘깁니다. 없으면(포털 밖에서 띄움·selftest) `notify` 는 아무것도 안 하고 `False`. 보내기는 백그라운드 스레드 하나가 순서대로, 2초 타임아웃, 실패는 버림 — 도구 동작에 영향 없음. selftest 에 '환경변수 없이도 정상' 시험을 넣어 주세요.
4. 다른 컨테이너의 도구(`tools.json` `host`): 그쪽에 `PORTAL_JOBS_URL=http://<포털>:<포트>/api/internal/jobs`·`PORTAL_JOBS_TOOL=<dir>` 와, `PORTAL_JOBS_TOKEN`(포털 쪽에서 관리자가 `python -m fastportal.jobs token <dir>` 로 만든 값 — 포털 비밀값 `_data/.portal_jobs_secret` 또는 `PORTAL_JOBS_SECRET` 이 같아야 맞음)을 주세요. 포털은 그 도구의 host 주소(또는 `PORTAL_JOBS_ALLOW`)에서 온 요청만 받습니다.
직접 알리는 도구는 포털의 '1분 넘는 POST 자동 기록'에서 빠집니다(두 번 뜨지 않게). 지금 쓰는 도구: protein·battery·avatar·image·meeting·review·agent-cad.

## 도구 목록

| 도구 | dir | 분류 | 하는 일 | API 문서 |
|---|---|---|---|---|
| Humanize KR | `humanize-kr-local` | 문서 | AI가 쓴 한글의 번역투·상투구 제거 | [humanize-kr-local.md](humanize-kr-local.md) |
| kordoc | `kordoc-local` | 문서 | HWP·PDF 요약·질문·공문서 초안 HWPX | [kordoc-local.md](kordoc-local.md) |
| Notebook | `notebook-local` | 문서 | 출처 올리고 인용 채팅·노트·팟캐스트 대본 | [notebook-local.md](notebook-local.md) |
| Writer | `writer-local` | 문서 | 건배사·경조사·축사·공지문 패턴 글쓰기 | [writer-local.md](writer-local.md) |
| 메일 | `mail-local` | 문서 | 업무 메일 작성·회신·윤문 | [mail-local.md](mail-local.md) |
| 상장 생성기 | `award-local` | 문서 | 상장·표창장·감사장 PDF/PNG, 일괄 발급 | [award-local.md](award-local.md) |
| 논문 리뷰 | `review-local` | 문서 | 투고 전 원고 사전 심사 | [review-local.md](review-local.md) |
| 주간보고 | `weekly-local` | 문서 | 주간보고 작성·취합, HWPX·DOCX | [weekly-local.md](weekly-local.md) |
| 글쓰기 코치 | `coach-local` | 문서 | 쓰는 동안 글 종류에 맞춰 고칠 곳 제안 | [coach-local.md](coach-local.md) |
| Meeting | `meeting-local` | 회의 | 녹음 → 화자 분리 → 회의록 | [meeting-local.md](meeting-local.md) |
| TTS | `tts-local` | 음성 | 한국어 음성 합성 (OpenAI 음성 API 호환) | [tts-local.md](tts-local.md) |
| Diagram | `diagram-local` | 그림 | 말로 설명하면 다이어그램 | [diagram-local.md](diagram-local.md) |
| Sketch | `sketch-local` | 그림 | 손그림 사진 → 논문 figure 스타일 | [sketch-local.md](sketch-local.md) |
| CAD Agent | `agent-cad-local` | 그림 | 3D 부품·2D 판재·배치도, DXF 수정 | [agent-cad-local.md](agent-cad-local.md) |
| 그림 생성 | `image-local` | 그림 | 말로 그리고 고치는 그림 | [image-local.md](image-local.md) |
| SQL (demoDB) | `sql-local` | 데이터 | 자연어 → SQL → 표·차트 | [sql-local.md](sql-local.md) |
| 그래프 | `chart-local` | 데이터 | 표 → 그래프 후보 → 편집 가능한 문서 차트 | [chart-local.md](chart-local.md) |
| 그래프 디지타이저 | `digitizer-local` | 데이터 | 그래프 그림 → 숫자 데이터 | [digitizer-local.md](digitizer-local.md) |
| 단백질 구조 예측 | `protein-local` | 연구 | 서열 → 3D 구조·pLDDT·PAE | [protein-local.md](protein-local.md) |
| 배터리 | `battery-local` | 연구 | PyBaMM 성능·수명 예측 | [battery-local.md](battery-local.md) |
| 차폐 계산기 | `shield-local` | 원자력 | 방사선 차폐·선량 간이 계산 | [shield-local.md](shield-local.md) |
| 팀 밸런스 맵 | `saju-local` | 재미 | 사주·MBTI 협업 궁합 | [saju-local.md](saju-local.md) |
| Avatar | `avatar-local` | 토이 | 캐릭터 그림 → 말하기·움직이기 영상 | [avatar-local.md](avatar-local.md) |
| 파동이 | `padong-local` | 토이 | 캐릭터 3D·2D, 말로 시키면 움직임 | [padong-local.md](padong-local.md) |

## 6. 화면이 하는 일 — 새 화면이 다시 만들어야 하는 것

AI 추론·시뮬레이션·문서 생성·저장·권한 확인은 **모두 서버(API)** 가 합니다. 그렇다고 지금 화면이 보여 주기만 하는 것은 아닙니다. 아래는 **브라우저에서 실제로 계산하는 것**이라, 새 화면을 만들 때는 같은 라이브러리로 옮기거나 다시 만들어야 합니다. 도구별 자세한 내용은 각 문서의 "화면만의 것" 절에 있습니다.

### 모든 도구 공통
- 파일 → base64 변환 후 업로드, 진행 상황 스트림(`fetch` + `response.body.getReader()`) 읽기, 경과 시간·진행률 표시
- 입력값·옵션 기억(브라우저 `localStorage`), 결과 탭·비교 화면·복사·다운로드 파일 이름
- 포털이 첫 화면 HTML 에 끼워 넣는 것: `?` 사용법, 테마, 파동이 펫, 둘러보기(새 화면을 따로 띄우면 붙지 않음)

### 브라우저에서 돌아가는 연산

| 종류 | 도구 | 화면이 하는 일 | 쓰는 것 |
|---|---|---|---|
| 3D 렌더링(WebGL) | padong-local | 캐릭터 3D·2D 동작·표정·옷·카메라·그림자, 초당 30프레임 제한. API 는 동작 **이름**만 줌 | Three.js (`static/`) |
| | agent-cad-local | GLB 3D 모델 보기, SVG 도면 확대·이동, 보고서 표시 | Three.js + GLTFLoader + OrbitControls (`static/`) |
| | protein-local | PDB 분자 구조 그리기, pLDDT 색(≥90/70/50), 사슬 색 | 3Dmol.js (`static/`) |
| 그래프 배치 | diagram-local · sketch-local | Mermaid 코드 → 도형 위치·선 경로 계산·그리기, 그리기 실패 시 `api/repair` 자동 1회(sketch) | Mermaid (`static/mermaid.min.js`) |
| 캔버스 | diagram · sketch | SVG → 2배 PNG 변환, SVG 파일 생성 | Canvas 2D |
| | sketch-local | 올리기 전 사진을 1600px 로 줄이기 | Canvas 2D |
| | protein-local | PAE 히트맵 그리기·PNG 저장, pLDDT 그래프 | Canvas 2D |
| 수치 계산 | digitizer-local | 축 보정 행렬, 픽셀↔데이터 좌표 변환, 표시 자릿수, 점 찍기·편집·제외 영역·스포이트·돋보기 (서버도 같은 규칙으로 따로 계산 — 내보내기는 서버 값) | 자체 JS |
| | shield-local | 단위 환산(Sv/h→μSv/h, cm→mm 등)·서식, 두께·거리 그래프, PDF 저장(브라우저 인쇄) | 자체 JS |
| | battery-local | 그래프 축·눈금·SVG 그래프와 SVG 저장, 비교 시나리오·예시 프로파일 만들기, 작업 상태 폴링(4초·1.5초) | 자체 SVG |
| 글 비교 | coach-local | 바뀐 문단 찾기, 멈춤 감지(약 1초) 후 검사, 결과 캐시, 밑줄·카드, 적용·무시·되돌리기, 제안 위치 다시 맞추기, .txt·.md 내보내기 | 자체 JS |
| | mail-local | 문장별 적용·되돌리기(이 선택은 서버에 저장 안 됨), 보내는 사람 정보 | 자체 JS |
| | meeting-local | 윤문 전후 어절 단위 비교 표시 | 자체 JS |
| 편집 | chart-local | 열 이름·단위·형식 편집, 표 뒤집기 → `api/suggest` 다시 부름 | 자체 JS |
| | weekly-local | 질문 카드 진행, 항목 끌어 옮기기·들여쓰기, 압축 뒤 되살리기, 미확인 약어 수 | 자체 JS |
| 음성 | padong-local | TTS 음량 분석으로 입 모양 맞추기, TTS 서버가 없을 때 브라우저 음성 합성으로 대신 | Web Audio, Web Speech |
| 파일 생성 | padong-local | .glb 3D 파일·PNG·표정 시트 만들기 | Three.js 내보내기 |

### 거의 보여 주기만 하는 도구
humanize-kr · kordoc · notebook · writer · meeting · tts · image · review · sql · saju · award · avatar — 서버가 준 결과를 그리기만 합니다. 새 화면으로 바꾸기 가장 쉽습니다.

### 아직 쓰지 않는 것
Web Worker(무거운 계산 분리), WebGPU, WebAssembly, 브라우저 안 AI 모델(WebLLM 등), Service Worker(오프라인). 지금 화면의 계산은 모두 화면과 같은 흐름에서 돌아갑니다.

### 옮길 때 주의
- **notebook**: 노트 본문의 `<!-- audio: … -->` 주석을 지우지 말고 그대로 저장해야 팟캐스트 음성 연결이 유지됩니다(지금 화면은 편집 후 저장하면 끊기는 버그가 있음).
- **digitizer**: `api/export` 에 화면이 만든 표를 보내도 서버는 쓰지 않고 다시 계산합니다.
- **스트리밍 응답**: 도중 오류도 HTTP 200 으로 오고, 오류는 스트림 안 `error` 이벤트로만 옵니다.
- **다크 테마 내보내기**: diagram·chart·protein 은 화면이 어두워도 내보내는 파일은 흰 종이로 만듭니다(화면 로직). 새 화면도 같은 원칙을 지켜 주세요.

## 7. 새 화면을 만들 때 부탁

- 서버가 계산한 결과(숫자·문장·판정)를 화면에서 고쳐 보이게 하지 말고(예: 반올림 바꾸기), 필요한 건 API 변경으로 요청해 주세요. 위 6절의 화면 계산은 지금 화면과 같은 규칙으로 옮겨 주세요.
- 문서와 실제 응답이 다르면 응답이 맞습니다 — 알려 주시면 문서를 고칩니다.
- 사진·그림·그래프·문서 미리보기의 색은 내용이므로 바꾸지 마세요.
- 저작자 표기와 "내부 시험용 · 배포용 아님" 표기는 남겨 주세요.

## 8. OpenAPI

위 문서와 도구별 문서를 **OpenAPI 3.1** 로 옮긴 파일이 [`openapi/`](openapi/) 에 있습니다. 경로는 모두 포털 기준(`/api/…`, `/t/<도구>/api/…`), `servers: [{"url": "/"}]` — 같은 출처 전제(3절).

| 파일 | 내용 |
|---|---|
| `openapi/openapi.json` | 포털 + 도구 24개 전체(저장소용) |
| `openapi/openapi.handoff.json` | 외부 전달용 합본 — 개인용 도구(saju-local) 제외 |
| `openapi/portal.json` | 포털 자체 API(로그인·도구 목록·계정·승인·관리) |
| `openapi/<도구 dir>.json` | 도구 하나씩(공통 components 포함, 단독으로 쓸 수 있음) |

타입 생성 예:

```bash
npx openapi-typescript docs/api/openapi/openapi.json -o src/api/schema.d.ts      # 타입만 (openapi-fetch 와 같이)
npx orval --input docs/api/openapi/openapi.json --output src/api/client.ts        # 클라이언트 함수까지
npx @redocly/cli lint docs/api/openapi/openapi.json                               # 검사
```

읽는 법:
- **보안**: `cookieAuth`(쿠키 `agsid`)가 전역. 로그인 전에 열린 경로(`/api/login`·`/api/signup`·`/api/logout`·`/api/depts`·`/api/auth`·`/api/version`·`/api/landing`·`/login`·`/logout`)만 `security: []`. 오류 `Error` 의 `code` enum 과 공통 응답 `TooManyRequests`(429, `Retry-After`)도 있습니다. 도구 태그의 `x-health-path` 는 표준 헬스 `/t/<도구>/healthz`.
- **오류**: 공통 `Error` = `{"error": "…"}`, 공통 응답 `BadRequest`(400)·`Unauthorized`(401, `login` 포함)·`Forbidden`(403)·`NotFound`(404)·`Conflict`(409)·`PayloadTooLarge`(413)·`ServerError`(500)·`BadGateway`(502 = 도구 꺼짐 → `POST /api/start`). 도구 경로에는 401·502 가 모두 붙어 있습니다. 모양이 다른 곳(tts-local POST 의 `{"error": {"message", "type"}}` = `OpenAIError`)은 그대로 적었습니다.
- **스트리밍**: `text/event-stream`(SSE)·`application/x-ndjson` 응답은 스키마가 문자열이고, 이벤트별 JSON 모양은 미디어 타입의 **`x-stream-events`** 목록(`[{name, schema}]`)과 operation description 에 있습니다. 생성기는 이 확장을 무시하므로, 이벤트 타입은 `components["schemas"]["<도구>…Event"]` 를 직접 가져다 쓰세요. **도중 오류도 HTTP 200 으로 오고 스트림 안 `error` 이벤트로만 옵니다.**
- **파일**: 업로드는 대부분 JSON 안 base64(`contentEncoding: base64`), 상한을 넘으면 413 — 상한은 각 operation description. 내려받기는 실제 `Content-Type`(`format: binary`) + `Content-Disposition` 헤더.
- **확장 필드**: `x-permission`(`admin` 관리자 · `dept-head` 부서장 · `creator` 만든 사람 · `self` 본인), `x-shared-data`(여러 사람이 같이 보는 데이터), `x-gpu`·`x-llm`·`x-long-running`(버튼 연타 막기). 도구 태그(와 도구별 파일의 `info`)에는 등록용 `x-tool-id`·`x-name`·`x-kind`(agent/tool)·`x-category`·`x-entry`·`x-health-path`·`x-docs-input`·`x-external-network`·`x-response-style` 이 있습니다.
- description 에 **"코드 확인 필요"** 라고 적힌 스키마는 문서·코드로 모양을 확정하지 못한 곳(`additionalProperties: true`)입니다. 실제 응답이 다르면 알려 주세요.
- 화면 HTML(그 밖의 GET → `ui.html`)과 정적 파일은 넣지 않았습니다.

## 9. 보안 모델 — 누가 무엇을 믿나

새 화면을 만드는 분과 점검하는 분이 같은 그림을 보도록 정리합니다. 근거 코드: `fastportal/`(포털), 각 도구의 `portal_auth.py`(원본 `sdk/portal_auth.py`).

```
브라우저 ──(쿠키 agsid, 같은 출처 또는 허용한 CORS 출처, Origin 검사)──▶ 포털
포털 ──(X-Agent-User + X-Agent-Auth 서명, 쿠키 없음)──▶ 도구        도구 ──(같은 서명)──▶ 다른 도구(tts·kordoc 등)
도구 ──(X-Portal-Jobs-Token, 도구별)──▶ 포털 /api/internal/jobs
```

| 경계 | 지키는 방법 |
|---|---|
| 브라우저 → 포털: 누구인가 | 세션 쿠키 `agsid`(HttpOnly, 서버에는 sha256 만, 30일·12시간). PIN 은 PBKDF2-SHA256 60만 회 + 사용자별 salt. 로그인 잠금: (아이디, 접속지) 10분 5회 · 접속지 10분 30회 · 아이디 전체 10분 20회 · PIN 해시 동시 4개 · 가입 접속지별 시간당 5회 |
| 브라우저 → 포털: 우리 화면에서 왔나 | 상태를 바꾸는 요청은 Origin/Referer 가 같은 출처·허용 목록이어야 함(3절). CORS 는 허용 목록이 비면 아예 꺼짐 |
| 포털 → 도구: 사용자 | 포털이 사용자 헤더를 지우고 다시 붙임 + **서명** `X-Agent-Auth: v1.<unix초>.<hex>`, hex = HMAC-SHA256(도구 키, `v1|<초>|<X-Agent-User>`), ±5분. 도구 키(`PORTAL_TOOL_KEY`)는 포털 비밀값에서 만들어 도구를 띄울 때 넘김(다른 컨테이너 도구는 `python -m fastportal.jobs toolkey` 값을 env 로) |
| 도구가 받는 요청 | 키가 있으면 서명이 맞는 요청만(아니면 401 `code: unauthenticated`). 키가 없으면(포털 없이 띄움) 127.0.0.1 요청만(아니면 403). 예외 `GET /healthz`(데이터 없음). 바인드 기본 127.0.0.1 |
| 도구 → 포털 응답 | 도구가 포털 쿠키(`ag…`)를 덮는 Set-Cookie 는 버림. `X-Content-Type-Options: nosniff`·`Referrer-Policy: same-origin`·`X-Frame-Options`·CSP `frame-ancestors` 를 붙임. 도구 API 가 주는 HTML·SVG·XML(사용자 내용일 수 있음)은 CSP `sandbox` — 그 안의 스크립트는 포털 쿠키·API 에 못 닿음 |
| 도구 → 포털 작업 알림 | 도구별 HMAC 토큰 + 접속지(127.0.0.1·tools.json host·`PORTAL_JOBS_ALLOW`) |
| 관리자 | 계정 `AGENT_OWNERS`, 또는 서버 안(127.0.0.1)에서 비밀 헤더 `X-Local-Owner`(토큰 파일, 600). 관리 API 는 서버에서 권한 확인. 숨긴 도구는 관리자만 쓰고 켬 |
| 비밀 파일 | `.local_owner_token`·`.portal_jobs_secret`(권한 600) — 기본 `_data/`, 도구가 같은 데이터 폴더를 붙이는 컨테이너 구성에서는 `PORTAL_SECRETS_DIR`(포털만 붙는 볼륨, 700). 값은 로그·응답에 찍지 않음. 백업은 `umask 077` |
| 오류 응답 | 내부 경로·주소·스택은 응답에 넣지 않음(포털: 로그에만, 도구: `portal_auth.scrub`) |
| 감사 기록 | `audit` 표: 로그인·로그인 실패·가입·도구 시작/중지/등록·관리 작업(부서·부서장·PIN 초기화·정지·비활성화·익명화)·승인 |

**알려진 한계(설계상, 다음 단계)**
- 모든 도구가 포털과 같은 출처(`/t/<도구>/`)라, 도구 첫 화면의 XSS 는 포털 API 를 부를 수 있습니다(도구 화면이 인라인 스크립트를 써서 `script-src` 를 조이지 못함). 근본 대책은 도구별 출처 분리(서브도메인) — 지금은 sandbox·nosniff·도구 쪽 입력 정리로 줄임.
- 관리자 계정이 여럿이어도 도구에는 `X-Agent-User: owner` 하나로 갑니다(도구 쪽 '주인' 작업 폴더 공유).
- HTTP 로 운영 중이면 쿠키에 Secure 가 없습니다 — 폐쇄망 이전 때 HTTPS(`TLS_CERT·TLS_KEY` 또는 앞단 HTTPS + `PORTAL_COOKIE_SECURE=1`) 권장.
- 실시간 알림(SSE)은 포털 프로세스 하나 전제(워커 1개).

## 10. 버전·호환

- `GET /api/version` 의 `api`(지금 `2026.10.14`) = 이 문서와 `openapi/*.json` 의 `info.version`. **응답 모양을 바꾸면(필드 삭제·이름 변경·의미 변경) 올립니다.** 필드를 더하는 것은 호환(화면은 모르는 필드를 무시해 주세요).
- 경로를 없애거나 바꿀 때는 최소 한 판 동안 예전 경로를 같이 둡니다(예: `GET /logout` ↔ `POST /api/logout`, `GET /api/account/notices` ↔ `POST`).
- `build` = 포털 저장소 `git describe`(태그-커밋) — 문제를 알릴 때 같이 적어 주세요.
- 바뀐 점(2026.10.14): `POST /api/logout`·`GET /api/version`·`GET /api/landing`·`POST /api/account/notices` 추가, 오류 `code`, 로그인 잠금 429, PUT·DELETE·PATCH·HEAD 프록시, CSRF 검사, 도구 서명·`/healthz`, CORS 설정(기본 꺼짐).
