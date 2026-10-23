# 개발자 업로드 형식 안내

직접 만든 도구 폴더를 zip 으로 올리면 포털이 형식 검사 → 격리 자가검증 → 관리자 승인을 거쳐 포털 카드로 등록합니다.
승인된 도구는 **격리 컨테이너**에서 돕니다(인터넷 없음, GPU 없음, 포털의 LLM·임베딩만 쓸 수 있음).
샘플 `hello-local` 을 그대로 올려도 검사를 통과합니다.

## 폴더

```
hello-local/            ← 이름은 <영문 소문자>-local, 기존 도구와 겹치지 않게. zip 안에 이 폴더 하나만
  app.py                ← 필수. 컨테이너에서 `python app.py` 로 뜹니다
  README.md             ← 필수. 도구 설명
  agent.json            ← 필수. 포털 카드·사용법
  requirements.txt      ← 선택. 이름·버전 조건만(예: `requests==2.32.3`)
  selftest.py           ← 선택. 있으면 격리 검증에서 실행(종료 코드 0 = 통과, 60초 안)
  portal_auth.py        ← 선택. 없으면 포털이 넣어 줌
```

venv·node_modules·데이터·모델 파일은 빼고 올립니다(zip 50MB, 파일 2000개까지).

## agent.json

```json
{
  "title": "Hello 샘플 — 내 메모 · LLM 인사",
  "name": "Hello 샘플",
  "desc": "카드에 보일 한 줄 설명",
  "group": "기타",
  "tags": "샘플 개발자",
  "usage": "## 사용법\n\n포털 '사용법(?)'에 그대로 뜨는 마크다운(30자 이상)"
}
```

`title`·`desc`·`group`·`usage` 는 꼭 적습니다. `usage` 대신 `guide.md` 파일을 넣어도 됩니다.

## 실행 환경(포털이 넘기는 환경변수)

| 이름 | 값 | 쓰는 법 |
|---|---|---|
| `PORT` | 포털이 배정한 포트 | 이 포트로 HTTP 서버를 엽니다(고정 포트 금지) |
| `BIND` | 컨테이너에서는 `0.0.0.0` | `os.environ.get("BIND", "127.0.0.1")` — 코드에 0.0.0.0 을 직접 쓰지 않기 |
| `WORKSPACE` | `/data` | 데이터는 여기에만. 사용자별로 `WORKSPACE/u/<사용자>/` |
| `PORTAL_TOOL_KEY` | 도구 전용 서명 키 | `portal_auth` 가 씀. 화면·로그에 찍지 않기 |
| `LLM_BASE_URL` | `http://127.0.0.1:11440/v1` | OpenAI 호환 채팅(`/chat/completions`). 모델 이름은 `LLM_MODEL` |
| `EMBED_BASE_URL` | `http://127.0.0.1:11436` | Ollama 임베딩(`/api/embed`). 모델 이름은 `EMBED_MODEL` |

- 루트 파일 시스템은 읽기 전용입니다. 쓸 곳은 `WORKSPACE` 와 `/tmp`(256MB)뿐입니다.
- 인터넷·다른 도구·포털 서버에는 닿지 않습니다. 외부 주소를 코드에 넣지 마세요.
- CPU 2개·메모리 2GB·프로세스 256개 상한이 있습니다.
- 의존성은 `requirements.txt` 의 바퀴(wheel) 패키지만 승인 때 설치합니다. `setup.sh` 는 실행하지 않습니다.
  폐쇄망 내부 저장소에 있는 패키지만 됩니다 — 모두의 에이전트 → 개발자 업로드의 '폐쇄망 패키지 찾기'로 미리 확인하세요.

## portal_auth 사용법

포털을 거친 요청만 받게 하는 관문입니다. 서버 줄 하나만 바꾸면 됩니다.

```python
import os, portal_auth
from http.server import ThreadingHTTPServer

PORT = int(os.environ["PORT"])
BIND = os.environ.get("BIND", "127.0.0.1")
ThreadingHTTPServer((BIND, PORT), portal_auth.install(H, "hello-local")).serve_forever()
```

- `GET /healthz` 는 누구나 받습니다(포털이 켜짐을 확인할 때 씀). 직접 만들지 않아도 됩니다.
- 요청한 사람: `portal_auth.user(self)` (포털 계정 아이디, 서명으로 확인된 값)
- 관리자인지: `portal_auth.is_admin(self)`
- 오류 문장을 화면에 보낼 때: `portal_auth.scrub(문장)` — 서버 경로·내부 주소를 가림

## 화면 주소

포털은 도구를 `/t/<이름>/` 아래로 붙입니다. 화면의 링크·fetch 는 `api/memo` 처럼 **상대 주소**로 씁니다(`/api/memo` 는 포털 주소가 됩니다).

## 검사와 승인

1. 형식 검사 — 위 규칙(오류가 있으면 승인할 수 없음)과 위험 코드·외부 주소·폐쇄망 패키지 경고
2. 격리 자가검증 — 네트워크 없는 컨테이너에서 문법 검사, `selftest.py`, `app.py` 를 띄워 `/healthz` 확인
3. 관리자 승인 — 이미지 빌드 → 격리 컨테이너 시작 → `/healthz` → 포털 카드 등록 → 사용법 등록. 실패하면 모두 되돌리고 이유를 보여 줍니다
