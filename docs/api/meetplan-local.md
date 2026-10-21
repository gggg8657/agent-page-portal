# 회의 안건·결정 추적 API (`meetplan-local`)
회의 전 안건 짜기(시간 배분·목표·정할 질문·사전 자료 — LLM 또는 규칙), 회의 뒤 메모·회의록(붙여 넣기·파일·meeting-local 가져오기)에서 결정·할 일·미결 뽑기(원문 인용을 서버가 확인, 기한 글 → 회의 날짜 기준 한국 날짜), 사용자별 할 일 추적 보드·다음 회의 안건, HWPX·Markdown·XLSX·ICS 내보내기. 명세: `openapi/meetplan-local.json`.

포털 경로 접두어: `/t/meetplan-local/` (예: `api/board` → `http://<서버>/t/meetplan-local/api/board`)

> 뽑은 항목의 `verified` 가 거짓이면 원문에서 인용을 찾지 못한 것입니다 — 화면에 ⚠ 로 늘 보여 주세요(`api/meta` 의 `notice`). 서버는 지우지 않고 표시만 합니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | AI·kordoc 상태, 오늘(한국 날짜), 목표·상태 값, 제한, accept, 보드 | LLM 확인(60초 기억) | - |
| GET·POST | `api/meetings` | 회의 목록 · 만들기 `{title, date}` | - | 예 |
| GET·POST | `api/meetings/{mid}` | 회의(안건·메모·정리 결과·작업 `job`) · 제목·날짜 바꾸기 | - | 예 |
| POST | `api/meetings/{mid}/delete` | 회의와 그 회의의 할 일·미결 지우기 | - | 예 |
| POST | `api/meetings/{mid}/agenda/draft` | 안건 짜기 `{purpose, duration, roles, topics, ai}` → AI 202 작업 / `ai:false` 200 | LLM 1번 | 예 |
| POST | `api/meetings/{mid}/agenda` | 고친 안건 저장 `{…, items, fit}` → `{meeting, total, diff}` | - | 예 |
| POST | `api/meetings/{mid}/notes` | 메모 넣기 `{source: paste\|upload\|meeting-local, text \| name+b64 \| run_id}` | kordoc 변환·meeting-local 호출 수 초 | 예 |
| POST | `api/meetings/{mid}/extract` | 메모 정리 → 202 작업 | LLM 덩어리 수(최대 4) | 예 |
| GET | `api/meetings/{mid}/job` | AI 작업 상태 | - | 예 |
| POST | `api/meetings/{mid}/commit` | 보드에 올리기 `{actions: [{key, …}], open: [{key, …}]}` | - | 예 |
| GET | `api/meetings/{mid}/agenda.md` · `agenda.hwpx` · `minutes.md` · `minutes.hwpx` | 내보내기(attachment) | HWPX kordoc 수 초 | 예 |
| GET | `api/board?owner=&status=&overdue=1&mid=` | 추적 보드 | - | 예 |
| GET | `api/board/actions.xlsx` · `actions.ics` · `actions.md` | 할 일 내보내기(같은 거르기) | - | 예 |
| POST | `api/actions` · `api/actions/{aid}` · `api/actions/{aid}/delete` | 할 일 직접 추가 · 고치기 · 지우기 | - | 예 |
| POST | `api/open/{oid}` · `api/open/{oid}/delete` | 미결 사항 고치기 · 지우기 | - | 예 |
| POST | `api/next-agenda` | 다음 회의 안건 `{open_ids?, include_overdue?, duration, title, date}` → 새 회의 | - | 예 |
| GET | `api/meeting-local/runs` | 이 사용자의 meeting-local 회의록 목록(끝난 것) | meeting-local 호출 | 예 |
| GET | `/` | 예전 화면(`ui.html`) | - | - |

## 안건 (`api/meetings/{mid}/agenda/draft`)
- 입력: `purpose`(300자), `duration`(10~480분), `roles`(참석 **역할** 12개까지 — 이름 말고), `topics`(12개까지). 배열이나 줄바꿈·쉼표로 나눈 글. 주제 + 2 가 `duration/2` 를 넘으면 400.
- AI: LLM(JSON 스키마 `{items[{title, goal, minutes, owner, question, prereading[]}], prereading[], tips[]}`) → 서버 검사: `goal` 은 결정·공유·논의(아니면 제목으로 추정), `owner` 는 `roles` 중 하나(아니면 빈 글 — 사람 이름이 남지 않게), 빠진 주제는 덧붙임(`added_topics`), **분 배분을 회의 시간에 딱 맞춤**(LLM 값 비율 유지·안건마다 최소 2분·큰 나머지 순).
- 규칙(`ai: false`, LLM 없음): 시작(5분) · 주제마다 하나(목표 추정: 확정·승인·선정 → 결정, 현황·보고·공유 → 공유, 나머지 논의) · 마무리(5분).
- 저장(`…/agenda`): 화면에서 고친 값. `fit: true` 면 다시 나눔, 아니면 합과 회의 시간의 차이를 `diff` 로 돌려줌(저장은 함).

