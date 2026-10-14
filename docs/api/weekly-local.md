# 주간보고 API (`weekly-local`)
개인 주간보고 작성(메모 → LLM 항목화 → 원문 대조 → 제출)과 부서·상위조직 취합(제출 모으기 → LLM 합치기 → 1쪽 압축 → HWPX/DOCX/PPTX/XLSX/HTML/TXT 내보내기). 약어집·과제 등록부 관리 포함.
포털 경로 접두어: `/t/weekly-local/` (아래 경로는 모두 이 뒤에 붙음. 예: `api/itemize` → `https://<서버>/t/weekly-local/api/itemize`)

> 이 문서는 API 의 필드 이름과 구조만 다룹니다. 기관 양식의 실제 문구·배치는 서버의 양식 파일(`templates/`)과 `api/meta` 응답에서 옵니다.

공통:
- 포털을 거치면 로그인 쿠키(`agsid`)가 필요합니다. 없으면 `401 {"error":"로그인이 필요합니다","login":"/login"}`.
- **데이터 공유**: 다른 도구와 달리 보고·취합·약어집·과제 등록부·조직 설정은 사용자 구분 없이 **모든 사용자가 같은 저장소**를 씁니다(부서 취합을 위해). 보고는 **로그인 계정**(`X-Agent-User`)마다 주·소본부(`org`)당 하나입니다(예전 보고는 `org|dept|name` 조합 id 그대로). 사용자별인 것은 `api/sources`(내 회의록·메일 기록 읽기)뿐입니다.
- **권한**(포털이 붙인 `X-Agent-User` 기준, 관리자 = `owner`): 보기는 누구나. 덮어쓰기·고치기·지우기는 **처음 만든 계정 또는 owner 만** — 제출 보고, 취합본(주·소본부별), 약어집 사내 항목, "약어 아님" 해제, 합치기 제안 "따로 두기" 해제, 과제 등록부의 과제(고치기·지우기·되살리기·순서·별칭)와 실 구성원. 만든 계정 기록이 없는 예전 데이터는 owner 만. 새로 만들기(새 보고·새 약어·새 과제·약어 아님 표시)는 누구나. **조직 설정(`api/orgs`)은 owner 만.** 거절은 `403 {"error": "…만든 사람이나 관리자만 …"}`. 만든 계정은 서버 파일에 `_by` 로 남고 응답에는 나가지 않습니다.
- **작성자 = 로그인 계정**: 제출자의 이름·부서는 서버가 포털 계정(표시 이름·**승인된** 부서)에서 채웁니다 — 요청의 `profile.name`·`profile.dept` 는 무시하고 `org`(소본부)만 씁니다(조직 설정에 그 부서가 있으면 그 소본부 안에서만). 승인된 부서가 없으면 제출 `403 {"error", "no_dept": true}`. 관리자(`owner`)만 이름·부서를 적어 대신 내거나 `id` 로 남의 보고를 고칠 수 있습니다(주인·이름·부서는 그대로).
- **행위자 기록**: 보고 `history`·취합본 `history`·약어집·과제 등록부 이력에 서버가 바꾼 계정을 남깁니다(서버 파일에만, 응답에는 안 나감). 요청의 `editor` 는 무시하고 계정 표시 이름(관리자는 "관리자")을 씁니다.
- **과제 등록부**(`api/registry/op`)는 그 실 사람(포털 승인 부서)·그 실 부서장·owner 만 바꿀 수 있습니다.
- POST 본문은 JSON, `Content-Length` 필수. 응답에 `Cache-Control: no-store`.
- 오류: `{"error":"..."}`. GET 은 잘못된 값·없는 파일이 `404`, 그 밖 `500`. POST 는 입력 오류(`ValueError` 등)가 `400`, 권한 없음이 `403`, 그 밖 `500`. 깨진 JSON 은 `400 {"error":"잘못된 요청"}`, JSON 객체가 아닌 본문은 `400 {"error":"요청 본문은 JSON 객체여야 합니다"}`.
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
| GET | `api/merge-dismissed?dept=…` | 합치기 제안을 숨긴(따로 둔) 짝 목록 + 풀 수 있는지 | 아니오 | 공유 |
| POST | `api/itemize` | 메모 → 항목화 (SSE) | 예(LLM, 수십 초~수 분) | 아니오(저장 안 함) |
| POST | `api/submit` | 개인 보고 제출(저장) | 아니오 | 공유(저장) |
| POST | `api/aggregate` | 부서별 합치기 → 취합본 (SSE) | 예(LLM, 부서 수 × 수십 초) | 공유(저장) |
| POST | `api/compress` | 취합본 1쪽 압축 (SSE) | 예(LLM) | 아니오(결과만 반환) |
| POST | `api/agg/save` | 취합본 저장(화면에서 고친 것) | 아니오 | 공유(저장) |
| POST | `api/report/cancel` | 내 제출 취소(휴지통으로, 취합에 쓰였으면 잠김 409) | 아니오 | 공유(본인·owner) |
| GET | `api/trash` | 휴지통 목록(owner 전부, 그 밖 자기 제출만) | 아니오 | 공유 |
| POST | `api/trash/put` | 제출·취합본을 휴지통으로 (owner) | 아니오 | 공유(owner) |
| POST | `api/trash/restore` | 휴지통에서 되살리기(기한 안) | 아니오 | 공유(owner·본인) |
| POST | `api/trash/purge` | 기한 지난 것 비우기 (owner, 되돌릴 수 없음) | 아니오 | 공유(owner) |
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
- 응답 키: `model`, `is_owner`(이 요청 계정이 관리자인지 — 화면은 아니면 조직 설정을 잠금), `me`(내 계정: `{"admin","account","name","dept","heads","orgs","problem"}` — 화면은 이름·부서 입력칸 대신 이 값을 표시, `problem` 이 있으면 제출 버튼을 막음), `week`(아래 주 정보), `cats`(키→이름표), `kinds`(키→이름표), `guide`(입력 도움말 `[제목, 예시]` 배열), `orgs`(`{상위조직: [부서…]}`), `templates`(`[{id, name}]` — 내보내기 양식), `glossary`(약어집 통계), `fields`(약어 분야 목록), `levels`(분야별 기본 풀이 수준 1~3), `presets`(독자별 풀이 수준 묶음), `colors`, `has_sources`(회의록·메일 기록 폴더가 있는지), `kordoc`(HWPX 검증 도구 유무).

