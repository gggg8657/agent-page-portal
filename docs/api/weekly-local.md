# 주간보고 API (`weekly-local`)
개인 주간보고 작성(메모 → LLM 항목화 → 원문 대조 → 제출)과 부서·상위조직 취합(제출 모으기 → LLM 합치기 → 1쪽 압축 → HWPX/DOCX/PPTX/XLSX/HTML/TXT 내보내기). 약어집·과제 등록부 관리 포함.
포털 경로 접두어: `/t/weekly-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/itemize` → `https://<서버>/t/weekly-local/api/itemize`)

> 이 문서는 API 의 필드 이름과 구조만 다룹니다. 기관 양식의 실제 문구·배치는 서버의 양식 파일(`templates/`)과 `api/meta` 응답에서 옵니다.

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- **데이터 공유**: 다른 도구와 달리 보고·취합·약어집·과제 등록부·조직 설정은 사용자 구분 없이 **모든 사용자가 같은 저장소**를 씁니다(부서 취합을 위해). 보고는 `org|dept|name` 조합으로 구분되며, 권한 검사는 없습니다(같은 이름으로 제출하면 덮어씀). 사용자별인 것은 `api/sources`(내 회의록·메일 기록 읽기)뿐입니다.
- POST 본문은 JSON, `Content-Length` 필수. 응답에 `Cache-Control: no-store`.
- 오류: `{"error":"..."}`. GET 은 잘못된 값·없는 파일이 `404`, 그 밖 `500`. POST 는 입력 오류(`ValueError`)가 `400`, 그 밖 `500`. 깨진 JSON 은 `400 {"error":"잘못된 요청"}`.
- 주차 키 형식: `YYYY-Www` (ISO 주, 예 `2026-W42`). 대부분 `week` 를 비우면 이번 주.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/GPU·LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지 | 아니오 | 아니오 |
| GET | `api/meta` | 선택지·양식 목록·약어집 통계·이번 주 등 | 아니오 | 아니오(공유) |
| GET | `api/models` | LLM 모델 목록 | LLM 서버 조회 | 아니오 |
| GET | `api/week?d=` | 날짜/주차 → 주 정보 | 아니오 | 아니오 |
| GET | `api/prev?org&dept&name&week` | 같은 사람의 지난 제출(최근 4주) | 아니오 | 공유 |
| GET | `api/report?org&dept&name&week` | 그 주 제출본 | 아니오 | 공유 |
| GET | `api/reports?week&org` | 그 주 제출 전체 | 아니오 | 공유 |
| GET | `api/status?week&org` | 부서별 제출 현황 + 저장된 취합본 | 아니오 | 공유 |
| GET | `api/projects?org&dept&name&week` | 과제명 후보 | 아니오 | 공유 |
| GET | `api/registry?org&dept` | 과제 등록부 보기 | 아니오 | 공유 |
| GET | `api/form-template?fmt&org&dept&name&week&template` | 빈 작성 양식 파일 | 아니오 | 공유 |
| GET | `api/xlsx-template?org&dept&name&week` | 엑셀 입력 양식 | 아니오 | 공유 |
| GET | `api/sources?week` | 이번 주 내 회의록·메일 기록(다른 도구) | 아니오 | **예** |
| GET | `api/glossary?q&field` | 약어집 검색 | 아니오 | 공유 |
| POST | `api/itemize` | 메모 → 항목화 (SSE) | 예(LLM, 수십 초~수 분) | 아니오(저장 안 함) |
| POST | `api/submit` | 개인 보고 제출(저장) | 아니오 | 공유(저장) |
| POST | `api/aggregate` | 부서별 합치기 → 취합본 (SSE) | 예(LLM, 부서 수 × 수십 초) | 공유(저장) |
| POST | `api/compress` | 취합본 1쪽 압축 (SSE) | 예(LLM) | 아니오(결과만 반환) |
| POST | `api/agg/save` | 취합본 저장(화면에서 고친 것) | 아니오 | 공유(저장) |
| POST | `api/preview` | 미리보기 HTML·점검·분량 추정·텍스트 | 아니오 | 아니오 |
| POST | `api/check` | 규칙 점검 + 분량 추정만 | 아니오 | 아니오 |
| POST | `api/export` | 파일 내보내기 | 아니오(수 초) | 아니오 |
| POST | `api/verify-hwpx` | 내보낸 HWPX 를 다시 읽어 확인 | kordoc(수 초) | 아니오 |
| POST | `api/import` | 파일 여러 개 → 항목으로 읽기 | 수 초 | 아니오 |
| POST | `api/dates` | 항목 글의 날짜 표기 통일 | 아니오 | 아니오 |
| POST | `api/merge-suggest` | 비슷한 과제명 합치기 제안 | 예(LLM) | 아니오 |
| POST | `api/merge-dismiss` | 합치기 제안 숨기기 | 아니오 | 공유(저장) |
| POST | `api/registry/op` | 과제 등록부 편집 | 아니오 | 공유(저장) |
| POST | `api/registry/template` | 등록부로 채운 작성 양식(또는 미리보기) | 아니오 | 아니오 |
| POST | `api/orgs` | 상위조직 → 부서 목록 저장 | 아니오 | 공유(저장) |
| POST | `api/glossary/save` | 약어 등록·수정 | 아니오 | 공유(저장) |
| POST | `api/glossary/delete` | 약어 삭제 | 아니오 | 공유(저장) |
| POST | `api/glossary/ignore` | "약어 아님" 표시/해제 | 아니오 | 공유(저장) |
| GET | 그 밖의 경로 | 화면 HTML | — | — |

