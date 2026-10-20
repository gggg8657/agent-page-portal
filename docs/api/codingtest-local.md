# 코딩테스트 연습 API (`codingtest-local`)
문제 은행(약 100문제, 입문~고급·실무형)을 Python 으로 풀고 제출하면 격리 컨테이너에서 채점합니다. LLM 은 단계 힌트·왜 틀렸나·정답 리뷰에만 씁니다(없으면 그 셋만 503). 명세: `openapi/codingtest-local.json`.

포털 경로 접두어: `/t/codingtest-local/` (예: `api/submit` → `http://<서버>/t/codingtest-local/api/submit`)

## 요약 표
| 메서드 | 경로 | 하는 일 | 오래 걸림/LLM | 사용자별 데이터 |
|---|---|---|---|---|
| GET | `api/health` | 살아 있는지(문제 수) | - | - |
| GET | `api/meta` | 수준·유형·언어, 채점기·AI 코치 상태와 안내 글, 제한 | Docker·LLM 확인(60초 기억) | - |
| GET | `api/problems` | 문제 목록 + 내 상태(new·tried·solved)·제출 수 | - | 예 |
| GET | `api/problems/<id>` | 문제 하나(숨김 테스트·참고 풀이 없음) + 내 기록·연 힌트·리뷰 | - | 예 |
| POST | `api/run` | 예제 실행(또는 `input` 하나로 직접 실행) | 격리 컨테이너 1~5초 | 초안 저장 |
| POST | `api/submit` | 제출 — 예제+숨김 테스트 채점, 기록 | 격리 컨테이너 1~5초(시간 초과면 더) | 예 |
| POST | `api/draft` | 쓰던 코드 저장 | - | 예 |
| POST | `api/hint` | 단계 힌트 `level` 1·2·3(앞 단계를 연 뒤) | LLM 수 초~수십 초 | 예(저장) |
| POST | `api/why` | 마지막 틀린 제출의 원인 설명 | LLM | 예 |
| POST | `api/review` | 정답 코드 리뷰(`refresh` 로 새로) | LLM | 예(저장) |
| POST | `api/solution` | 참고 풀이(기록에 '풀이 봄') | - | 예 |
| GET | `api/records` | 대시보드: 수준·유형별 진도, 약한 유형, 지금 수준, 추천, 최근 제출 | - | 예 |
| GET | `api/recommend` | 다음 문제 추천(규칙) | - | 예 |
| GET | `/` | 예전 화면(`ui.html`) | - | - |

## 채점
- 제출 하나 = 컨테이너 하나(`docker run --rm -i --network none --cpus 1 --memory 1g --pids-limit 256 --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user <uid>`, 이미지 `dongjukim/agent-portal:v2`, 이름 `dongjukim-codingtest-*`). 포털 개발자 업로드 자가검증과 같은 격리 설정입니다. 볼륨 없음 — 코드·입력은 표준입력으로, 정답 출력은 해시만 컨테이너에 들어갑니다.
- 테스트마다 CPU 시간 제한(문제의 `time_limit`), 메모리(`memory_mb`), 출력 1 MiB 상한. 컨테이너 전체 시간 제한은 테스트 수에 비례(최대 120초). 도구 전체 동시 컨테이너 3개.
- 판정 `verdict`: `AC` 정답 · `WA` 오답 · `TLE` 시간 초과 · `MLE` 메모리 초과 · `RE` 런타임 오류 · `OLE` 출력 초과 · `CE` 컴파일 오류(문법) · `SYS` 채점 불가(Docker 없음 등, 기록 안 함) · `DONE` 직접 입력 실행 완료.
- 비교: 줄마다 공백 묶음을 하나로, 앞뒤 빈 줄 무시. `checker: "float:<오차>"` 문제는 수 토큰을 절대·상대 오차 안에서 비교.
- 제출은 처음 실패에서 멈춥니다. 숨김 테스트 실패는 `failed.number`(번호)만 — 입력·기대 출력·내 출력은 주지 않고, 런타임 오류면 마지막 오류 줄만.

## 제한·오류
- 사용자당 채점 동시 1(409 `busy`)·10분 40번(429 `rate_limited`, `Retry-After`). AI 코치 동시 1·10분 20번, 도구 전체 LLM 동시 2.
- LLM 이 없으면 `api/hint`·`api/why`·`api/review` 는 503 `unavailable`(이미 저장된 힌트·리뷰는 `api/problems/<id>` 의 `record.hint_texts`·`record.review` 로 볼 수 있음).
- 입력 오류 400 `bad_request`, 없는 문제 404 `not_found`, 본문 512KB 초과 413. 코드 64KB, 직접 입력 256KB 까지.

## 코칭 안전장치
- 사용자 코드·오류·예제는 구분자(`<<<USER_CODE>>>` …)로 감싼 데이터로 넣고 구분자 흉내는 지웁니다. 숨김 테스트 내용은 LLM 에 보내지 않습니다.
- 참고 풀이는 코치만 보는 자료로 넣고, 답에서 코드 블록을 지우며(③ 의사코드는 파이썬으로 실행되지 않는 블록만 남김), 참고 풀이 줄이 많이 겹치면 다시 묻고 그래도 겹치면 버립니다.