### GET api/week?d=<YYYY-MM-DD 또는 YYYY-Www>
- 응답: `{"key":"2026-W42","mon":"2026-10-12","fri":"2026-10-16","label":"2026. 10. 12.(월) ~ 10. 16.(금)","title":"2026년 10월 2주차"}`. `d` 가 없으면 오늘. 형식 오류는 404.

### GET api/report · api/prev
- 쿼리: `org`, `dept`, `name`, `week`(필수: report — 형식이 틀리면 404 "잘못된 주차"), `id`(관리자만).
- 계정 사용자: 그 주·`org` 의 **자기 보고**(`dept`·`name` 무시). `prev` 도 그 계정이 낸 지난 제출. 관리자: `id` 또는 `org`·`dept`·`name` 으로.
- 응답: 제출 레코드(없으면 `{}`):
```json
{"id": "<해시 12자리>", "week": "2026-W42", "org": "…", "dept": "…", "name": "…", "memo": "원래 메모",
 "items": [<item>], "carry": [<지난 계획 대조>], "remarks": ["특기사항"], "ts": "…",
 "history": [{"ts": "…", "n": 7, "action": "제출|다시 제출|되살림"}], "unresolved": [{"abbr": "…", "kind": "abbr", "sentence": "…"}], "unresolved_reason": ""}
```
  - `prev`: 지난 4주 안의 가장 최근 제출(계정 사용자는 같은 계정, 관리자는 같은 org·dept·name).
  - `report` 는 제출이 있으면 `"locked": bool, "lock_reason": "…", "mine": bool` 도 붙음(아래 '제출 취소·휴지통').

### GET api/reports?week=&org=
- 응답: 그 주 제출 레코드 배열. `org` 를 주면 그 조직만.