## 항목(item) 모양
보고·취합·내보내기에서 공통으로 쓰는 한 줄. 서버가 받을 때 정규화합니다(모르는 값은 기본값으로).
| 이름 | 형식 | 설명 |
|---|---|---|
| `id` | 문자열 | 없으면 서버가 만듦(16진 8자리) |
| `text` | 문자열 | 내용. 끝의 `(@장소, 참석자)` 와 앞의 `(기간)` 은 서버가 `place`·`people`·`period` 칸으로 옮김 |
| `kind` | `done` \| `plan` | 수행 / 계획(이름표는 `api/meta` 의 `kinds`) |
| `cat` | `goal` \| `perf` \| `event` \| `etc` | 분류(이름표는 `api/meta` 의 `cats`) |
| `depth` | 0~2 | 들여쓰기 수준(하위 항목은 상위 바로 뒤) |
| `project` | 문자열 | 과제명 |
| `period` | 문자열 | 기간(`M.D~M.D` 등) |
| `ext` | 불리언 | 외부활동 |
| `place` `party` `people` | 문자열 | 장소·상대 기관/인물·우리 쪽 참석자 |
| `core` `msit` `nobbs` | 불리언 | 표시 플래그(핵심 / 상급기관 보고 / 게시판 게시 제외) |
| `warn` | 문자열 배열 | (서버가 붙임) 원문 대조 경고 |
| `ask` `guess` `project_guess` `src` `who` `carry` `went` | 여러 형식 | (서버가 붙임) 질문 필요 표시·추정 칸·취합 원본 id·작성자 등. 그대로 돌려보내면 됨 |

## 엔드포인트 — 조회

### GET api/meta
- 응답 키: `model`, `week`(아래 주 정보), `cats`(키→이름표), `kinds`(키→이름표), `guide`(입력 도움말 `[제목, 예시]` 배열), `orgs`(`{상위조직: [부서…]}`), `templates`(`[{id, name}]` — 내보내기 양식), `glossary`(약어집 통계), `fields`(약어 분야 목록), `levels`(분야별 기본 풀이 수준 1~3), `presets`(독자별 풀이 수준 묶음), `colors`, `has_sources`(회의록·메일 기록 폴더가 있는지), `kordoc`(HWPX 검증 도구 유무).

