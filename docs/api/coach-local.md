# 글쓰기 코치 API (`coach-local`)
쓰는 동안 문단별로 고칠 곳(맞춤법·문체·형식·명료·논리·사실)을 제안하고, 다 쓴 글을 전체 검토하는 글쓰기 코치. 글 종류는 "페르소나"로 고름.
포털 경로 접두어: `/t/coach-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/coach-local/api/check`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 + 서버 기본 모델 | 아니오 | 아니오 |
| GET | `api/models` | LLM 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/personas` | 페르소나 목록(기본 + 내 것) | 아니오 | 예(내 것) |
| POST | `api/personas` | 페르소나 저장(새로/고치기) | 아니오 | 예 |
| POST | `api/personas/delete` | 내 페르소나 삭제 | 아니오 | 예 |
| POST | `api/check` | 문단 검사 → 제안 목록 | `pass` 가 `rules` 면 아니오(수십 ms), `spell`·`style` 은 LLM(수 초) | 아니오 |
| POST | `api/profile` | 글 파악(문체·용어·숫자·약어 등) | `llm: true` 면 LLM | 아니오 |
| POST | `api/cancel` | 진행 중인 check·profile 취소 | 아니오 | 아니오 |
| POST | `api/review` | 글 전체 검토 | 예 — LLM, 보통 5~15초 | 아니오 |
| GET | `api/docs` | 저장한 문서 목록 | 아니오 | 예 |
| GET | `api/docs/<id>` | 문서 하나(판 기록 포함) | 아니오 | 예 |
| POST | `api/docs` | 문서 저장(자동 저장·판 남기기) | 아니오 | 예 |
| POST | `api/docs/delete` | 문서 삭제 | 아니오 | 예 |
| GET | `api/import_ok` | HWP·HWPX·PDF 가져오기 가능 여부 | 아니오 | 아니오 |
| POST | `api/import` | 파일 → 글(텍스트) | 조금(kordoc 변환, 최대 180초) | 아니오 |
| POST | `api/export` | 글 → .docx 내려받기 | 아니오 | 아니오 |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

모든 POST 는 JSON 본문 + JSON 응답(한 번에). **스트리밍 없음.**

## 엔드포인트

### GET api/health · GET api/models
- `api/health` → `{"ok": true, "model": "gemma4:31b"}` (기본 모델 이름은 여기서 얻을 수 있음)
- `api/models` → 문자열 배열. 오류 시 500 `{"error": "..."}`

### GET api/personas
- 응답: 기본 페르소나(`builtin: true`, 공문서·편지·논문·사설·칼럼·메시지·메일·보고서·보도자료·자기소개서 8개) 다음에 사용자 것
```json
[{"id": "gongmun", "name": "공문서", "audience": "결재권자·수신 기관 담당자", "tone": "간결한 개조식 ...",
  "rules": ["두괄식: 목적·요청을 첫 항목에", "..."], "avoid": ["..."], "example": "...", "builtin": true},
 {"id": "u-1a2b3c", "name": "내 보고서", "audience": "", "tone": "", "rules": [], "avoid": [], "example": "", "updated": "..."}]
```

### POST api/personas
- 요청: `{"id": "u-xxxxxx"(고칠 때, 없으면 새로), "name": "필수 30자", "audience": "200자", "tone": "300자", "rules": ["..."] 또는 "줄바꿈 문자열", "avoid": 같은 형식, "example": "2000자"}`
  - `rules`·`avoid` 는 최대 20줄, 줄당 200자로 잘림.
- 응답: 저장된 페르소나(새 id·`updated` 포함)
- 오류: 이름 없음 → 400 `{"error": "페르소나 이름이 필요합니다"}`
- 메모: 기본 페르소나(`gongmun` 등)의 id 로 보내면 고쳐지지 않고 **새 사용자 페르소나**(`u-...`)로 저장됨.

### POST api/personas/delete
- 요청 `{"id": "u-1a2b3c"}` → `{"ok": true}`. 기본 페르소나·없는 id → 404 `{"error": "없음"}`

### POST api/check
문단 검사. 두 가지 입력 방식이 있음.

**(가) 화면 방식 — 글 전체 + 검사할 줄 번호** (문맥·규칙 검사 포함, 권장)

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `text` | string | 예 | 글 전체(줄 = 문단). 200,000자까지 |
| `idx` | int[] | 예 | 검사할 줄 번호(0부터, 빈 줄 제외). 한 번에 최대 20개 |
| `pass` | string | 아니오 | `spell`(맞춤법, LLM) · `style`(문체·형식·명료·논리·사실, LLM) · `rules`(규칙 검사, LLM 없음). 없으면 셋 다 |
| `persona` | object | 아니오 | 페르소나 **객체 전체**(id 아님). 없으면 공문서 |
| `purpose` | string | 아니오 | 글의 목적 한 줄 |
| `title` | string | 아니오 | 제목 |
| `profile` | object | 아니오 | `{"llm": api/profile 의 llm 결과, "user": {"style": "합니다체 등", "terms": "쉼표로 고정 용어"}}` |
| `model` | string | 아니오 | |
| `minimal` | bool | 아니오 | true 면 문장 통째 바꾸기 대신 최소 조각만(글 종류가 안 맞아 보일 때) |
| `rid` | string | 아니오 | 요청 id — `api/cancel` 로 취소할 때 씀 |

- 응답
```json
{"s": [{"i": 3, "o": "시일내에", "n": "시일 내에", "r": "띄어쓰기", "c": "맞춤법", "at": 12},
       {"i": 5, "o": "따라서", "n": "한편", "r": "앞 문장과 인과 아님", "c": "논리", "at": 0, "src": "rule"}],
 "dropped": 2, "profile": {"style": "합니다체", "styles": {"합니다체": 8}, "abbr": [...], "numbers": [...], "variants": [...]},
 "ms": 2140, "partial": "한쪽 검사 실패 사유(있을 때만)"}
