# 성과지표(KPI) 설계 API (`kpi-local`)
사업 목표·현황(+첨부·논리모형)으로 성과지표 표 초안을 LLM 이 만들고, 서버가 입력에 없는 기준값·목표값 숫자를 지운 뒤(측정 필요·설정 필요) 지표별 SMART 와 지표 묶음 빈틈을 규칙으로 점검합니다. 기존 지표 점검·보완 지표, 지표 하나 보완 제안, XLSX(목표·실적·달성률이 엑셀 수식)·Markdown·HWPX 내보내기. 명세: `openapi/kpi-local.json`.

포털 경로 접두어: `/t/kpi-local/` (예: `api/sheets` → `http://<서버>/t/kpi-local/api/sheets`)

> 수치는 지어내지 않습니다. `api/meta` 의 `notice` 를 화면에 늘 보여 주세요. 서버가 지운 AI 수치는 행의 `baseline_note` 에 이유와 함께 남습니다.

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM·kordoc 상태, 유형·주기·SMART·빈틈 코드, 안내 문구, 기본 연차(한국 날짜 기준 내년부터 3년), 제한, accept | LLM 확인(60초 기억) | - |
| GET·POST | `api/sheets` | 설계 기록 목록 · 만들기 `{title, mode, goal, context, existing, years, logic, kpis}` | - | 예 |
| GET·POST | `api/sheets/{sid}` | 설계 하나(표·`checks`·`gaps`·`hints`·`job`) · 고치기(준 키만, `rev`) | - | 예 |
| POST | `api/sheets/{sid}/delete` | 지우기(작업 중이면 409) | - | 예 |
| POST | `api/sheets/{sid}/attach` · `…/attach/{aid}/delete` | 첨부 `{name, b64}`(kordoc 으로 글) · 빼기 | 변환 수 초 | 예 |
| POST | `api/sheets/{sid}/run` | AI 초안 `{mode: design\|audit}` → 202 작업 | LLM 1번(30~90초) | 작업(메모리) |
| GET | `api/sheets/{sid}/job` | 작업 상태 | - | 예 |
| POST | `api/sheets/{sid}/undo` | 이전 표로(한 단계, 다시 누르면 앞으로) | - | 예 |
| POST | `api/sheets/{sid}/suggest` | 지표 하나 보완 제안 `{kid}` | LLM 1번(약 10~20초) | 예 |
| GET | `api/sheets/{sid}/export.xlsx` · `.md` · `.hwpx` | 내려받기(attachment) | HWPX 는 kordoc 수 초 | 예 |
| GET | `/` | 예전 화면(`ui.html`) | - | - |

## 표의 행(`KpiRow`)
`id`(k+6자리) · `name` 지표명 · `type` 투입|과정|산출|성과(활동→과정, 단기·중기·장기성과·영향→성과로 받음) · `definition` · `formula` 산식(글) · `calc {kind: ratio|value, num, den, scale}`(엑셀 산식) · `unit` · `direction` up|down · `baseline`(글)·`baseline_value`(숫자 또는 null) · `targets {연도: 수|null}` · `target_rule {kind: input|rate|none|user, pct}` · `target_note` 목표 설정 방법 · `cycle` · `source` · `owner` · `rationale` 근거·선정 이유 · `risk` 왜곡 위험·짝 지표 · `core` · `logic_ref` 논리모형 번호 · `origin` llm|user|proposal|existing|logic · `baseline_note`(서버가 지운 수치).
- 고칠 때는 표 전체를 순서대로 `kpis` 로 보냄(순서 바꾸기·지우기·제안에서 더하기 모두 이것). 모르는 키는 버리고 길이는 자름. 같은 id 가 두 번 오면 뒤의 것에 새 id.
- `target_rule.kind = rate` 면 목표 = 기준값 × (1 + pct/100)^연차(기준값이 10 이상 정수면 정수, 아니면 소수 둘째 자리, 사사오입). 기준값을 바꾸거나 연차를 바꾸면 다시 계산. 손으로 목표를 넣는 화면은 `user` 로 보냄.