### GET api/week?d=<YYYY-MM-DD 또는 YYYY-Www>
- 응답: `{"key":"2026-W42","mon":"2026-10-12","fri":"2026-10-16","label":"2026. 10. 12.(월) ~ 10. 16.(금)","title":"2026년 10월 2주차"}`. `d` 가 없으면 오늘. 형식 오류는 404.

### GET api/report · api/prev
- 쿼리: `org`, `dept`, `name`, `week`(필수: report — 형식이 틀리면 404 "잘못된 주차").
- 응답: 제출 레코드(없으면 `{}`):
```json
{"id": "<해시 12자리>", "week": "2026-W42", "org": "…", "dept": "…", "name": "…", "memo": "원래 메모",
 "items": [<item>], "carry": [<지난 계획 대조>], "remarks": ["특기사항"], "ts": "…",
 "history": [{"ts": "…", "n": 7}], "unresolved": [{"abbr": "…", "kind": "abbr", "sentence": "…"}], "unresolved_reason": ""}
```
  - `prev`: 지난 4주 안의 가장 최근 제출(같은 org·dept·name).

### GET api/reports?week=&org=
- 응답: 그 주 제출 레코드 배열. `org` 를 주면 그 조직만.

### GET api/status?week=&org=
- 응답: `{"week": <주 정보>, "org": "…", "depts": [{"dept": "…", "reports": [{"id","name","ts","n"}], "missing": false}], "agg": <저장된 취합본 또는 null>}`
  - 부서 목록 = 조직 설정(`api/orgs`)의 부서 + 실제 제출이 있는 부서.

### GET api/projects
- 응답: 과제명 문자열 배열(최대 40) — 등록부 과제 먼저, 그다음 같은 부서 최근 8주 제출에서 많이 쓴 이름.

### GET api/registry?org=&dept=
- 응답: `{"org","depts":[…],"orgs":[…],"units":[{"org","dept","members":[…],"projects":[{"id","name","full","period","person","active","order","aliases",…}],"deleted":[…]}]}`