```
- 제안 필드: `i` 줄 번호, `o` 원문 조각(그 줄에 글자 그대로 있음), `n` 바꿀 말(`o` 와 같으면 "확인 메모"), `r` 이유, `c` 분류 키(`맞춤법`·`문체`·`형식`·`명료`·`논리`·`사실`), `at` 줄 안 시작 위치(문자 인덱스), `src: "rule"` 이면 규칙 검사에서 나온 것
- `dropped`: 자리를 못 찾거나 겹쳐서 버린 제안 수

**(나) 단순 방식 — 문단 목록**
- 요청: `{"units": [{"id": "p0", "text": "문단"}, ...], "persona": {...}, "purpose": "", "pass": "spell"|"style", "model": "", "minimal": false, "rid": ""}` (최대 20개, 문단당 3,000자)
- 응답: `{"s": [{"p": "p0", "o", "n", "r", "c", "at"}], "dropped": 0, "ms": ...}` — 줄 번호 대신 `p`(보낸 id). 규칙 검사는 없음.

- 취소되면 HTTP **499** `{"cancelled": true}`
- 메모: `spell` 과 `style` 은 화면이 따로 불러 먼저 온 것부터 보여 줌. 둘 다 부르면 서버가 동시에 돌려 합침. 같은 문단을 다시 묻지 않는 캐시는 화면 쪽에 있음.

### POST api/profile
- 요청: `{"text": "글 전체", "llm": true|false, "model": "", "rid": ""}`
- 응답: 규칙으로 뽑은 값은 늘, `llm: true` 이고 글이 40자 이상이면 `llm` 이 붙음
```json
{"style": "합니다체"|"해요체"|"개조식"|"~다체"|"섞임"|"", "styles": {"합니다체": 8, "해요체": 1},
 "abbr": [{"a": "LSTM", "def": true, "at": 120}], "numbers": ["예산 1,500만 원", "10. 12.(월)"],
 "variants": [{"spaced": "고속 카메라", "joined": "고속카메라", "first": "spaced"}],
 "llm": {"style": "합니다체", "terms": ["..."], "entities": ["..."], "ask": "핵심 요청 한 줄", "summary": "지금까지 1~2줄"},
 "ms": 3200}
```
- 취소되면 499 `{"cancelled": true}`

### POST api/cancel
- 요청 `{"rids": ["abc123", ...]}`(최대 50) → `{"ok": true}`
- 메모: 포털 프록시는 브라우저 연결 끊김을 도구에 전하지 않으므로, fetch 를 abort 한 뒤 이 API 로 서버 쪽 LLM 작업도 멈춰야 GPU 를 아낌. 취소 표시는 같은 사용자 + 같은 rid 에만 적용, 5분 뒤 지워짐.

### POST api/review
- 요청: `{"text": "글 전체(12,000자까지)", "persona": {...}, "purpose": "", "model": ""}`
- 응답
```json
{"kind": "공문서", "summary": "총평 2문장", "good": ["잘된 점"],
 "issues": [{"q": "관련 원문 일부(없으면 빈 문자열)", "t": "문제", "f": "고칠 방향·예시", "c": "형식"}], "ms": 8000}
