# 과제 제안서 도우미 API (`proposal-local`)
과제 아이디어에서 연구개발 과제 제안서를 절(사업 유형별 구성)마다 쓰고 다듬습니다 — 절 작업(LLM: 초안 쓰기·다듬기·개조식으로·분량 줄이기/늘리기), 공고문·기존 보고서의 쪽 번호 근거(서버가 인용을 원문에서 확인), 추진 일정 간트(연차·분기), 평가 관점 점검(필요성·창의성·실현 가능성·파급효과 + 규칙), HWPX(kordoc)·DOCX·Markdown·간트 SVG/CSV 내보내기. 명세: `openapi/proposal-local.json`.

포털 경로 접두어: `/t/proposal-local/` (예: `api/drafts` → `http://<서버>/t/proposal-local/api/drafts`)

> 절 구성은 공개된 일반 연구개발 제안서 구성을 바탕으로 한 **틀**입니다(특정 기관 서식 아님). `api/meta` 의 `notice` 를 화면에 늘 보여 주세요.

## 요약 표
| 메서드 | 경로 | 하는 일 | LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM·kordoc 상태, 사업 유형(`kinds[].sections[]`: id·제목·권장 글자 수), 평가 관점, 절 작업, HWPX 프리셋, 제한, 1쪽 어림 글자 수 | 확인(60초 기억) | - |
| GET·POST | `api/drafts` | 목록 · 만들기 `{title, idea, period, scale, kind, years, origin?}` / 연습 초안 `{sample: true}` | - | 예 |
| GET·POST | `api/drafts/{did}` | 초안 · 저장 `{rev, …바꾼 칸}` (rev 다르면 409 `conflict` + `draft`) | - | 예 |
| POST | `api/drafts/{did}/delete` | 초안 지우기 | - | 예 |
| POST | `api/drafts/{did}/refs` | 참고 자료 올리기 `{name, b64 \| text}` | - | 예 |
| GET | `api/drafts/{did}/refs/{rid}?n=` | 참고 자료 한 쪽(단위) 원문 | - | 예 |
| POST | `api/drafts/{did}/refs/{rid}/delete` | 참고 자료 빼기(그 자료 근거도 뺌) | - | 예 |
| POST | `api/drafts/{did}/sections/{sid}/act` | 절 작업 `{action, text?}` → 제안(저장 안 함) | 1번 | 예 |
| POST | `api/drafts/{did}/sections/{sid}/cite` | 근거 붙이기 `{rev}` → 저장된 초안·근거·버린 인용·통계 | 1번 | 예 |
| POST | `api/drafts/{did}/schedule/suggest` | 내용에서 일정 뽑기 `{years?}` → 행 제안 | 1번 | 예 |
| POST | `api/drafts/{did}/review` | 평가 관점 점검(초안에 저장) | 1번 | 예 |
| GET | `api/drafts/{did}/export.md` · `export.hwpx?preset=계획서\|보고서\|개조식` · `export.docx` · `gantt.svg` · `gantt.csv` | 내보내기(attachment) | - | 예 |
| GET | `/` | 간단판 화면(`ui.html`) | - | - |

## 초안·저장
- 초안 `{id, title, rev, kind, idea, period, scale, years, sections[{id, title, guide, budget, text, cites[], updated}], schedule{years, rows[{task, q[bool × years·4]}]}, review, refs[], origin}`. 시각은 UTC ISO.
- 사업 유형 `kind`: `rnd` 국가 R&D 신규과제 · `inst` 기관 고유사업(과제 개요 절 포함) · `basic` 기초·개인 연구 · `biz` 기술사업화·실증. 절 id 는 유형마다 다름(`meta.kinds`).
- **저장은 바꾼 칸만**(`title`·`idea`·`period`·`scale`·`sections[{id, text?, budget?, cites?}]`·`schedule`) + 마지막으로 받은 `rev`. 다른 창이 먼저 저장했으면 409 `{code: "conflict", draft}` — 덮어쓰지 않음. `rev` 는 사용자가 고치는 칸과 근거 붙이기만 올림(참고 자료·평가 점검은 그대로라 자동 저장과 부딪히지 않음). `cites` 는 남길 번호만 보냄(지우기만 됨 — 새 근거는 근거 붙이기로만).
- 상한: 초안 40개, 절 20,000자, 아이디어 4,000자, 일정 5년·30행, 권장 분량 100~20,000자.

## 참고 자료
- pdf·hwp·hwpx·hml·docx 는 kordoc CLI(옆 kordoc-local 의 것) `--format json --no-images` → 블록의 `pageNumber` 로 쪽마다. kordoc 이 없으면 pdf 는 `pdftotext -layout`(쪽마다), docx 는 표준 라이브러리(쪽 없음). md·txt 는 `\f` 나 `<!-- page N -->` 줄이 있으면 쪽, 없으면 1,500자 안팎 줄 범위(`where: "12–20줄"`).
- 자료 `{id: R1…, name, units, pages, chars, paged}`. 초안당 8개, 파일 20MB(본문 b64), 같은 이름은 ` (2)`.