### GET api/form-template · api/xlsx-template
- `form-template` 쿼리: `fmt` = `xlsx`(기본)·`hwpx`·`docx`·`pptx`, `org`, `dept`, `name`, `week`, `template`(`api/meta` 의 templates id).
- 응답: 파일. `Content-Disposition: attachment; filename*=UTF-8''주간보고_작성양식.<fmt>` / `주간보고_입력양식.xlsx`. Content-Type: xlsx `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, hwpx `application/hwp+zip`, docx·pptx 는 각 OOXML 형식.
- 메모: 잘못된 `fmt` 는 500(KeyError).

### GET api/sources?week=
- 응답: `{"meetings":[{"id","title","ts","text"}],"mails":[{"id","title","ts","kind":"write|reply","text"}]}` — 그 주에 만든 **내** 회의록(meeting-local, 4000자까지)·메일(mail-local, 1500자까지). `api/itemize` 의 `imports` 에 넣는 용도.

### GET api/glossary?q=&field=
- 응답: `{"rows": [약어 행…], "stats": {…}, "ignored": ["약어 아님으로 표시된 것"]}` — `rows` 최대 200. 행 필드: `abbr`, `full`, `ko`, `desc`, `field`, `note`, `src` 등(세부는 `rules.py` `Glossary.search` 확인 필요).

## 엔드포인트 — 개인 작성

### POST api/itemize  (SSE)
- 요청:

| 이름 | 형식 | 필수 | 설명 |
|---|---|---|---|
| `memo` | 문자열 | 예* | 자유 메모(최대 8000자). `imports` 가 있으면 비워도 됨 |
| `imports` | 문자열 배열 | 아니오 | 가져온 기록(각 3000자까지 사용), 예: `api/sources` 의 `text` |
| `profile` | 객체 | 아니오 | `{"org","dept","name"}` — 지난 계획·과제 등록부 조회에 씀 |
| `week` | 문자열 | 아니오 | 주차 키 |
| `use_prev` | 불리언 | 아니오 | 지난 제출의 계획과 대조(기본 true) |
| `model` | 문자열 | 아니오 | LLM 모델 |

- SSE 이벤트(`data: <JSON>\n\n`):
  - `{"stage":"llm","msg":"<모델> · 항목 정리","reset":false}` — LLM 호출마다(항목 정리, 약어 후보 제안, 과제명 합치기 판단). 형식 재시도면 `reset:true`
  - `{"token":"…"}` — LLM 원문 토큰
  - 끝: `{"done": {...}}`, 실패: `{"error":"메모가 너무 깁니다 (…자 > 8000자)"}` 등
- `done` 키: `items`(item 배열), `carry`(`[{"prev": <지난 계획 item>, "status": "완료|진행|미착수|미확인", "evidence": "메모 속 근거"}]`), `notes`(경고 문장들), `questions`(질문 카드 — 아래), `remarks`(특기사항), `projects`, `alias_log`, `merge_suggest`(`[{"a","b","into","how","samples"}]`), `prev_week`, `week`, `raw`(LLM 원문).
- `questions` 항목: 확정 못 한 약어(`kind:"abbr"`, `abbr`, `sentence`, `candidates:[{"full","ko","desc","field","src","suggest"}]`) 또는 과제명 미정(`kind:"project"`, `candidates`). LLM 제안 후보는 `suggest:true` — 사용자가 고르기 전에는 문서에 쓰이지 않습니다. 답은 `api/glossary/save`(풀이) 또는 `api/glossary/ignore`(약어 아님)로 저장.
- 메모: 저장하지 않습니다. 결과를 화면에서 고친 뒤 `api/submit`.

### POST api/submit
- 요청: `{"profile": {"org","dept","name"}, "week", "items": [<item>], "memo", "carry", "remarks": [...], "unresolved_reason": ""}` — `dept`·`name` 필수, 빈 `text` 항목은 버림.
- 응답: `{"ok": true, "id": "<해시>", "week": "2026-W42", "path": "reports/2026-W42/<id>.json"}`
- 오류: `400` 이름·부서 없음/항목 없음. **`409`** 미확인 약어·과제명이 남음: `{"error": "확인되지 않은 약어 2개(…) — …", "unresolved": [{"abbr","kind","sentence",…}]}` → 질문에 답하거나 `unresolved_reason` 에 사유를 넣어 다시 제출(사유는 제출본에 기록).
- 메모: 같은 org·dept·name·주차는 덮어쓰고 `history` 에 기록이 쌓입니다.

## 엔드포인트 — 취합

### POST api/aggregate  (SSE)
- 요청: `{"week", "org"(필수), "model"}`
- SSE: `api/itemize` 와 같은 형식(`stage`·`token`), 부서마다 `"<부서> 합치기 (N항목)"` 단계.
- `done`: 취합본 `agg`
```json
{"week": "2026-W42", "org": "…", "ts": "…", "model": "…",
 "rows": [{"label": "<부서>", "items": [<item + src·who>], "people": ["…"]}, {"label": "<부서>", "items": [], "missing": true}],
 "notes": ["…"], "remarks": ["(<부서>) …"], "merge_suggest": {"<부서>": [ … ]}}
