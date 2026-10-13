# 업무 메일 API (`mail-local`)
업무 메일 작성·회신·윤문 도구. 로컬 LLM 만 쓰고 메일을 밖으로 보내지 않음. 윤문은 문장 단위로 고치고 숫자·날짜·고유 표기가 바뀐 문장은 원문을 유지.
포털 경로 접두어: `/t/mail-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `/t/mail-local/api/run`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | 아니오 | 아니오 |
| GET | `api/meta` | 선택지(관계·목적·어조·길이·윤문 방식)·기본 모델·오늘 날짜 | 아니오 | 아니오 |
| GET | `api/models` | LLM 모델 이름 목록 | 아니오 | 아니오 |
| GET | `api/history` | 작업 이력 목록(최대 200개) | 아니오 | 예 |
| GET | `api/history/<id>` | 이력 하나(모든 판 포함) | 아니오 | 예 |
| POST | `api/history/delete` | 이력 삭제 | 아니오 | 예 |
| POST | `api/run` | 작성·회신·윤문 실행 (SSE) | 예 — LLM 1~2콜, 수 초~수십 초 | 예 (이력에 저장) |
| GET | 그 밖의 모든 경로 | 화면(`ui.html`) | 아니오 | 아니오 |

## 엔드포인트

### GET api/health
- 응답 `{"ok": true}`

### GET api/meta
```json
{"relations": ["상급자", "동료", "후배·팀원", "타 부서", "외부기관", "협력업체", "교수·학계", "고객·민원인"],
 "purposes": ["요청", "보고", "안내", "감사", "사과", "회신", "독촉", "일정 조율", "자료 송부", "협조 요청", "문의", "축하", "거절", "기타"],
 "tones": ["격식", "정중", "간결", "친근"], "lengths": ["짧게", "보통", "자세히"],
 "polish_modes": [{"id": "spell", "label": "맞춤법·띄어쓰기만"}, {"id": "natural", "label": "자연스럽게"},
                  {"id": "polite", "label": "더 정중하게"}, {"id": "concise", "label": "더 간결하게"}, {"id": "english", "label": "영문 다듬기"}],
 "model": "gemma4:31b", "today": "2026-10-13"}
```

### GET api/models
- 응답: 문자열 배열. **LLM 서버에 닿지 못하면 오류 대신 빈 배열 `[]`**.

### GET api/history
- 응답: `[{"id": "20261007-062603-e557", "kind": "write"|"reply"|"polish", "title": "첫 제목 후보 또는 첫 줄", "ts": "...", "versions": 1}]` (id 역순 = 최근 것 먼저)

### GET api/history/<id>
- `id` 형식 `YYYYMMDD-HHMMSS-xxxx`. 잘못되었거나 없으면 404 `{"error": "없음"}`.
- 응답: `{"id", "kind", "title", "ts", "inputs"(마지막 요청의 입력), "model", "versions": [결과, ...]}` — `versions` 의 각 항목은 아래 `api/run` 의 `result` 와 같은 모양.

### POST api/history/delete
- 요청 `{"id": "..."}` → `{"ok": true}` (없는 id 도 ok). 형식이 틀리면 400 `{"error": "잘못된 이력 id"}`.

### POST api/run
- 요청 (JSON)

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `kind` | string | 예 | `write`(작성) · `reply`(회신) · `polish`(윤문) |
| `inputs` | object | 예 | 아래 표 |
| `model` | string | 아니오 | 비우면 서버 기본 |
| `id` | string | 아니오 | 기존 이력 id. 주면 그 이력에 새 판(version)을 더함(다시 생성·이어 다듬기). 없거나 못 찾으면 새 이력 |
| `instruction` | string | 아니오 | 이어서 다듬기 지시(예 "더 짧게"). `prev` 와 함께 써야 효과 |
| `prev` | object\|string | 아니오 | 직전 결과. write·reply 는 `{"subjects": [...], "body": "..."}`, polish 는 지금 결과 글(문자열) |

`inputs` 필드
- write: `to` `{name, title, org, relation}`, `purpose`(meta.purposes), `points`(핵심 내용, **필수**), `attach`, `due`(날짜 `YYYY-MM-DD` 권장), `tone`, `length`, `lang`(`ko`|`en`), `sender` `{name, title, dept, sig_ko, sig_en}`
- reply: `original`(받은 메일 원문, **필수**, 12,000자까지만 씀), `points`(답장 요지), `to` `{relation}`, `attach`, `tone`, `length`, `lang`(`ko`|`en`|`auto` — auto 면 원문에 한글이 없을 때 영어), `sender`
- polish: `text`(**필수**, 12,000자 넘으면 오류), `mode`(`spell`|`natural`(기본)|`polite`|`concise`|`english`)

- 응답: `text/event-stream`, `data: <JSON>\n\n`
  - `{"stage": "llm", "msg": "...", "reset": true?}` → `{"token": "..."}`… → `{"done": {"id", "kind", "version", "result"}}`
  - 윤문에서 모델이 번호 형식을 못 지키면 한 번 더 부르며 `{"stage": "llm", "msg": "형식 재시도", "reset": true}` (받은 토큰 비우기)
  - 오류: `{"error": "핵심 내용을 적어 주세요"}` 등(HTTP 200 이 이미 나간 뒤). 본문 JSON 이 깨졌으면 스트림 전에 400 `{"error": "잘못된 요청"}`.
- 토큰은 모델 원문(`[제목]`·`[본문]` 섹션 또는 `[1] ...` 번호 줄)이 그대로 흐름. 정리된 값은 `done.result` 에서 받기.

`result` — write·reply
```json
{"subjects": ["제목 후보1", "후보2", "후보3"], "body": "본문(+서명)",
 "checklist": [{"ok": true, "text": "원문 요청 1 — 반영"}],
 "notes": ["입력에 없던 숫자가 본문에 생김(지어낸 것인지 확인): 3억"],
 "lang": "ko", "diff": [["=", "같은 부분"], ["-", "지운 것"], ["+", "더한 것"]],
 "raw": "모델 출력", "instruction": "", "ts": "..."}