## AI 초안 (`run` → `job`)
- design: 지표 4~10개(성과 2개 이상, 논리모형 성과·산출 항목마다), `proposals` 대안·보완 지표, 핵심 3개(`core`), 확인할 질문, 왜곡 위험(`distortion`). 목표가 8자 안이면 400(무엇을 적을지 안내).
- audit: `existing` 을 한 줄씩 표로 옮김(`origin: existing`), 문제 코드(`ai_issues` — kid 와 「인용」 note), 보완 지표. 기존 지표가 없으면 400.
- LLM: JSON 스키마 안내 디코딩(vLLM `response_format: json_schema`) → 틀리면 JSON 객체 방식 + 앞 오류로 한 번 더. 입력은 `<<<목표>>>`·`<<<현황>>>`·`<<<기존 지표>>>`·`<<<논리모형>>>`·`<<<첨부 이름>>>` 구분자 안 데이터(구분자 흉내·제어문자 지움). 첨부는 프롬프트 14,000자 안 남은 자리만큼.
- **수치 거르기(서버)**: 입력 전체(첨부 포함, '1.2만'·'1,200' 도 읽음)의 숫자 모음에 없는 기준값은 '측정 필요', 근거 없는 목표값은 지우고 `target_rule: none`·'설정 필요' + 정하는 방법. `baseline_note` 에 지운 값.
- 표를 통째로 바꾸고 이전 표·제안·요약·질문은 `undo` 로 한 번 되돌릴 수 있게 남김.

## 점검(저장할 때마다, 서버 규칙)
- `checks[id]`: S 지표명·정의(막연한 말 '제고·강화·활성화…' + 짧은 정의) · M 산식·단위·출처(만족도면 문항·척도) · A 기준값·목표(비율 100 초과·음수·3배 넘는 상향·나빠지는 방향) · R 유형·허영 지표·선정 이유·목표 문장과 겹침 · T 주기·연차 목표. 각 `{level: ok|warn|bad, msg, fix}`, `score`(적합 수), `level`.
- `hints[id]`: 적합이 아닌 항목의 고칠 방향(AI 없이도).
- `gaps`: `outcome_only`·`no_outcome`·`no_baseline`·`vanity`·`satisfaction_only`·`no_target`·`no_source`·`single`(규칙) + 마지막 AI 문제(`distortion`·`unclear` 등). `by: rule|ai|rule+ai`, `items` 해당 지표 id, `notes` AI 설명.

## 보완 제안 (`suggest`)
- 응답 `{kid, hints, check, llm, summary, changes: [{field, value, why, before}], pair}`. 바꿀 수 있는 칸: 지표명·유형·정의·산식·단위·기준값·주기·출처·담당·근거·왜곡 위험·목표 설정 방법(목표 숫자는 안 바꿈). 입력에 없는 기준값 숫자는 '측정 필요'로, 지금 값과 같은 제안은 버림. `pair` 는 짝 지표(같은 수치 규칙).
- AI 가 꺼져 있으면 `llm: false` 와 규칙 방향만. 적용은 화면이 표를 고쳐 저장.

## 내보내기
- XLSX(openpyxl, chart-local 과 같은 방식 — 수식은 글로 넣고 엑셀이 계산): '지표 정의'(11칸 + 방향·목표 설정 방법·연 증감률·왜곡 위험·핵심, 유형·주기 목록 고르기), '실적·달성률'(지표 × 연도 — 목표 = 지표 정의 칸 참조, 비율 지표 실적 = 분자÷분모×배수, 달성률 = 실적/목표(↓ 는 목표/실적), 진척률 = (실적-기준)/(목표-기준), 넣는 칸 노란 바탕, 달성률 100% 이상·80% 미만 색), 'SMART 점검', '빈틈·질문'.
- Markdown: 목표 정리·지표 정의·기준값과 연차별 목표·근거·SMART·왜곡 위험·핵심·빈틈·제안·질문. HWPX: 같은 Markdown 을 `kordoc generate --preset 보고서 --no-cover --no-toc`.

## 넘겨받기(브라우저 안, `src/lib/handoff.ts`)
- 전략 도구 → `{from: "strategy-local", to: "kpi-local", title, question?: 목표 한 줄, files: [{name: "logic-model.json", text: JSON {goal, context?, items: [{level, text}]}}]}`. 화면이 처음 그릴 때 한 번 꺼내 목표·현황·논리모형 항목을 새 설계 칸에 채우고, 실행은 사용자가 누름.

## 제한·오류
- 사용자당 AI 작업 동시 1(409 `busy`)·1시간 12번(초안·보완 제안 합쳐서, 429 `rate_limited`, `Retry-After`), 도구 전체 초안 동시 2(409)·LLM 호출 동시 2, 호출 시간 제한 240초.
- LLM 이 없으면 `run` 503 `unavailable`(표 직접 쓰기·점검·내보내기는 됨). 저장 rev 가 다르면 409 `conflict`. 입력 오류 400, 없는 설계·첨부·지표 404, 본문이 너무 크면 413.
- 설계 60개, 지표 30개, 첨부 5개(20MB, 60,000자까지), 연차 1~5년(2000~2100), 목표 2,000자·현황 12,000자·기존 지표 8,000자.
