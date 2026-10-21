# 실험 노트 정리 API (`labnote-local`)
흩어진 실험 메모·로그와 첨부(CSV·XLSX 측정 파일, 손글씨 사진)를 실험 노트 양식(날짜·제목·목적·시료·장비·조건·절차·측정 결과·관찰·다음 계획)으로 정리합니다. LLM 은 칸 나누기만 하고, **숫자 출처 확인·통계(평균·표준편차·n)·미기재 규칙은 서버**가 합니다. 노트 목록·찾기, 두 노트 조건 비교, HWPX·Markdown·XLSX 내보내기, 연구 산출물 발굴(yield-local) 연구일지로 넘기기. 명세: `openapi/labnote-local.json`.

포털 경로 접두어: `/t/labnote-local/` (예: `api/notes` → `http://<서버>/t/labnote-local/api/notes`)

> 숫자는 입력(메모·측정 파일·사진에서 옮긴 글)에 있는 것만. `derived.verify.bad` 에 있는 숫자는 화면에서 빨갛게 보여 주세요. 평균·표준편차는 LLM 이 아니라 `derived.stats`(서버 계산)만 씁니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM·그림 입력(`vision`)·kordoc 상태, 안내 문구, 제한, accept | LLM 확인(60초 기억), 그림 입력은 처음 한 번 뒤에서 확인 | - |
| GET·POST | `api/notes` | 목록(`q`·`from`·`to`·`tag`, 실험 날짜 최근 순) + 태그 수 · 만들기 `{title, memo}` / 연습 노트 `{sample: true}` | - | 예 |
| GET·POST | `api/notes/{nid}` | 노트(양식·첨부·`derived`·`job`) · 고치기 `{memo, title, tags, form, photo_text, dismissed}` | - | 예 |
| POST | `api/notes/{nid}/delete` | 노트 지우기(정리 중이면 409) | - | 예 |
| POST | `api/notes/{nid}/files` | 첨부 올리기 `{name, b64}` — CSV·XLSX·PNG·JPG·WebP | XLSX 읽기 1초 안팎 | 예 |
| GET | `api/notes/{nid}/files/{fid}` | 첨부 원본(사진은 그림, 측정 파일은 내려받기) | - | 예 |
| POST | `api/notes/{nid}/files/{fid}/delete` | 첨부 빼기(그 측정 파일에서 온 결과 행도 뺌) | - | 예 |
| POST | `api/notes/{nid}/build` | 정리 시작 `{overwrite}` → 202 작업 | 사진 장마다 LLM 1 + 양식 LLM 1(약 15–30초) | 작업(메모리) |
| GET | `api/notes/{nid}/job` | 작업 상태(`stage` photo·extract·done, `msg`, `elapsed`) | - | 예 |
| GET | `api/compare?a=&b=` | 두 노트 조건·시료 차이(바뀜·같음·한쪽만)·통계 | - | 예 |
| GET | `api/notes/{nid}/export.md` · `.xlsx` · `.hwpx` | 내보내기(attachment). HWPX 는 kordoc, 응답 머리 `X-Hwpx-Valid` | HWPX 1–3초 | 예 |
| POST | `api/notes/{nid}/handoff` | 연구일지(yield-local)로 넘길 자료 | - | 예 |
| GET | `/` | 예전 화면(`ui.html`, 보기 위주) | - | - |

## 첨부
- 판별은 맨 앞 바이트: PNG·JPEG·WebP = 사진(8MB, 노트당 4장), `PK` = XLSX(풀린 크기 80MB, DOCTYPE·ENTITY 선언 막음), 그 밖 = CSV(10MB, utf-8·cp949, 쉼표·탭·세미콜론). `.xls`·HEIC 는 400.
- 측정 파일 → 결과 행: 첫 줄 머리글, 숫자만 든 열 = 항목(머리글 `경도(HV)`·`두께 [mm]` 에서 단위), 글자 열 = 시료 이름, `반복·회차·횟수·No·#·n·run·trial` 열 = 반복 번호(항목 아님), 날짜 열은 시료 쪽. **칸 값은 글자 그대로**. 시트 5개 × 2,000줄 × 40열까지.
- 사진은 정리할 때 글로 옮김(`files[].text`, `text_model`) — 사용자가 `photo_text` 로 고칠 수 있음(`text_model` 에 '(고침)').