```
- 메모: 서버에 저장도 합니다(같은 주·조직은 덮어씀). 합친 항목의 `src` 는 원래 항목 id 들.

### POST api/compress  (SSE)
- 요청: `{"agg": <취합본>, "template": "<양식 id>", "model"}`
- `done`: `{"agg": <줄인 취합본>, "before": <분량 추정>, "after": <분량 추정>, "notes": [...]}` — 뺀 항목은 각 행의 `dropped` 에 남아 되살릴 수 있음. 핵심·상급기관 표시 항목은 지우지 않음.
- 메모: **저장하지 않습니다.** 결과를 쓰려면 `api/agg/save`.

### POST api/agg/save
- 요청: `{"agg": <취합본>}` (`agg.week`·`agg.org` 로 저장 위치 결정) → `{"ok": true}`

### POST api/orgs
- 요청: `{"orgs": {"<상위조직>": ["부서1", "부서2"] 또는 "부서1,부서2"}}` → 정리된 객체. **전체를 덮어씁니다.**

## 엔드포인트 — 문서

`report`(개인) 또는 `agg`(취합) 중 하나를 넣습니다. 공통 `options`:
| 이름 | 설명 |
|---|---|
| `template` | 양식 id(`api/meta` 의 `templates`) |
| `title` `subtitle` | 제목 덮어쓰기 |
| `attendee` | 참석자 표기 `external`(기본)·`always`·`never` |
| `writer` | 작성자 자동 포함 `yes`·`no` |
| `online` | 온라인 표기 `show`·`omit` |
| `expand_mode` | 약어 풀이 방식 `paren`·`note`(기본)·`off` |
| `levels` | 분야별 풀이 수준 `{분야: 1~3}` |
| `desc_block` `first_only` `legend` | 풀이 설명을 표 아래로 / 첫 등장에만(기본 true) / 범례(기본 true) |

- 개인: `{"report": {"org","dept","name","week","items":[…],"remarks":[…]}, "options": {…}}`
- 취합: `{"agg": <취합본>, "options": {…}}`

### POST api/preview
- 추가 필드: `bbs`(불리언 — 게시판 게시용: `nobbs` 항목 제외)
- 응답: `{"html": "<표 HTML 조각>", "checks": [점검 결과…], "estimate": {"lines": …, …}, "text": "텍스트 버전"}` (`checks`·`estimate` 세부는 `rules.check_doc`·`render.estimate` 확인 필요)

### POST api/check
- 응답: `{"checks": […], "estimate": {…}}` (미리보기 HTML 없이)

### POST api/export
- 추가 필드: `format` = `html`(기본)·`hwpx`·`docx`·`pptx`·`xlsx`(보고서형)·`xlsx-data`(다시 올리기용 입력 양식)·`txt`, `bbs`
- 응답: 파일 + `Content-Disposition: attachment; filename*=UTF-8''<제목>[_BBS게시용].<확장자>` + 헤더 **`X-Unresolved: <미확인 약어 수>`**.
- 메모: hwpx·docx·pptx·xlsx 안에 항목 JSON 이 함께 들어가, `api/import` 로 다시 올리면 손실 없이 돌아옵니다. txt 는 `text/plain; charset=utf-8`(화면은 클립보드 복사에 씀).

### POST api/verify-hwpx
- 요청: `api/export` 와 같음(형식은 hwpx 로 고정).
- 응답: `{"inspect": {…HWPX 구조 점검…}, "kordoc": {"valid": true, "msg": "…", "text": "다시 읽은 본문"} 또는 null}`

### POST api/import
- 요청: `{"files": [{"name": "보고.hwpx", "b64": "<base64 또는 data URL>"}], "week"}` — 형식 섞어도 됨(hwpx·docx·xlsx·pptx 등), 파일당 30MB 넘으면 건너뜀.
- 응답: `{"files": [{"name","format","method":"embedded|table|form|memo","rows":[{"row","dept","name","item":<item>,"errors":[],"warnings":[],"suggest_project"?}],"abbrs":[{"abbr","full",…,"known"}],"remarks":[],"org","memo","notes":[]}], "asks": [확인 필요한 약어], "week": <주 정보>}`
- 메모: 저장하지 않습니다. 읽은 항목을 화면에서 확인한 뒤 제출·취합에 씁니다.

### POST api/dates
- 요청: `{"week", "items": [<item>]}` → `{"items": [글의 날짜 표기만 바꾼 item]}`

### POST api/merge-suggest
- 요청: `{"items": [<item>], "org", "dept", "model"}` → `{"suggest": [{"a","b","into","how":"llm|규칙","samples"}]}`
  또는 `{"agg": <취합본>, "model"}` → `{"by_dept": {"<부서>": [ … ]}}`

### POST api/merge-dismiss
- 요청: `{"dept", "a", "b"}` → `{"ok": true}` (이 짝은 다시 제안하지 않음)

## 엔드포인트 — 과제 등록부·약어집

### POST api/registry/op
- 요청 공통: `{"op", "org", "dept"(필수), "editor"}` + op 별:
  - `add`: `name` 또는 `names:[…]`, (한 개일 때) `full`, `period`, `person`
  - `update`: `id` + 바꿀 칸
  - `delete` / `restore`: `id`
  - `reorder`: `ids:[과제 id…]` (이 순서로)
  - `members`: `members` — 이름 배열 또는 쉼표/줄바꿈 구분 문자열(통째로 바꿈)
  - `alias`: `name`(등록 이름), `alias`(앞으로 이 이름으로 합침)
  - `copy_prev`: `week` — 최근 4주 제출에서 쓴 과제명을 한꺼번에 추가
- 응답: `api/registry?org=` 와 같은 모양(조직 전체).

### POST api/registry/template
- 요청: `{"org", "dept"(비우면 조직 전체), "fmt":"hwpx|docx|pptx|xlsx", "week", "template", "person", "per_person", "prefill", "preview"}`
- 응답: `preview:true` 면 `{"html": "…", "rows": N}`, 아니면 파일(`주간보고_작성양식_<부서 또는 조직>.<fmt>`).

### POST api/glossary/save
- 요청: `{"abbr", "full", "ko", "desc", "field", "note", "editor", "src", "ctx"}` — `abbr` 와 (`full` 또는 `ko`) 필수.
- 응답: 저장된 약어 행.

### POST api/glossary/delete · api/glossary/ignore
- `delete`: `{"abbr", "editor"}` → `{"ok": true}`
- `ignore`: `{"abbr", "on": true|false}` → `{"ok": true}` (`true` = 약어 아님으로 표시)

## 흐름 예
개인 작성 → 제출:
```js
const base = '/t/weekly-local/', post = (p, b) => fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
const { week } = await fetch(base + 'api/meta').then(r => r.json());
const profile = { org: '…', dept: '…', name: '…' };
// SSE 읽기 함수 sse() 는 meeting-local 문서와 같은 방식(POST + fetch 스트림)
const r = await sse(base + 'api/itemize', { profile, week: week.key, memo }, ev => ev.token && appendRaw(ev.token));
// r.questions 에 답하기: post('api/glossary/save', {abbr, full}) 또는 post('api/glossary/ignore', {abbr, on: true})
const s = await post('api/submit', { profile, week: week.key, items: r.items, memo, carry: r.carry, remarks: r.remarks });
if (s.status === 409) { const j = await s.json(); /* j.unresolved 를 다시 묻거나 unresolved_reason 넣어 재제출 */ }
```
취합 → 압축 → 저장 → HWPX:
```js
const agg = await sse(base + 'api/aggregate', { week: week.key, org: '…' });
const c = await sse(base + 'api/compress', { agg, template: '<양식 id>' });
await post('api/agg/save', { agg: c.agg });
const f = await post('api/export', { agg: c.agg, options: { template: '<양식 id>' }, format: 'hwpx' });
console.log('미확인 약어', f.headers.get('X-Unresolved'));
```

## 화면만의 것 / 주의
- 프로필(조직·부서·이름)·옵션은 화면이 localStorage 에 기억합니다. 서버에는 로그인 계정과 연결된 "내 프로필"이 없습니다.
- 질문 카드 진행, 항목 편집(드래그·들여쓰기), 지난 계획 상태 고르기, 압축 뒤 되살리기 버튼은 화면이 `items`·`dropped` 를 직접 고쳐 다시 보냅니다.
- 내보내기 전 "미확인 약어 N개" 확인 창은 화면이 셉니다(서버는 `X-Unresolved` 헤더로도 알려줌).
- `api/compress` 와 `api/import` 는 저장하지 않습니다. 저장은 `api/agg/save`·`api/submit` 으로만.
- 모든 사용자가 같은 저장소를 쓰므로, 새 화면에서 `api/orgs`(전체 덮어쓰기)·`api/registry/op`·`api/glossary/*` 를 부를 때 다른 사람 데이터에 영향이 갑니다.
- `/static/` 경로는 없습니다.