## 메모 정리 (`…/notes` → `…/extract` → `…/commit`)
- 메모: `paste` 글 / `upload` 파일(md·txt 그대로, docx 표준 라이브러리, hwp·hwpx·pdf 는 kordoc CLI) / `meeting-local` — **도구 간 호출**: `portal_auth.sign(사용자)` 로 서명해 meeting-local 의 `GET /api/runs`·`GET /api/runs/{run_id}` 를 부름. meeting-local 이 서명된 사용자의 폴더만 보여 주므로 남의 회의록은 목록에도 없음. 회의록(`minutes`)·회의 제목·일시를 가져와 회의 제목('새 회의'일 때)·날짜를 채움.
- 정리: 덩어리(12,000자 × 최대 4, 넘치면 `stats.skipped_chunks`)마다 LLM — `decisions[{text, quote}]`·`actions[{task, owner, due_text, quote}]`·`open[{topic, quote}]`. 메모는 `<<<NOTES>>>` 안 데이터(구분자 흉내·제어문자 지움, 안의 지시는 따르지 않음).
- **인용 확인(서버)**: NFKC·소문자·목록 머리표·마크다운 기호·괄호·공백 정규화 뒤 원문에 그대로 있으면 1.0, 아니면 비슷한 구간 difflib 비율 — **0.85 이상 + 인용 속 숫자가 모두 원문에** 있어야 `verified`. 6자 미만 인용은 불통. 못 하면 지우지 않고 `verified: false`·`flags`.
- **담당·기한 확인**: `owner`·`due_text` 가 원문에 있는 말이 아니면 '미정'·빈 기한으로 바꾸고 `flags`.
- **기한 → 날짜**(LLM 없음, 회의 날짜 기준, 한국 날짜): 오늘·내일·모레 / (이번·다음·다다음·지난) 주 X요일 / 요일만(회의 뒤 가장 가까운 그 요일) / M/D·M월 D일·YYYY-MM-DD(해가 없으면 회의일보다 60일 넘게 앞이면 다음 해) / N일·주·개월 뒤·안 / 이번·다음 달 (말·초·중순·D일) / D일(이번 달, 지났으면 다음 달) / 이번·다음 주(까지·중) → 그 주 금요일(어림) / M월 첫째~마지막 주 → 그 주 금요일(어림) / 연말·상·하반기. 주는 월요일 시작. 어림이면 `due_exact: false`, `due_note` 에 설명. '다음 회의 전'·'가능한 빨리'는 `due` 를 비움.
- 올리기: 고른 `key` 와 사용자가 고친 `task`·`owner`·`due`·`status`. `quote`·`verified` 는 서버 값. 같은 회의·같은 key 는 고쳐 씀.

## 보드 (`api/board`)
- 할 일 `{id, mid, key, task, owner, due, due_text, due_note, status(할 일·진행 중·완료·보류), quote, verified, flags, note, created, updated, done_at, overdue, mtitle, mdate}` · 미결 `{id, mid, key, topic, quote, verified, status(미결·안건으로 넘김·해결), carried_to, carried_title}`.
- `overdue` = 기한이 오늘(한국 날짜)보다 앞이고 완료·보류가 아님. 정렬: 안 끝난 것 → 기한 지난 것 → 기한 가까운 순.
- 거르기 `owner`(그대로 같은 것)·`status`(`열림` = 완료·보류 아님)·`overdue=1`·`mid`. `counts` 는 거르기 전 전체.
- 다음 회의 안건(`api/next-agenda`): 미결(`open_ids`, 없으면 '미결' 전부) + 기한 지난 할 일(`include_overdue`, 기본 참)로 새 회의 + 안건(시작 · 기한 지난 할 일 점검 또는 진행 중 확인 · 미결마다 하나 · 마무리), 지난 회의의 역할을 이어받고 시간을 맞춤. 넘긴 미결은 '안건으로 넘김'·`carried_to`. 넘길 것이 없으면 400.

## 내보내기
- 안건 Markdown·HWPX(kordoc `generate --preset 회의록 --no-cover --no-toc --no-page-numbers` + `validate`, 한 쪽), 회의 정리 Markdown·HWPX(보드에 올렸으면 보드의 지금 값).
- 할 일 XLSX(표준 라이브러리, 머리줄 굵게·고정·자동 필터, 글은 inlineStr), ICS(기한 있는 안 끝난 할 일마다 `DTSTART;VALUE=DATE` 하루 일정, UID = 할 일 id, 줄 75바이트 접기·CRLF), Markdown.

## 데이터·확장
- `WORKSPACE/u/<id>/board/meetings/<mid>.json` · `items.json`. 서버 주인·단독 실행은 `WORKSPACE/board/`.
- **개인 보드만**. 회의·할 일·미결마다 `board`(지금 `me`)·`by`(만든 사용자)를 적어 둠 — 부서 공유 보드는 같은 모양 폴더를 `WORKSPACE/boards/<보드 id>/` 에 두고 `board_root()` 를 늘리면 됨(권한·알림은 그때 설계).

## 제한·오류
- 사용자당 AI 작업 동시 1(409 `busy`)·1시간 20번(429 `rate_limited`, `Retry-After`), 도구 전체 동시 3(409)·LLM 호출 동시 2, 호출 시간 제한 240초.
- LLM 이 없으면 AI 안건·정리 503 `unavailable`(규칙 안건·메모 넣기·보드·내보내기는 됨). kordoc 이 없으면 HWPX 503. meeting-local 이 꺼져 있으면 가져오기 503.
- 입력 오류 400 `bad_request`, 없는 회의·할 일·미결 404, 본문이 너무 크면 413 `too_large`. 오류 문장에서 서버 경로·내부 주소는 지움.
- 회의 300개, 할 일 3,000개, 미결 1,000개, 메모 저장 20만 자(정리 48,000자), 파일 20MB.