## 정리 (`build` → `job`)
1. 사진: `meta.vision` 이 참이면(OpenAI 호환 `image_url` 데이터 URI — 운영 vLLM gemma4:31b 는 받음) 장마다 옮겨 적기(LaTeX 기호는 글자로). 거짓이면 건너뛰고 `built.warnings` 에 안내.
2. 양식: 메모(앞 16,000자)·사진 글·측정 파일 머리 몇 줄을 `<<<MEMO>>>`·`<<<PHOTO>>>`·`<<<DATA>>>` 구분자 안 데이터로, JSON 스키마 안내 디코딩(vLLM `response_format: json_schema`) → 서버 검사(틀리면 JSON 객체 방식 + 앞 오류로 한 번 더). LLM 규칙: 숫자 그대로·계산 금지·측정 파일 값은 옮기지 않음·없는 것은 빈 칸·평가 표현 금지.
3. 서버: 측정 파일 행을 결과표에 그대로(`src: "file:<fid>"`, `ref: "파일 N행"`) — 결과표 400행까지, 넘으면 `built.warnings`. 날짜를 `YYYY-MM-DD` 로(연도 없으면 노트를 만든 해, `built.warnings` 에 알림). 태그는 비어 있을 때만 채움.
- 양식이 이미 있으면(직접 쓴 것 포함) `overwrite: true` 없이는 409 `confirm`.

## 서버 확인 (`derived`, 노트를 볼 때마다 다시 셈)
- **`verify`**: 결과(`value`·`note`)·조건(`value`)·시료(`amount`·`spec`·`note`) 칸과 목적·절차·관찰·다음 계획 글의 숫자마다 입력에 같은 값이 있는지. 같은 값 = 십진수로 같음(1.20 = 1.2, `1.2×10^-3` = `1.2e-3`), 음수는 입력에도 부호. `cells` 는 `"results.3.value"` 같은 경로 → `[{num, ok, src}]`, `bad` 는 입력에 없는 숫자, `photo_only` 는 사진 글에만 있는 숫자.
- **`stats`**: (시료·항목·단위·출처)가 같은 숫자 하나짜리 값이 2개 이상이면 n·평균·표본 표준편차(n−1)·최소·최대·값 목록. 출처: 측정 파일마다 따로(`src: "file:<fid>"`), 메모·직접 쓴 값끼리(`src: ""`). 소수 자릿수 = 입력 최대 + 1.
- **`checks`**: 규칙(`by: "rule"`) — 날짜·목적·시료·조건·절차·결과 빔, 단위 없는 결과·조건 값, 반복 횟수(n ≥ 2 묶음도, '3회'·'n=5' 같은 말도 없으면), 온도(`level: info`), 가열·열처리가 있는데 분위기 없음 + LLM 질문(`by: "ai"`, 규칙이 맡는 날짜·목적·반복·횟수·단위·온도·분위기 칸 이름이면 뺌). `dismissed` 에 넣은 id 는 `dismissed: true`.

## 비교 (`api/compare`)
- 조건은 `item`, 시료는 `name` 으로 짝(공백·기호·대소문자 무시). 값은 숫자면 십진수로(600 = 600.0), 아니면 글로 비교 → `status: same|changed|only_a|only_b`. `summary` 는 조건 수, `stats` 는 같은 항목·단위·시료 키의 n·평균·표준편차를 나란히, `procedure` 는 단계 수·같은지.

## 내보내기·넘기기
- Markdown: 양식 그대로(통계는 '서버 계산값'과 식, 미기재 체크는 확인함 뺀 것). XLSX: 개요·시료·재료·장비·조건·측정 결과(출처·수치 확인 열)·통계·미기재 체크·원본 측정 표(3개 파일 × 2시트까지). 끝 0 이 있는 소수(12.10)·앞 0 은 글 칸으로 남겨 유효숫자를 지킴.
- HWPX: Markdown(절차는 번호 표)을 kordoc `generate --preset 보고서 --no-cover --no-toc` 로 만들고 `validate`. kordoc 이 없으면 503 `unavailable`.
- 넘기기 `handoff`: `{to: "yield-local", path: "/tool/yield-local", params: {kind: "log"}, title, file: {name, text}, warning}` — 첫 줄 `## YYYY-MM-DD 제목`(yield 가 날짜 줄로 연구일지 항목을 나눔). **이 API 는 자료만 만들고 yield 를 부르지 않습니다.** 포털 화면이 `src/lib/handoff.ts`(sessionStorage 한 번)로 넘기고, yield 화면에서 사용자가 과제를 골라 넣습니다.

## 제한·오류
- 사용자당 정리 동시 1(409 `busy`)·1시간 20번(429 `rate_limited`, `Retry-After`), 도구 전체 정리 동시 2(409 `busy`)·LLM 호출 동시 2, 호출 시간 제한 240초(사진 180초).
- LLM 이 없으면 `build` 503 `unavailable`(양식 직접 쓰기·수치 확인·통계·내보내기는 됨).
- 입력 오류 400 `bad_request`(지원 안 하는 형식, 표 없는 CSV, 잘못된 b64·양식, 빈 노트 정리 …), 없는 노트·첨부 404, 본문이 너무 크면 413 `too_large`, 정리 중 저장·지우기·첨부 바꾸기 409 `busy`.
- 노트 300개, 노트당 첨부 10개(사진 4장), 메모 60,000자.
