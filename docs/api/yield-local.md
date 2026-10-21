# 연구 산출물 발굴 API (`yield-local`)
연구 과제의 git 이력(zip·git bundle·`git log -p --stat` 글)과 연구일지에서 따로 낼 만한 중간 연구 산출물 후보를 LLM 으로 찾고, 근거 인용을 서버가 원문에서 확인(없으면 탈락)한 뒤, 사용자가 올린 기존 산출물(논문·보고서)과 비교해 이미 냄·일부·아직 안 냄으로 나눕니다. kordoc 초안 넘기기·Markdown 보고서. 명세: `openapi/yield-local.json`.

포털 경로 접두어: `/t/yield-local/` (예: `api/projects` → `http://<서버>/t/yield-local/api/projects`)

> 새로움 판단은 **사용자가 올린 산출물하고만** 비교합니다(문헌 조사 아님). `api/meta` 의 `notice` 를 화면에 늘 보여 주세요. LLM 답의 '세계 최초' 같은 표현은 서버가 지웁니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM·임베딩 상태, 자료 읽기 프로그램(git·pdftotext·kordoc), 종류·형태·판정, 안내 문구, 제한, accept | LLM 확인(60초 기억) | - |
| GET·POST | `api/projects` | 과제 목록 · 만들기 `{title}` / 연습 과제 `{sample: true}` | - | 예 |
| GET·POST | `api/projects/{pid}` | 과제(자료·마지막 결과 `result`·작업 `job`) · 이름 바꾸기 `{title}` | - | 예 |
| POST | `api/projects/{pid}/delete` | 과제 지우기(분석 중이면 409) | - | 예 |
| POST | `api/projects/{pid}/sources` | 자료 올리기 `{kind: git\|log\|output, name, b64 \| text}` | git zip·bundle·문서 변환 수 초 | 예 |
| POST | `api/projects/{pid}/sources/{sid}/delete` | 자료 빼기 | - | 예 |
| POST | `api/projects/{pid}/analyze` | 분석 시작 `{stage: all\|novelty}` → 202 작업 | LLM 덩어리 수 + 묶기 1 + 비교 ⌈후보/4⌉번 | 작업(메모리) |
| GET | `api/projects/{pid}/job` | 작업 상태(`stage`·`done/total`·`msg`·`elapsed`) | - | 예 |
| GET | `api/projects/{pid}/item?ref=` | 근거 항목 원문(커밋·일지 항목·산출물 구절) | - | 예 |
| GET | `api/projects/{pid}/report.md?ids=K1,K2` | Markdown 보고서(attachment) | - | 예 |
| POST | `api/projects/{pid}/handoff` | kordoc 초안으로 넘길 자료 `{ids}` | - | 예 |
| GET | `/` | 예전 화면(`ui.html`) | - | - |

## 자료 (`api/projects/{pid}/sources`)
- **git**: 맨 앞 바이트로 판별 — zip(`PK`)은 임시 폴더에 풀어 `.git`(깊이 3까지, 또는 bare 저장소)을 찾고, bundle(`# v2/v3 git bundle`)은 `git clone --bare`. 둘 다 `git log --stat -p -U1 --no-ext-diff --no-textconv --date=iso-strict` 로 글을 만든 뒤 붙여 넣은 글과 같은 파서로 읽음. **올린 저장소의 `.git/config` 는 최소 설정으로 바꾸고 hooks·info/attributes 를 지우며**, 전역·시스템 설정도 읽지 않음(`GIT_CONFIG_NOSYSTEM`·`GIT_CONFIG_GLOBAL=/dev/null`). zip 은 절대 경로·`..`·심볼릭 링크를 막고 풀린 합 400MB·5만 개까지. 커밋 1,500개까지(넘으면 최근 것, `summary.truncated`).
- 커밋 항목: `ref`(해시 12자)·`date`·`title`·`text`(메시지 + 변경 파일 + 줄인 diff — 파일당 바뀐 줄 40개, 잠금·빌드 파일은 이름만).
- **log**: 날짜 줄(`## 2026-04-06`·`2026.04.06`·`2026/4/6`·`2026년 4월 6일`, 줄 앞 25자 안)로 항목, ref `파일#L줄`. 날짜 줄이 없으면 파일 이름의 날짜로 통째로, 그것도 없으면 40줄씩(날짜 없음). 같은 이름 파일은 ` (2)` 를 붙임.
- **output**: 마크다운 제목·빈 줄·쪽(pdf)으로 나눈 600자 안팎 구절, ref `P번호`(과제 안에서 이어짐), `label` = `파일 · 쪽/줄 · 제목`.
- 문서 변환: md·txt 그대로, pdf 는 `pdftotext -layout`(쪽마다), docx·hwp·hwpx·hml 은 kordoc CLI(옆 kordoc-local 의 것, 없으면 docx 만 표준 라이브러리). 파일 하나 40MB(본문 b64), 붙여 넣은 글 8MB.

