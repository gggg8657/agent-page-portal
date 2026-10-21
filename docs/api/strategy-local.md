# SWOT·로직모델 API (`strategy-local`)
사업·과제·조직 설명(첨부 포함)으로 SWOT 2×2·TOWS 교차 전략·우선 전략·로직모델 초안을 LLM 으로 만들고, 서버가 근거 인용·전략 연결·목표 수치를 확인합니다. 편집(부분별 rev)·되돌리기·내보내기(SVG·PNG·PPTX·Markdown·HWPX)·KPI 도구 넘기기. 명세: `openapi/strategy-local.json`.

포털 경로 접두어: `/t/strategy-local/` (예: `api/analyses` → `http://<서버>/t/strategy-local/api/analyses`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(LLM 연결 여부) | - | - |
| GET | `api/meta` | LLM 상태, 내보내기 가능 형식(`export`), 칸·단계 이름, 연습 예시, 제한, accept | LLM 확인(60초 기억) | - |
| GET·POST | `api/analyses` | 분석 목록 · 만들기 `{title, subject, notes}` / 연습 예시 `{sample: true}` | - | 예 |
| GET·POST | `api/analyses/{aid}` | 분석 하나(`job` 포함) · 부분 저장 `{part, rev, data}` | - | 예 |
| POST | `api/analyses/{aid}/delete` | 지우기(초안 중이면 409) | - | 예 |
| POST | `api/analyses/{aid}/files` | 첨부 `{name, b64}` → 글로 바꿔 저장 | 문서 변환 수 초 | 예 |
| POST | `api/analyses/{aid}/files/{fid}/delete` | 첨부 빼기 | - | 예 |
| POST | `api/analyses/{aid}/generate` | AI 초안 `{part: swot\|tows\|logic\|all}` → 202 작업 | LLM 부분마다 1번(약 15~30초) | 작업(메모리) |
| GET | `api/analyses/{aid}/job` | 작업 상태(`stage`·`done/total`·`msg`·`elapsed`·`note`) | - | 예 |
| POST | `api/analyses/{aid}/undo` | AI 가 덮기 전으로 `{part}` | - | 예 |
| GET | `api/analyses/{aid}/export.{svg\|png\|pptx\|md\|hwpx}` | 내보내기(attachment). 그림은 `?part=swot\|logic` | HWPX 수 초 | 예 |
| POST | `api/analyses/{aid}/handoff/kpi` | KPI 도구로 넘길 logic-model.json | - | 예 |
| GET | `/` | 예전 간단 화면(`ui.html`) | - | - |

## 문서 모양
- `swot.{S,W,O,T}[]`·`logic.{assumptions,external}[]`: `{id, text, quote, basis, src}` — `src`: `input`(인용을 입력에서 확인)·`suggest`(검토 제안)·`user`(사람이 씀).
- `logic.{inputs,activities,outputs,short,mid,impact}[]`: 위 + 산출·성과 단계는 `indicator`·`target`.
- `tows.{SO,WO,ST,WT}[]`: `{id, text, uses[SWOT 항목 id]}` · `priorities[]`: `{key: "SO1", text, why}`.
- `revs.{meta,swot,tows,logic}`: 부분별 판 번호. `tows_stale`: 전략을 만든 뒤 SWOT 을 고침. `undo`: 되돌릴 수 있는 부분. `insufficient`: 입력이 모자라 SWOT 대신 낸 `{questions, example}`.

## 저장 (`POST api/analyses/{aid}`)
- `{part: "meta", rev, data: {title?, subject, notes{S,W,O,T}, questions?}}` · `{part: "swot", rev, data: {S,W,O,T}}` · `{part: "tows", rev, data: {tows, priorities}}` · `{part: "logic", rev, data: {...}}`.
- `rev` 가 `doc.revs[part]` 와 다르면 **409 `conflict` + 최신 `doc`**. AI 가 그 부분을 만드는 중이면 409 `busy`.
- 서버 검사: 글 300자·설명 6,000자·메모 2,000자, SWOT 칸 15·전략 종류 8·우선 전략 5·로직 단계 12개, 틀린 id 는 새로, 모르는 `src` 는 `user`. SWOT 에서 지운 항목을 가리키던 전략 연결은 걷고, 전략 연결은 짝 칸(SO 는 S·O …)의 항목만 남김.

## AI 초안 (`generate` → `job`)
- 입력: 대상 설명 + 칸별 메모 + 첨부 글(합쳐 16,000자) → `<<<INPUT>>>` 구분자 안 데이터(구분자 흉내·제어문자 지움). JSON 스키마 안내 디코딩(vLLM `response_format: json_schema`, 필수 칸 먼저) → 틀리면 JSON 객체 방식 + 앞 오류로 한 번 더.
- **swot**: 칸마다 2~5개 `{text, from_input, quote, basis}`. `from_input` 이고 인용이 입력에 있으면(정규화 — NFKC·소문자·기호·공백 — 뒤 비슷함 0.85 이상 + 인용 속 숫자가 모두 입력에 있어야) `input`, 아니면 `suggest`. 입력에 없는 숫자가 든 항목은 basis 에 '확인 필요'. `enough: false` 면 SWOT 을 바꾸지 않고 `insufficient`.
- **tows**: SWOT 를 S1·W2… 번호로 보여 주고 전략 `uses` 번호 → 항목 id(짝 칸·실제 번호만), 우선 전략 `key` 는 있는 전략만(중복 제외).
- **logic**: 여섯 단계 1~4개 + 가정·외부 요인. `target` 이 비었거나 입력에 없는 수치면 `'설정 필요'`.
- `all`: swot → tows → logic. swot 가 `insufficient` 면 tows 는 건너뛰고 logic 은 시도(`job.note`).
- 덮는 부분은 `prev` 에 하나 남김 → `undo`. 작업은 메모리에만(재시작 시 `job: null`).

## 내보내기
- SVG(서버가 그림, 한글 글꼴 이름 Pretendard·Noto Sans KR·NanumGothic) → PNG 는 `rsvg-convert -z 2`. PPTX 는 python-pptx **네이티브 도형**(SWOT·TOWS+우선 전략·로직모델·질문 슬라이드, 16:9). Markdown 표. HWPX 는 옆 kordoc-local 의 `kordoc generate --preset 보고서`(그림 PNG data URI + 표). 없는 형식은 400(`meta.export`).

## 넘기기 (`api/analyses/{aid}/handoff/kpi`)
- 응답 `{to: "kpi-local", path: "/tool/kpi-local", title: "전략 수립: <분석 이름> 논리모형", question: <목표 한 줄>, file: {name: "logic-model.json", type: "application/json", text}, count}`.
  `text` 는 kpi-local 과 정한 약속 JSON `{goal, context, items: [{level, text}]}` — level 은 투입·활동·산출·단기성과·중기성과·영향(로직모델 여섯 단계 그대로, 빈 칸 제외). goal = 분석 대상 한 줄(없으면 설명 첫 줄), context = 대상 설명(2,000자). **자료만 만들고 다른 도구를 부르지 않습니다.** 포털 화면이 `src/lib/handoff.ts` 로 받는 화면에 넘김(받는 도구가 등록돼 있을 때만 단추가 보임).

## 제한·오류
- 사용자당 초안 동시 1(409 `busy`)·1시간 30번(429 `rate_limited`, `Retry-After`), 도구 전체 동시 2(409), LLM 호출 동시 2, 호출 시간 제한 240초.
- LLM 이 없으면 `generate` 503 `unavailable`(직접 쓰기·내보내기는 됨). 설명이 너무 짧으면 400, SWOT 에 내부·외부 항목이 없으면 `tows` 400.
- 첨부 20MB(본문 b64), 분석마다 5개, 분석 100개. 지원 안 하는 형식·잘못된 b64 400, 본문이 너무 크면 413 `too_large`. 오류 문장의 서버 경로·내부 주소는 가림.