```
- `checklist` 는 회신에서 원문 요청사항 반영 여부(`ok`: true 반영 / false 확인 필요·미반영 / null 판정 없음).
- `diff` 는 `prev` 가 있을 때만(직전 본문 ↔ 새 본문).
- `sender.sig_ko`/`sig_en` 이 있으면 본문 끝에 서명을 붙여 줌.

`result` — polish
```json
{"mode": "natural", "label": "자연스럽게", "parts": ["", 0, " ", 1, "\n", "감사합니다."],
 "units": [{"i": 0, "before": "원문 문장", "after": "고친 문장", "changed": true, "use": true, "warn": "",
            "ops": [["=", "..."], ["-", "..."], ["+", "..."]]}],
 "text": "적용된 최종 글", "source": "원문", "changed": 3, "total": 8, "warnings": ["2번 문장: 숫자·고유 표기 변경 ..."], "raw": "...",
 "instruction": "", "ts": "..."}
```
- `parts`: 문자열(그대로 둘 조각: 줄바꿈·서명·인용·글머리)과 정수(문장 번호)를 이은 것. **문장마다 원문/수정본을 골라 다시 조립**할 때 씀.
- `use`: 서버가 기본으로 적용했는지. 숫자·고유 표기가 바뀐 문장은 `use: false` + `warn`.
- `ops`: 문장 안 비교(바뀐 문장에만).

## 흐름 예
```js
const base = '/t/mail-local/';
// 1) 작성
const req = {kind: 'write', inputs: {to: {name: '홍길동', relation: '외부기관'}, purpose: '요청',
             points: '10/20까지 자료 회신 부탁', tone: '정중', length: '보통', lang: 'ko', sender: {name: '김OO', dept: 'OO팀'}}};
const r = await fetch(base + 'api/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(req)});
// SSE 읽기 → done = {id, kind, version, result}

// 2) 이어서 다듬기 (같은 이력에 새 판)
await fetch(base + 'api/run', {method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({...req, id: done.id, instruction: '더 짧게',
                        prev: {subjects: done.result.subjects, body: done.result.body}})});

// 3) 윤문 결과를 사용자가 문장별로 골랐을 때 최종 글 만들기 (화면이 직접 계산)
const text = v.parts.map(p => typeof p === 'number' ? (v.units[p].use ? v.units[p].after : v.units[p].before) : p)
                    .join('').replace(/[ \t]+\n/g, '\n');
```

## 화면만의 것 / 주의
- LLM 연결 실패 오류 문장(`"LLM 서버 연결 실패: …"`)에 서버 주소는 들어가지 않습니다. 파일 올림(base64) API 는 없습니다.
- **문장별 적용/되돌리기는 화면에서만** 함(위 3번). 서버 이력에는 처음 결과(`use` 기본값)만 저장되고 사용자가 바꾼 선택은 저장되지 않음.
- 보내는 사람 정보(이름·소속·서명)는 화면이 브라우저 `localStorage`(`mail-local:` 접두어)에 보관하고 요청마다 `inputs.sender` 로 보냄. 서버 쪽 설정 API 없음. 단, 요청 입력이 이력 파일에 그대로 저장되므로 서명도 이력에 남음.
- 화면은 write 의 `to.addr` 도 보내지만 서버는 쓰지 않음.
- GET 경로는 `?` 뒤를 잘라 비교하므로 쿼리를 붙여도 됨(POST 는 정확히 일치해야 함).
- 이력은 사용자 폴더 `history/<id>.json`. 정적 파일 경로 없음.