### GET api/status?week=&org=
- 응답: `{"week": <주 정보>, "org": "…", "depts": [{"dept": "…", "reports": [{"id","name","ts","n","locked","lock_reason","mine"}], "missing": false}], "agg": <저장된 취합본 또는 null>}`
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
- 요청: `{"profile": {"org"}, "week", "items": [<item>], "memo", "carry", "remarks": [...], "unresolved_reason": ""}` — 빈 `text` 항목은 버림. 계정 사용자는 `profile.name`·`dept` 를 보내도 무시(포털 계정 값). 관리자는 `profile` 의 `dept`·`name` 필수, 또는 `"id"`(기존 보고 고치기).
- 응답: `{"ok": true, "id": "<해시>", "week": "2026-W42", "path": "reports/2026-W42/<id>.json", "profile": {"org","dept","name"}}` — `profile` 은 실제로 저장된 값.
- 오류: `400` (관리자) 이름·부서 없음/항목 없음/소본부 모호. `403` 승인된 부서 없음(`"no_dept": true`)·포털 계정을 못 읽음·남의 보고 `id`. **`409`** 미확인 약어·과제명이 남음: `{"error": "확인되지 않은 약어 2개(…) — …", "unresolved": [{"abbr","kind","sentence",…}]}` → 질문에 답하거나 `unresolved_reason` 에 사유를 넣어 다시 제출(사유는 제출본에 기록).
- 권한: 남의 보고는 고칠 수 없음(그 계정·owner 만). 다른 사람이 같은 이름을 적어도 자기 보고로 따로 저장되므로 자리를 차지할 수 없음.
- 메모: 같은 계정이 같은 주·소본부로 다시 내면 덮어쓰고 `history` 에 기록이 쌓입니다(바꾼 계정은 서버에만).

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
- 권한: 그 주·소본부 취합본이 이미 있고 처음 취합한 계정·owner 가 아니면 LLM 을 부르기 전에 `data: {"error": "…처음 취합한 사람이나 관리자만 …"}` 로 끝납니다(HTTP 는 200).

### POST api/compress  (SSE)
- 요청: `{"agg": <취합본>, "template": "<양식 id>", "model"}`
- `done`: `{"agg": <줄인 취합본>, "before": <분량 추정>, "after": <분량 추정>, "notes": [...]}` — 뺀 항목은 각 행의 `dropped` 에 남아 되살릴 수 있음. 핵심·상급기관 표시 항목은 지우지 않음.
- 메모: **저장하지 않습니다.** 결과를 쓰려면 `api/agg/save`.

### POST api/agg/save
- 요청: `{"agg": <취합본>}` (`agg.week`·`agg.org` 로 저장 위치 결정) → `{"ok": true}`
- 오류: 처음 취합한 계정·owner 가 아니면 `403`.
- `agg.src_reports`(취합에 쓰인 제출 id, `api/aggregate` 가 기록)는 서버 값을 유지합니다 — 보내지 않아도, 다른 값을 보내도 그대로.
- `agg.history`(`[{"ts","action":"취합|다시 취합|저장"}]`)는 서버가 쌓습니다(보낸 값은 무시, 행위자 계정은 서버에만).

## 엔드포인트 — 제출 취소·휴지통
바로 지우지 않고 휴지통(`WORKSPACE/trash/<id>.json`, 원래 레코드 통째로)으로 옮깁니다. 기한(`WEEKLY_TRASH_DAYS`, 기본 30일) 안에 되살릴 수 있고,
기한이 지난 것은 owner 가 `api/trash/purge` 로 비웁니다. 자동 비우기는 `WEEKLY_TRASH_AUTO_PURGE=1` 일 때만(기본 꺼짐, 하루 한 번).
- **잠김**: 같은 주·소본부의 (휴지통에 없는) 취합본 `src_reports` 에 든 제출. `src_reports` 가 없는 예전 취합본은 부서 행(`label`)의 `people` 에 이름이 있으면 잠김.
  잠긴 제출은 본인이 취소할 수 없음(owner 는 휴지통으로 보낼 수 있고 취합본은 그대로). 취합본을 휴지통으로 보내면 잠김이 풀림.

### POST api/report/cancel
- 요청: `{"week": "2026-W42", "id": "<제출 id 12자리>"}` (또는 `id` 대신 `"profile": {"org","dept","name"}`, 선택 `reason`) → `{"ok": true, "trash_id": "…"}`
- 오류: 처음 제출한 계정·owner 가 아니면 `403`, 잠기면 `409 {"error": "<소본부> <주> 취합본(취합 …)에 쓰였습니다 — …", "locked": true}`, 없으면 `404`.