## 분석 (`api/projects/{pid}/analyze` → `…/job`)
1. **덩어리**: 커밋·일지 항목을 날짜순(같은 날은 일지 먼저)으로 섞어 12,000자 × 최대 10개. 넘치면 diff 줄 수를 24→12→6→2→0 으로 줄이고, 그래도 넘치면 최근 쪽만(`skipped_items`).
2. **후보 찾기**: 덩어리마다 LLM, JSON 스키마 안내 디코딩(vLLM `response_format: json_schema`) → 틀리면 JSON 객체 방식 + 앞 오류로 한 번 더. 자료는 `<<<SOURCES>>>` 구분자 안 데이터(구분자 흉내·제어문자 지움). 후보 `{title, type, claim, why, evidence[{source, ref, quote}], form}`, 덩어리당 6개까지. 덩어리 하나가 실패해도 계속(`chunk_fails`).
3. **근거 검증(서버)**: ref → 항목(커밋 해시 앞 7자 이상, `파일#L줄`·줄 범위, 파일+날짜, 날짜만). 인용은 정규화(NFKC·소문자·diff 머리표·마크다운 기호·공백) 뒤 그대로 있으면 1.0, 아니면 4글자 조각으로 위치 후보를 찾아 difflib 비율 — **0.85 이상 + 인용 속 숫자가 모두 원문에 있어야** 통과(`YIELD_QUOTE_MIN`). 10자 미만 인용은 불통. ref 가 틀렸지만 같은 인용이 다른 항목에 있으면 바로잡고 `ref_fixed`. 확인된 근거가 없는 후보는 `dropped`(이유·근거별 판정과 함께), 수는 `stats.dropped`·`stats.evidence - stats.evidence_ok`.
4. **합치기**: bge-m3 코사인 0.88(같은 근거 항목이면 0.80)·제목 비슷함 0.8, 임베딩이 없으면 글자 2-gram. 후보가 3개 이상이면 LLM 이 '같은 결과' 묶음을 고르고 서버가 번호·겹침·종류(도구·데이터셋은 다른 종류와 안 묶음)를 확인해 합침(`merged`, `merge_llm`).
5. **비교**: 산출물 구절·후보(제목+주장)를 bge-m3 로 임베딩해 후보마다 TOP 4 구절(`novelty.hits`) → LLM 이 4개씩 `이미 냄|일부|아직 안 냄` + `passage`(받은 구절 번호 중 하나) + `reason`. 받은 구절이 아닌데 '이미 냄·일부'면 '아직 안 냄'으로 낮추고 `note`. 산출물이 없으면 `compared: false`. `stage: "novelty"` 는 이 단계만 다시(산출물을 더 올린 뒤).
- 작업은 메모리에만(서버 재시작 시 `job: null`), 결과는 `result.json` 으로 남음. 시각은 UTC ISO.

## 넘기기 (`api/projects/{pid}/handoff`)
- 요청 `{ids: ["K2", …]}`(1~3개, '이미 냄'은 400). 응답 `{to: "kordoc-local", path: "/tool/kordoc-local", params: {task: "draft", preset: 보고서|개조식}, form, question, file: {name, text}, ids, chars}`.
- 제안 형태 → 프리셋·구성: 기술보고서·논문 short paper·특허 아이디어 → 보고서, 학회 발표·내부 메모 → 개조식. 요청 글에는 '자료에 없는 수치·문헌·기관을 지어내지 말 것, 문헌 대비 새로움 주장 금지, 부족하면 [확인 필요]'를 붙임.
- 자료 파일은 후보별 주장·이유·근거·비교 구절·근거 원문 발췌 Markdown, **11,000자 안**(kordoc 이 앞 12,000자를 읽음 — 넘치면 발췌를 줄임).
- **이 API 는 자료만 만들고 kordoc 을 부르지 않습니다.** 포털 화면이 받는 쪽 화면에 넘김(브라우저 안 `sessionStorage` 한 번 읽기, `src/lib/handoff.ts`) → kordoc 화면이 작업 '공문서 초안'·프리셋·요청 글·자료 파일을 채우고, 실행은 사용자가 누름.

## 제한·오류
- 사용자당 분석 동시 1(409 `busy`)·1시간 6번(429 `rate_limited`, `Retry-After`), 도구 전체 분석 동시 2(409 `busy`)·LLM 호출 동시 2, 호출 시간 제한 240초, 후보 찾기 3,000토큰.
- LLM 이 없으면 `analyze` 503 `unavailable`(자료 올리기·결과 보기·내보내기는 됨). 임베딩이 없으면 글자 비교로 대신(`result.embed: false`).
- 입력 오류 400 `bad_request`(지원 안 하는 형식, 커밋 없는 글, 안전하지 않은 zip, .git 없는 zip, 잘못된 b64 …), 없는 과제·자료·항목 404, 본문이 너무 크면 413 `too_large`.
- 과제 30개, 과제당 자료 30개, 과제당 항목 합 6,000개.
