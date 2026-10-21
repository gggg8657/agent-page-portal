"""ExternalBackend — 외부 계정 저장소(운영 예정: 폐쇄망 챗봇의 계정 DB 또는 인증 API)로 아이디·비밀번호를 확인. 지금은 틀만.
그쪽 DB 종류·사용자 표·해시 방식·접근 방법을 받으면 아래 TODO 두 곳(_lookup·_check)만 채우면 된다 — 자세히는 auth/README.md.

외부 저장소일 때 포털 동작:
  - 가입·PIN 바꾸기·관리자 PIN 초기화는 끔(supports_* = False) — 화면은 숨기고 API 는 403 + 안내(guide)
  - 로그인 성공 → 포털 users 표에 같은 아이디로 '포털 쪽 사용자 정보' 행을 만들거나 이름을 맞춤(PIN 열은 비움).
    부서·부서장·권한·세션·사용자별 데이터는 지금처럼 포털이 아이디를 키로 가짐 → 같은 아이디면 데이터가 그대로 이어짐
  - 포털에서 비활성화·익명화한 계정은 외부 비밀번호가 맞아도 로그인 불가(users.disabled)
  - 관리자: AGENT_OWNERS 의 아이디 — 외부 아이디와 겹치면 그 사람이 관리자가 되므로 운영 전에 반드시 외부 아이디로 다시 정할 것
  - shortcut: 세션 확인(sessions.py)은 포털 users.disabled 만 봄 — 챗봇에서 막힌 계정도 이미 받은 포털 세션(최대 30일)은 살아 있음.
    _lookup 을 채울 때 세션 확인에서도 get_user(...).disabled 를 몇 분 캐시해 보게 할 것

설정(환경변수 — 값은 배포 환경에만, 저장소에 적지 말 것):
  AUTH_EXTERNAL_KIND     api | db
  AUTH_EXTERNAL_URL      api: 인증 API 기본 주소(예: http://<챗봇 서버>/auth)
  AUTH_EXTERNAL_DSN      db: 읽기 전용 연결 문자열(또는 파일 경로) — 비밀번호는 AUTH_EXTERNAL_DSN_FILE 처럼 파일로 넘기는 것을 권장
  AUTH_EXTERNAL_TIMEOUT  초(기본 5)
  AUTH_EXTERNAL_LABEL    관리 화면 표시(기본 "외부(챗봇 계정 DB)")
  AUTH_EXTERNAL_GUIDE    가입·PIN 변경 대신 보여 줄 안내 문구"""
import os

from .base import AuthBackend, AuthError, User

NOT_READY = "외부 계정 저장소(챗봇 계정 DB)가 아직 연결되지 않았습니다 — 관리자에게 알려 주세요"


class ExternalBackend(AuthBackend):
    name = "external"
    supports_signup = False
    supports_password_change = False
    label = "외부(챗봇 계정 DB)"
    guide = "계정은 사내 챗봇과 같은 아이디·비밀번호를 씁니다 — 가입·비밀번호 변경은 챗봇에서 하세요"

    def __init__(self):
        e = os.environ
        self.kind = (e.get("AUTH_EXTERNAL_KIND") or "").strip().lower()
        self.url = (e.get("AUTH_EXTERNAL_URL") or "").strip()
        self.dsn = (e.get("AUTH_EXTERNAL_DSN") or "").strip()
        self.timeout = float(e.get("AUTH_EXTERNAL_TIMEOUT") or 5)
        self.label = e.get("AUTH_EXTERNAL_LABEL") or type(self).label
        self.guide = e.get("AUTH_EXTERNAL_GUIDE") or type(self).guide

    def ready(self):
        """연결 설정이 있는지(기동 때 경고용 — 실제 연결은 로그인 때)"""
        return (self.kind == "api" and bool(self.url)) or (self.kind == "db" and bool(self.dsn))

    # ── 채울 곳 ──────────────────────────────────────────────────────────
    def _lookup(self, uid):
        """TODO(챗봇 계정 DB 정보가 오면): 아이디 → {"id", "name", "dept", "disabled", 해시·솔트 등} 또는 None.
        db: 읽기 전용 연결로 SELECT(아이디 열 이름·대소문자 규칙 확인), api: GET <URL>/users/<id> 같은 조회.
        연결 실패·시간 초과는 AuthError(화면엔 짧은 문구, 상세는 print 로 로그에만)."""
        raise AuthError(NOT_READY)

    def _check(self, row, secret):
        """TODO: 비밀번호 확인 — 챗봇 DB 의 해시 방식(bcrypt·PBKDF2·argon2 등)으로 비교(hmac.compare_digest),
        또는 API 라면 POST <URL>/login {id, password} 의 성공 여부. 맞으면 True"""
        raise AuthError(NOT_READY)

    # ── 공통 틀 구현 ─────────────────────────────────────────────────────
    def verify(self, c, uid, secret):
        row = self._lookup(uid)
        if not row or row.get("disabled") or not self._check(row, secret):
            return None
        return User(str(row["id"]), str(row.get("name") or row["id"]), str(row.get("dept") or ""))

    def get_user(self, c, uid):
        row = self._lookup(uid)
        return User(str(row["id"]), str(row.get("name") or row["id"]), str(row.get("dept") or ""), bool(row.get("disabled"))) if row else None