### GET api/trash
- 응답: `{"items": [{"id","kind":"report|agg","week","org","dept","name","ts"(버린 시각),"reason","mine","n","days_left","expired","restorable"}], "days": 30, "auto_purge": false}`
  - owner 는 전부, 그 밖의 계정은 자기가 처음 낸 제출만(관리자가 보낸 것 포함).

### POST api/trash/put  (owner)
- 요청: `{"kind": "report", "week", "id"}` 또는 `{"kind": "agg", "week", "org"}` (+ `reason`) → `{"ok": true, "trash_id": "…", "note"?: "취합본에 쓰인 제출입니다 — …"}`. owner 가 아니면 `403`.

### POST api/trash/restore
- 요청: `{"id": "<trash_id>"}` → `{"ok": true, "kind", "week"}`. owner, 또는 자기가 처음 낸 제출만(`403`).
- `409`: 기한이 지남, 또는 원래 자리에 새 제출·취합본이 있음(먼저 정리).

### POST api/trash/purge  (owner, 되돌릴 수 없음)
- 요청: `{}` → `{"ok": true, "purged": <지운 수>}` — 기한 지난 것만. owner 가 아니면 `403`.

### POST api/orgs
- 요청: `{"orgs": {"<상위조직>": ["부서1", "부서2"] 또는 "부서1,부서2"}}` → 정리된 객체. **전체를 덮어씁니다.** **owner 만**(그 밖 `403 {"error": "조직 설정은 관리자만 바꿀 수 있습니다"}`).

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
- 요청: `{"files": [{"name": "보고.hwpx", "b64": "<base64 또는 data URL>"}], "week"}` — 형식 섞어도 됨(hwpx·docx·xlsx·pptx 등), 파일당 30MB 넘으면 건너뜀. 요청 본문 전체가 160MB 를 넘으면(모든 POST 공통) `413 {"error":"요청이 너무 큽니다(최대 160MB)"}`.
- 응답: `{"files": [{"name","format","method":"embedded|table|form|memo","rows":[{"row","dept","name","item":<item>,"errors":[],"warnings":[],"suggest_project"?}],"abbrs":[{"abbr","full",…,"known"}],"remarks":[],"org","memo","notes":[]}], "asks": [확인 필요한 약어], "week": <주 정보>}`
- 메모: 저장하지 않습니다. 읽은 항목을 화면에서 확인한 뒤 제출·취합에 씁니다.

### POST api/dates
- 요청: `{"week", "items": [<item>]}` → `{"items": [글의 날짜 표기만 바꾼 item]}`

### POST api/merge-suggest
- 요청: `{"items": [<item>], "org", "dept", "model"}` → `{"suggest": [{"a","b","into","how":"llm|규칙","samples"}]}`
  또는 `{"agg": <취합본>, "model"}` → `{"by_dept": {"<부서>": [ … ]}}`

### POST api/merge-dismiss
- 요청: `{"dept", "a", "b"}` → `{"ok": true}` (이 짝은 그 부서에서 누구에게도 다시 제안하지 않음). `{"dept", "a", "b", "undo": true}` 는 숨김 해제 — 화면: 보고 작성·취합 미리보기 위 "따로 둔 것 n개" 접힘 목록의 **다시 제안 받기** 버튼(해제 뒤 합치기 제안을 바로 다시 찾음).
- 권한: 숨기기는 누구나(보고 작성 화면·취합 화면 둘 다 "따로 두기" 를 씀). 해제는 숨긴 계정·owner 만, 숨긴 계정 기록이 없는 예전 항목은 owner 만 — 아니면 `403 {"error":"이 '따로 두기'는 넣은 사람이나 관리자만 풀 수 있습니다"}`. 이미 숨긴 짝을 다른 계정이 다시 숨겨도 처음 숨긴 계정이 그대로 남음. 숨긴 계정은 서버 파일(`merge_dismissed_by.json`)에만 있고 응답에는 나가지 않습니다.
- 오류: `a`·`b` 가 비었거나 200자 넘으면 `400`.
- 화면 표시용 원래 이름은 `merge_dismissed_names.json` 에 따로 둠(키는 정규화된 이름 — 기존 파일 형식 그대로).