```
- `kind`: 이 글에 가장 맞아 보이는 기본 페르소나 이름(못 고르면 빈 문자열) — 화면은 이 값(과 화면 자체의 문장 끝 추정)이 지금 페르소나와 다르면 "글 종류를 바꿀까요" 알림을 띄우고, 그동안 `check` 에 `minimal: true` 를 보냄.
- 오류: 빈 글 → 400 `{"error": "검토할 글이 없습니다"}`

### GET api/docs · GET api/docs/<id>
- `api/docs` → `[{"id": "20261013-101500-ab12", "title": "...", "persona": "gongmun", "updated": "...", "chars": 1520, "versions": 3}]` (최근 수정 순, 최대 200)
- `api/docs/<id>` → `{"id", "created", "title", "text", "persona"(id), "purpose", "profile_user": {"style", "terms"}, "updated", "versions": [{"ts", "text"}]}`. 없으면 404.

### POST api/docs
- 요청: `{"id": "기존 id 또는 빈 값", "title": "", "text": "", "persona": "페르소나 id", "purpose": "", "profile_user": {"style": "", "terms": ""}, "snapshot": false}`
- 응답: `{"id", "title", "updated", "versions": 판 수}` — 처음 저장이면 새 id 를 돌려줌(이후 이 id 로 계속 저장)
- 메모: 같은 id 면 덮어씀. 내용이 바뀌었고 `snapshot: true` 이거나 마지막 판이 10분 넘었으면 판을 하나 더 남김(최대 30개). 제목이 비면 첫 줄 40자.

### POST api/docs/delete
- 요청 `{"id": "..."}` → `{"ok": true}`, 없으면 404

### GET api/import_ok · POST api/import
- `api/import_ok` → `{"kordoc": true|false}` — true 면 `.hwp .hwpx .pdf .hml` 도 받음
- `api/import` 요청 `{"file_name": "글.docx", "file_b64": "<base64>"}` → `{"text": "추출한 글", "name": "글.docx"}`
  - 늘 되는 형식: `.txt .md`(UTF-8 또는 CP949), `.docx`
- 오류: 400 `{"error": "지원하지 않는 형식: .xlsx (...)"}`, 변환 실패 500

### POST api/export
- 요청 `{"title": "제목", "text": "본문"}`
- 응답: `.docx` 바이너리. `Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `Content-Disposition: attachment; filename*=UTF-8''<제목>.docx`
- 메모: 줄 = 문단, 맑은 고딕. POST 라서 `<a href>` 로는 못 받음 → `fetch` 후 `blob()` 으로 저장.

## 흐름 예
```js
const base = '/t/coach-local/';
const api = (p, b) => fetch(base + 'api/' + p, b === undefined ? {} : {method: 'POST',
  headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b)}).then(r => r.json());

const personas = await api('personas'); const persona = personas[0];
// 사용자가 3번 줄을 고치고 멈췄을 때: 규칙 → 맞춤법 → 문체 순으로 따로
const body = {text, idx: [3], persona, purpose: '장비 구매 승인 요청', profile: {llm: {}, user: {style: '', terms: ''}}};
const rules = await api('check', {...body, pass: 'rules'});
const rid = Math.random().toString(36).slice(2);
const ctrl = new AbortController();
const spell = fetch(base + 'api/check', {method: 'POST', signal: ctrl.signal, headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({...body, pass: 'spell', rid})}).then(r => r.json());
// 사용자가 다시 타이핑하면: ctrl.abort(); api('cancel', {rids: [rid]});

// 제안 적용: 줄 i 의 at 위치에서 o 를 n 으로
const L = text.split('\n'); const s = rules.s[0];
L[s.i] = L[s.i].slice(0, s.at) + s.n + L[s.i].slice(s.at + s.o.length);

// 내보내기
const blob = await fetch(base + 'api/export', {method: 'POST', headers: {'Content-Type': 'application/json'},
                         body: JSON.stringify({title, text})}).then(r => r.blob());
```

## 화면만의 것 / 주의
- 경로는 정확히 일치해야 함 → 쿼리를 붙이면 화면 HTML 이 돌아옴.
- 화면이 하는 것: 바뀐 문단 찾기·멈춤 감지(약 1초), 같은 문단 결과 캐시, 밑줄·카드 그리기, 적용/무시/되돌리기, 분류 색, `.txt`·`.md` 내보내기(서버는 `.docx` 만), 자동 저장 타이밍. 분류 이름표(`맞춤법→맞춤법·띄어쓰기` 등)는 서버가 화면 HTML 의 `%CATS%` 에 넣어 줌 — API 로는 없음(위 `c` 키 6개 고정).
- 제안을 적용하면 글이 바뀌므로 같은 줄의 다른 제안 `at` 은 다시 맞춰야 함(화면은 다시 검사).
- `api/check`·`api/review` 의 `persona` 는 객체 전체, `api/docs` 의 `persona` 는 id 문자열.
- 페르소나·문서는 사용자 폴더(`personas/`, `docs/`)에 저장. 다른 사용자 것은 안 보임. 정적 파일 경로 없음.
