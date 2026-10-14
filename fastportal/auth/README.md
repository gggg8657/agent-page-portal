# 계정 저장소 바꾸는 법 (AUTH_BACKEND)

> **현재 계정 저장소는 임시**(포털 내부 DB `_data/portal.db` 의 `users` 표)이며, 운영 시 **사내 챗봇 계정 DB 로 대체 예정**입니다(아이디·비밀번호 하나로).

포털은 "아이디·비밀번호가 맞는가"만 계정 저장소에 묻습니다. 그 밖의 것은 저장소가 바뀌어도 포털이 그대로 갖고 있습니다.

| 계정 저장소가 맡음 | 포털(portal.db)이 그대로 맡음 |
|---|---|
| 아이디·비밀번호 확인, (local 만) 가입·PIN 바꾸기·PIN 초기화 | 세션·쿠키·로그인 유지·실패 잠금, 부서·부서장·승인, 관리자(`AGENT_OWNERS`), 비활성화·익명화, 테마, 사용자별 도구 데이터(`X-Agent-User` → `WORKSPACE/u/<아이디>`) |

## 고르기

| `AUTH_BACKEND` | 뜻 |
|---|---|
| `local` (기본) | `auth/local.py` `LocalBackend` — 지금 동작 그대로(PIN PBKDF2-SHA256 60만 번, 예전 해시는 로그인 때 다시 해시, 비활성·정지·익명화는 로그인 불가) |
| `external` | `auth/external.py` `ExternalBackend` — 챗봇 계정 DB/API. **지금은 틀만**: 설정이 없으면 로그인이 503 + "아직 연결되지 않았습니다" |
| `<모듈>:<클래스>` | `AuthBackend` 를 이은 클래스(PYTHONPATH 에서 찾음) — 시험용 가짜 저장소(`tests/contract_fake.py`)나 사내 전용 연동 |

틀린 값이면 포털이 뜨지 않습니다(기동 때 확인). 관리 화면(관리자만)에 "계정 저장소: 임시(포털 내부 DB)" 처럼 표시되고, `/api/admin` 의 `auth_backend` 에도 있습니다.

## external 일 때 바뀌는 것

- 로그인 화면의 '가입' 탭, 계정 설정의 'PIN 바꾸기', 관리 화면의 'PIN 초기화'가 사라지고 안내 문구(`AUTH_EXTERNAL_GUIDE`)가 보임. API(`/api/signup`·`/api/pin`·`/api/admin/reset`)는 403 + 같은 안내.
- 로그인 성공 → `users` 표에 같은 아이디로 '포털 쪽 사용자 정보' 행을 만듦(처음 한 번, 이름은 저장소 값, PIN 열은 비움). 부서는 지금처럼 요청·승인.
- 포털에서 비활성화·정지·익명화한 계정은 외부 비밀번호가 맞아도 로그인 불가.
- 아이디 `owner` 는 로컬 주인 자리라 외부 아이디로도 로그인 불가.
- 화면은 `GET /api/auth`(로그인 전에도) `{signup, password_change, guide}` 로 판단(포털 HTML 화면은 서버가 `window.AG_AUTH` 로 넣어 줌).

## ExternalBackend 채우는 법

챗봇 쪽에서 받아야 할 것: DB 종류(또는 인증 API), 사용자 표·열 이름(아이디·이름·부서·사용 중지), 비밀번호 해시 방식(bcrypt·PBKDF2·argon2 등과 매개변수), 접속 방법(읽기 전용 계정·주소), 아이디 대소문자 규칙.

1. `auth/external.py` 의 TODO 두 곳만 채웁니다.
   - `_lookup(uid)` → `{"id", "name", "dept", "disabled", …해시 정보}` 또는 `None`. DB 면 읽기 전용 연결로 SELECT, API 면 조회 호출.
   - `_check(row, secret)` → 비밀번호가 맞으면 `True`(해시는 `hmac.compare_digest` 로 비교, API 면 로그인 호출 성공 여부).
   - 장애·시간 초과는 `AuthError("짧은 안내")` — 화면엔 그 문구만, 상세(주소·스택)는 `print` 로 로그에만.
   - 아이디 규칙이 다르면 `normalize_id` 를 덮어씀(기본: 앞뒤 공백 빼고 소문자).
2. 필요한 드라이버(DB 클라이언트 등)를 `fastportal/requirements.txt` 에 더하고 라이선스를 `THIRD_PARTY_NOTICES.md` 에 적습니다.
3. 설정은 환경변수로만(저장소에 적지 않음): `AUTH_BACKEND=external`, `AUTH_EXTERNAL_KIND=db|api`, `AUTH_EXTERNAL_URL` 또는 `AUTH_EXTERNAL_DSN`(비밀번호는 파일로 넘기기를 권장), `AUTH_EXTERNAL_TIMEOUT`(초, 5), `AUTH_EXTERNAL_LABEL`, `AUTH_EXTERNAL_GUIDE`.
4. **관리자 아이디**: `AGENT_OWNERS` 를 외부 아이디로 다시 정합니다(기본값 아이디가 외부에 있으면 그 사람이 관리자가 됨).
5. **옮기기**: 지금 포털 계정의 아이디가 챗봇 아이디와 같으면 부서·데이터가 그대로 이어집니다. 다르면 바꾸기 전에 아이디 대응표를 만들어 `users`·`dept_heads`·`requests`·`audit`·`jobs`·`events` 와 도구 데이터 폴더(`_data/<도구>/u/<아이디>`)를 함께 옮겨야 합니다.
6. 확인: `python3 fastportal/tests/contract_fake.py` 의 "계정 저장소 바꿔 끼우기" 절(가짜 외부 저장소로 로그인 성공·가입/PIN 변경 막힘·비활성 계정 거절)이 그대로 통과하는지, 실제 저장소로 로그인·틀린 비밀번호·잠금(10분 5번)·저장소 꺼짐(503)을 손으로 한 번씩.

## 공통 틀 (`auth/base.py`)

```python
class AuthBackend:
    name, label, guide                       # 표시용
    supports_signup, supports_password_change
    normalize_id(raw) -> str
    verify(c, uid, secret) -> User | None    # 저장소 장애는 AuthError
    get_user(c, uid) -> User | None
    list_users(c) -> [User]                  # 선택(점검·동기화용)
    check_new / create_user                  # 가입(local 만)
    check_secret_rule / set_password         # PIN 바꾸기·초기화(local 만)
```

`c` 는 portal.db 연결(요청 하나의 트랜잭션)입니다. 외부 저장소는 쓰지 않아도 되고, 포털 DB 에 쓰는 일은 포털(`accounts.ensure_profile`)이 합니다.