### GET api/merge-dismissed?dept=<부서>&dept=<부서>…
- `dept` 여러 번(빈 값 = 부서 없이 숨긴 것), 없으면 전부 → `{"items": [{"dept","a","b","can_undo","why"}]}`.
- `can_undo`: 부른 계정이 풀 수 있는지(숨긴 계정·owner 참, 기록 없는 예전 항목은 owner 만). 거짓이면 `why` 에 이유("다른 사람이 숨김 — …", "예전 항목 — …"). **숨긴 계정 id 는 내보내지 않음.** 예전 항목(이름 기록 없음)은 `a`·`b` 가 정규화된 이름(공백·괄호 뺀 소문자).
- 오류: `dept` 200개 넘거나 하나가 200자 넘으면 `400`.

## 엔드포인트 — 과제 등록부·약어집

### POST api/registry/op
- 요청 공통: `{"op", "org", "dept"(필수)}` + op 별(`editor` 는 보내도 무시 — 서버가 계정 표시 이름을 기록):
  - `add`: `name` 또는 `names:[…]`, (한 개일 때) `full`, `period`, `person`
  - `update`: `id` + 바꿀 칸
  - `delete` / `restore`: `id`
  - `reorder`: `ids:[과제 id…]` (이 순서로)
  - `members`: `members` — 이름 배열 또는 쉼표/줄바꿈 구분 문자열(통째로 바꿈)
  - `alias`: `name`(등록 이름), `alias`(앞으로 이 이름으로 합침)
  - `copy_prev`: `week` — 최근 4주 제출에서 쓴 과제명을 한꺼번에 추가
- 응답: `api/registry?org=` 와 같은 모양(조직 전체).
- 권한: 모든 op 는 **그 실(`dept`) 사람(포털 승인 부서)·그 실 부서장·owner 만**(다른 실 사람은 새 과제·별칭·구성원도 못 바꿈). 그 안에서 `update`·`delete`·`restore`·`reorder`(순서가 바뀌는 과제)·`alias`(이미 있는 과제에 별칭을 더하거나 다른 과제를 합쳐 지울 때)는 그 과제를 만든 계정·그 실 부서장·owner 만, `members` 는 그 실 사람·owner. 아니면 `403`. 이력(`history`)마다 `editor`(계정 표시 이름)·`action`, 바꾼 계정은 서버에만.

### POST api/registry/template
- 요청: `{"org", "dept"(비우면 조직 전체), "fmt":"hwpx|docx|pptx|xlsx", "week", "template", "person", "per_person", "prefill", "preview"}`
- 응답: `preview:true` 면 `{"html": "…", "rows": N}`, 아니면 파일(`주간보고_작성양식_<부서 또는 조직>.<fmt>`).

### POST api/glossary/save
- 요청: `{"abbr", "full", "ko", "desc", "field", "note", "src", "ctx"}` — `abbr` 와 (`full` 또는 `ko`) 필수. `editor` 는 보내도 무시(서버가 계정 표시 이름을 기록).
- 응답: 저장된 약어 행.
- 권한: 이미 있는 사내 항목은 만든 계정·owner 만 고침(기록 없는 예전 항목은 owner 만). 지운 항목은 누구나 다시 만들 수 있음. 아니면 `403`.

### POST api/glossary/delete · api/glossary/ignore
- `delete`: `{"abbr"}` → `{"ok": true}` — 지운 계정·원래 만든 계정을 서버에 남김.
- `ignore`: `{"abbr", "on": true|false}` → `{"ok": true}` (`true` = 약어 아님으로 표시)
- 권한: `delete` 는 만든 계정·owner 만. `ignore` 표시는 누구나, 해제(`on:false`)는 표시한 계정·owner 만(기록 없는 예전 것은 owner 만). 아니면 `403`.

## 흐름 예
개인 작성 → 제출:
```js
const base = '/t/weekly-local/', post = (p, b) => fetch(base + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
const { week, me } = await fetch(base + 'api/meta').then(r => r.json());   // 이름·부서는 계정 값(me.name·me.dept)
const profile = { org: (me.orgs || [])[0] || '…' };
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
- 모든 사용자가 같은 저장소를 쓰므로 다른 사람 데이터는 볼 수 있지만, 고치기·지우기는 만든 계정·owner 만 됩니다(위 권한). `api/orgs` 는 owner 만.
- `/static/` 경로는 없습니다.
