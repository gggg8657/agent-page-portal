# 에이전트 포털 API 안내 — 새 화면(프론트엔드)을 붙이려는 분께

포털의 모든 도구는 **화면(`ui.html`)과 처리(`app.py`)가 나뉘어 있고, 둘 사이를 JSON HTTP API 로 주고받습니다.**
지금 화면도 이 API 만 씁니다. 따라서 화면을 새로 만들어도 같은 API 를 부르면 기능은 그대로 쓸 수 있습니다.

- 이 문서: 공통 규칙, 로그인, 연결 방법, 포털 자체 API
- 도구별 API: 아래 [도구 목록](#도구-목록)의 각 파일

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
도구 app.py  http://127.0.0.1:<포트>   ← 서버 안에서만 열림. 바깥에서는 반드시 포털을 거침
   │  필요하면
   ▼
로컬 LLM(Ollama) · GPU 모델
```

- **주소 규칙**: 도구 문서의 경로 `api/xxx` 는 포털에서 `/t/<도구 dir>/api/xxx` 입니다.
  예: 사주 도구의 `api/graph` → `GET /t/saju-local/api/graph?dept=…`
- **도구 포트로 직접 붙지 마세요.** 도구는 127.0.0.1 에만 열려 있고, 누가 보냈는지는 포털이 붙이는 `X-Agent-User` 로만 정해집니다.
  화면에서 `X-Agent-User`·`X-User` 헤더를 보내도 포털이 지웁니다.
- **사용자별 데이터**: 도구는 `X-Agent-User` 로 사람마다 따로 저장합니다(기록·업로드 등). 일부는 일부러 공유합니다(주간보고 제출함, kordoc 양식, SQL 데모 DB, CAD 지식 보정, 사주 팀 지도) — 도구별 문서에 표시.

## 2. 로그인

| 호출 | 설명 |
|---|---|
| `POST /api/login` `{"id": "아이디", "pin": "PIN", "remember": true}` | 성공 `{"ok": true, "id": …}` + 쿠키 `agsid` (HttpOnly, SameSite=Lax). `remember`(로그인 유지, 없으면 `true`): `true` = 30일, 쓰는 동안 하루에 한 번 연장 · `false` = 쿠키에 Max-Age 없음(브라우저 닫으면 끝) + 서버 만료 12시간, 연장 없음. 틀리면 400 `{"error": …}`. 10분에 5번 틀리면 10분 잠김 |
| `POST /api/signup` `{"id", "name", "pin", "dept"}` | 가입. id 는 영문 소문자·숫자·`_.-` 2~32자, PIN 은 숫자 4~8자리. 부서는 `GET /api/depts` 목록 중 하나(부서장 승인 뒤 반영) |
| `GET /login` | 로그인 화면(랜딩). 이미 로그인했으면 `next`(같은 사이트 경로만, 아니면 `/`)로 302 |
| `GET /logout` | 세션 삭제 후 `/login` 으로 302 |
| `GET /api/me` | 지금 사용자 `{"id", "name", "role", "dept", "owner", "onboarded", …}` — `onboarded`: 첫 로그인 둘러보기를 봤거나 건너뜀 |

- 로그인 안 된 요청: API 는 **401** `{"error": "로그인이 필요합니다", "login": "/login"}`, 화면 요청(`Accept: text/html` 인 GET)은 `/login?next=…` 로 302.
- 쿠키가 HttpOnly 라 JS 에서 읽을 수 없습니다. **같은 출처에서 `fetch(…, {credentials: "same-origin"})`(기본값)로 부르면 브라우저가 알아서 붙입니다.**
- 서버 안(127.0.0.1)에서 쿠키 없이 부르면 "서버 주인(owner)"으로 처리됩니다(관리·시험용).

## 3. 새 화면을 연결하는 방법

포털은 **CORS 헤더를 내보내지 않고**, 로그인 쿠키는 **SameSite=Lax** 입니다. 그래서 다른 주소(다른 포트·도메인)에 올린 화면에서 포털 API 를 바로 부르면 막히거나 로그인이 안 됩니다. 화면이 포털과 **같은 출처**로 보이게 하는 것이 원칙입니다.

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
- 포털에 새 화면용 정적 경로를 추가하기.

다른 서버에 따로 올려야 한다면 포털에 CORS·토큰 방식을 따로 열어야 하니 먼저 말해 주세요.

### 포털이 도구 화면에 자동으로 넣는 것
도구의 **첫 화면 HTML**(`/t/<도구>/`)에는 포털이 사용법 `?` 버튼, 화면 테마(boot.js·CSS), 저작자 표기, 파동이 펫을 끼워 넣습니다. API 응답(JSON·파일)은 손대지 않습니다.
새 화면을 따로 띄우면 이것들은 붙지 않으니, 필요하면 `GET /api/guide/<도구>`(사용법 마크다운)를 직접 가져다 쓰세요.

## 4. 공통 규칙

- **요청**: 대부분 `POST` + JSON 본문(`Content-Type: application/json`). 파일 업로드는 도구마다 다릅니다(JSON 안 base64 또는 multipart) — 도구별 문서 참고.
- **응답**: JSON. 파일 내려받기는 해당 `Content-Type` 과 `Content-Disposition` 으로 옵니다.
- **오류**: 본문 `{"error": "사람이 읽을 수 있는 한국어 설명"}` + 상태 코드

  | 코드 | 뜻 |
  |---|---|
  | 400 | 입력이 잘못됨(본문이 JSON 객체가 아님, 필수 값 없음 등) |
  | 401 | 로그인 필요 |
  | 403 | 권한 없음(관리자·부서장 전용) |
  | 404 | 없는 경로·도구 |
  | 409 | 이미 실행 중인 도구를 다시 시작하려 함(관리자만 가능) |
  | 502 | **도구가 꺼져 있음** → `POST /api/start {"dir": "<도구>"}` 로 켠 뒤 다시 시도 |

- **오래 걸리는 작업**: 포털은 요청 하나를 최대 1시간 기다립니다. 영상·구조 예측처럼 긴 작업은 도구마다 ① 스트리밍(SSE·줄 단위 JSON)으로 진행 상황을 흘리거나 ② 작업 id 를 받고 상태를 폴링합니다 — 도구별 문서의 "오래 걸림" 표시를 보세요.
  1분 넘게 걸린 POST 는 포털이 기록해서 `GET /api/events` 로 알려 줍니다(파동이 펫의 "작업 끝났어요!" 알림).
- **스트리밍**: 포털은 응답을 버퍼링하지 않고 그대로 흘립니다. `fetch` 의 `response.body.getReader()` 나 `EventSource` 로 읽으면 됩니다.
- **GPU·LLM**: 이 표시가 있는 호출은 서버 GPU 를 씁니다. 여러 사람이 동시에 부르면 줄을 서니, 버튼 연타를 막아 주세요. `GET /api/gpu` 로 GPU 여유를 볼 수 있습니다.

## 5. 포털 자체 API

| 메서드·경로 | 하는 일 | 권한 |
|---|---|---|
| `GET /api/tools` | 도구 목록 `{"tools": [{dir, name, title, desc, group, tags, port, up(켜짐), url, usage}], "rank", "posts", "me"}` | 로그인 |
| `GET /api/me` | 지금 사용자 | 로그인 |
| `GET /api/gpu` | GPU 사용 현황 | 로그인 |
| `GET /api/events?since=<id>` | 1분 넘게 걸려 끝난 내 작업 `{"events": [{id, dir, name, ok, sec, t}], "last"}` | 로그인 |
| `GET /api/guide/<도구>` | 도구 사용법(마크다운 텍스트) | 로그인 |
| `GET /api/readme/<도구>` | 도구 README(마크다운 텍스트) | 로그인 |
| `GET /api/depts` | 부서 목록 | 누구나 |
| `POST /api/start` `{"dir"}` | 꺼진 도구 켜기(켜진 도구 재시작은 관리자만) | 로그인 |
| `POST /api/stop` `{"dir"}` | 도구 끄기 | 관리자 |
| `POST /api/open` · `/api/like` `{"dir"}` | 사용·좋아요 기록(순위에 반영) | 로그인 |
| `POST /api/post` · `/api/post_like` | 노하우 게시판 글쓰기·좋아요 | 로그인 |
| `POST /api/account/theme` `{"theme"}` | 화면 테마 저장(`classic`·`trendy`·`v2` × `light`·`dark`·`auto`) | 로그인 |
| `POST /api/pin` `{"pin", "new"}` | PIN 바꾸기(다른 기기 세션은 끊김, 지금 세션의 '로그인 유지'는 그대로) | 로그인 |
| `POST /api/account/onboarded` `{"done": true}` | 첫 로그인 둘러보기 마침·건너뜀 기록(`/api/me` 의 `onboarded`) | 본인 |
| `/api/account…` · `/api/approvals…` · `/api/admin…` | 계정 설정·부서 승인·관리 | 본인·부서장·관리자 |

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
