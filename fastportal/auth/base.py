"""계정 저장소의 공통 틀(AuthBackend) — 아이디·비밀번호(PIN)를 '어디서' 확인하느냐만 바꿔 끼운다.
부서·부서장·권한·세션·사용자별 데이터(X-Agent-User)는 계정 저장소와 무관하게 포털(portal.db)이 아이디를 키로 갖고 있는다.
바꾸는 법: fastportal/auth/README.md"""
from dataclasses import dataclass


@dataclass
class User:
    """계정 저장소가 돌려주는 사용자. id 는 포털 안 키(세션·부서·데이터 폴더 WORKSPACE/u/<id>)가 되므로 바뀌지 않는 값이어야 함"""
    id: str
    name: str = ""
    dept: str = ""          # 저장소가 아는 부서(참고용 — 포털 부서는 승인 절차로 정함)
    disabled: bool = False  # 저장소 쪽에서 막힌 계정


class AuthError(Exception):
    """계정 저장소가 응답하지 않거나 설정이 틀림(아이디·PIN 이 틀린 것과 다름) — 화면엔 message 만, 내부 주소·스택은 로그에만"""


class Unsupported(PermissionError):
    """이 계정 저장소에서는 할 수 없는 일(예: 외부 저장소에서 가입·PIN 변경) — 403 + 안내 문구"""


class AuthBackend:
    """모든 메서드의 c 는 portal.db 연결(같은 트랜잭션 — local 은 users 표를 씀, 외부 저장소는 안 써도 됨)."""
    name = "base"
    label = ""                        # 관리 화면 표시: 예 "임시(포털 내부 DB)"
    supports_signup = False           # 포털 가입 화면·/api/signup
    supports_password_change = False  # PIN 바꾸기(/api/pin)·관리자 PIN 초기화(/api/admin/reset)
    guide = ""                        # 가입·PIN 변경을 막을 때 보여 줄 안내

    def normalize_id(self, raw):
        """로그인 칸에 적은 아이디 → 포털 키. 기본: 앞뒤 공백 빼고 소문자(지금 포털 규칙)"""
        return str(raw).strip().lower()

    def verify(self, c, uid, secret):
        """아이디·비밀번호가 맞고 막히지 않았으면 User, 아니면 None. 저장소 장애는 AuthError"""
        raise NotImplementedError

    def get_user(self, c, uid):
        """아이디 → User 또는 None(없음)"""
        raise NotImplementedError

    def list_users(self, c):
        """저장소가 아는 사용자 목록(User). 관리 화면은 포털 users 표를 보므로 필수는 아님 — 동기화·점검용"""
        return []

    def create_user(self, c, uid, name, secret):
        raise Unsupported(self.guide or "이 계정 저장소에서는 포털에서 가입할 수 없습니다")

    def set_password(self, c, uid, secret):
        raise Unsupported(self.guide or "이 계정 저장소에서는 포털에서 PIN 을 바꿀 수 없습니다")

    def check_new(self, c, uid, name, secret, local):
        """가입 값 검사(틀리면 ValueError)"""
        raise Unsupported(self.guide or "이 계정 저장소에서는 포털에서 가입할 수 없습니다")

    def check_secret_rule(self, secret):
        """새 비밀번호 규칙(틀리면 ValueError)"""
        raise Unsupported(self.guide or "이 계정 저장소에서는 포털에서 PIN 을 바꿀 수 없습니다")

    def info(self):
        """화면·관리 API 에 내보내는 설정(비밀값·주소 없음)"""
        return {"name": self.name, "label": self.label, "signup": self.supports_signup, "password_change": self.supports_password_change,
                "guide": "" if (self.supports_signup and self.supports_password_change) else self.guide}