## 절 작업 (`…/sections/{sid}/act`)
- `action`: `draft`(초안 쓰기 — 아이디어·다른 절 요약·참고 자료 발췌 4쪽, 목표 = 권장 분량) · `polish`(다듬기 — 사실·수치 그대로) · `bullets`(개조식 □ ○ -) · `shorter`(목표 = 지금의 70%, 권장 분량 이하) · `longer`(목표 = 지금의 135%, 권장 분량의 2배 이하). `text` 를 주면 그 글로(편집 칸의 저장 전 글), 없으면 저장된 글.
- 응답 `{text, chars, target, budget, questions[≤4], notes, unverified[{n, ctx}], placeholders, warnings[]}` — **저장하지 않음**(화면이 적용 후 저장).
  - `unverified`: 글 속 숫자 중 아이디어·기간·규모·모든 절·넣은 자료 발췌에 없는 것(10 이상·소수, `[입력 필요]` 안·〔근거 n〕 제외).
  - `warnings`: '세계 최초'·'국내 유일' 같은 단정 표현.
- 프롬프트 규칙: 입력·자료에 없는 수치·통계·정책·기관·인명·선행 연구 결과 금지(`[입력 필요: …]`·`[근거 자료 필요: …]`), 단정 표현 금지, 사용자 글·자료는 `<<<태그>>>` 데이터(구분자 흉내·제어문자 지움). JSON 스키마 안내 디코딩(vLLM `response_format: json_schema`) → 서버 검사 → 틀리면 JSON 객체 방식 + 앞 오류로 한 번 더.

## 근거 붙이기 (`…/sections/{sid}/cite`)
1. 절 글(옛 〔근거 n〕 표시를 지운 것)을 문장(S번호, 8자 이상, 80개까지)으로 나누고, 글자 2-gram 으로 가까운 참고 자료 단위 8개(U번호, 1,800자씩)를 고름.
2. LLM 이 `{cites: [{s, u, quote}]}`(8개까지) — 원문 그대로의 구절.
3. 서버 확인: 문장 번호·단위가 있는지, 인용이 그 단위 원문에 있는지(정규화 뒤 그대로면 1.0, 아니면 비슷한 구간 difflib 비율 **0.85 이상 + 인용 속 숫자가 모두 원문에**, 10자 미만 불통). 그 단위에 없으면 같은 자료부터 다른 단위를 찾아 **쪽을 바로잡고**(`fixed`), 어디에도 없으면 `dropped`(이유·AI 가 말한 위치).
4. 문장 뒤에 ` 〔근거 n〕`(n 은 절 안 번호, 글 순서) → 절 글·근거 저장, `rev` 올림. 응답 `{draft, cites, dropped, stats{asked, kept, dropped, fixed, sentences, units}}`.
- 요청 `rev` 가 지금 값과 다르면 409 `conflict`(먼저 저장해 맞출 것). 자료가 없거나 글이 없으면 400.
- 내보내기에는 글에 표시가 남은 근거만 `근거 n: 파일 · 3쪽 — “인용”` 으로.

## 일정 뽑기 · 평가 점검
- `schedule/suggest`: 목표·내용·추진 체계 절 + 아이디어 → `rows[{task, q}]` 12행까지(분기 1~연차×4, 범위 밖·거꾸로는 바로잡고 빈 이름·같은 이름은 뺌). 저장 안 함.
- `review`: 관점마다 `scores[{criterion, score 1~5 | null, comment}]`, `items[{criterion, section, issue, suggestion, quote, quote_ok, source}]`, `overall`. 절 id 는 스키마 enum, `quote` 는 서버가 그 절 글에서 확인 — 다른 절에 있으면 절을 바로잡고, 어디에도 없으면 지움(`quote_ok: false`). `source: "규칙"` 항목(`criterion: "형식"` 등)은 AI 없이: 빈 절, 분량 초과(120%)·부족(35%), `[입력 필요]` 남음, 필요성 절에 확인된 근거 없음, 일정 비어 있음. 결과는 초안 `review` 에 저장(`rev` 그대로).

## 내보내기
- `export.md`: 제목·사업 유형·기간·규모 표, `## n. 절` + 글(빈 절은 `[작성 필요]`), 추진 체계 절 뒤 간트 표(연차 칸마다 분기 `■■□□`), 근거 인용.
- `export.hwpx`: 위 Markdown(번호 없이)을 kordoc `generate --preset 계획서|보고서|개조식` → `validate`(헤더 `X-Hwpx-Valid`). kordoc 이 없으면 503.
- `export.docx`: 표준 라이브러리 OOXML(맑은 고딕, 제목·절 제목 스타일, □/○/-/· 들여쓰기, 간트 표 분기 칸 음영, 근거 ※ 줄).
- `gantt.svg`(이어진 분기는 막대 하나, 인쇄용 고정 색) · `gantt.csv`(UTF-8 BOM, `=`·`+`·`-`·`@` 로 시작하는 칸은 `'` 를 붙임).

## 제한·오류
- 사용자당 AI 작업 동시 1(409 `busy`)·10분 30번(429 `rate_limited`, `Retry-After`), 도구 전체 LLM 동시 2, 호출 시간 240초.
- LLM 이 없으면 AI 작업 503 `unavailable`(저장·참고 자료·일정 편집·내보내기는 됨). AI 답이 두 번 다 형식에 맞지 않으면 500(`AI 답이 형식에 맞지 않았습니다`).
- 입력 오류 400 `bad_request`, 없는 초안·절·자료·쪽 404, 본문이 너무 크면 413 `too_large`. 오류 문장의 서버 경로·내부 주소는 지움.
