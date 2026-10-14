"""요청 하나의 상태(Ctx)와 응답(Raw)·본문 읽기 — 모든 처리 함수가 받는 ctx. portal.py 의 핸들러 속성(uid·uname·_setcookie)과 같은 뜻.
누가 요청했는지(auth)는 sessions.resolve 가 정한다."""
import json, urllib.parse

from fastapi import Request
from starlette.responses import Response

from . import accounts, config, pages, sessions


class Raw(Response):
    """헤더를 정해진 순서·중복(Set-Cookie 여러 개) 그대로 내보내는 응답. Content-Length 는 본문에서 계산"""

    def __init__(self, body=b"", status=200, headers=(), length=True):
        self.status_code, self.body, self.background = status, body, None
        self.raw_headers = [(k.encode("latin-1") if isinstance(k, str) else k, v.encode("latin-1") if isinstance(v, str) else v) for k, v in headers]
        if length:
            self.raw_headers.append((b"content-length", str(len(body)).encode()))


def hval(v):   # 헤더 값: 한글 등은 latin-1 로 못 담으므로 UTF-8 바이트 그대로(portal.py 의 send_header 와 같음)
    return v.encode("utf-8") if isinstance(v, str) else v


class Ctx:
    def __init__(self, request: Request):
        self.request = request
        self.method = request.method
        qs = request.scope.get("query_string", b"")
        self.raw = request.scope.get("raw_path", request.url.path.encode()).decode("latin-1") + ("?" + qs.decode("latin-1") if qs else "")
        self.p = self.raw.split("?")[0]
        self.headers = request.headers
        self.cookie = "; ".join(request.headers.getlist("cookie"))   # Cookie 줄이 여럿(대소문자 다른 줄 포함)이면 모두 합쳐서 봄
        self.client = sessions.client_ip(request.client.host if request.client else "", request.headers.getlist("x-forwarded-for"))
        self.uid = self.uname = self.setcookie = None

    def send(self, body, ctype="application/json", code=200, headers=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        hd = [("X-Author", hval(pages._SIG_A)), ("Content-Type", ctype), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")]
        if ctype.startswith("text/html"):   # 고전 포털 화면(/ · /login · /account · /guide): 끼워 넣기 제한
            hd += [("Content-Security-Policy", pages.frame_csp())] + ([] if config.CORS_ORIGINS else [("X-Frame-Options", "SAMEORIGIN")])
        given = {k.lower() for k, _ in headers or ()}
        hd = [(k, v) for k, v in hd if k.lower() not in given] + [(k, hval(v)) for k, v in headers or ()]
        if self.setcookie:
            hd.append(("Set-Cookie", self.setcookie))
        return Raw(b, code, hd)

    @staticmethod
    def bare(code, headers=()):
        return Raw(b"", code, list(headers))

    # 계정
    def auth(self):
        """쿠키·로컬 주인 토큰 → uid(없으면 None). 규칙은 sessions.resolve"""
        self.uid, self.uname, self.setcookie = sessions.resolve(self.cookie, self.client, self.headers.get("x-local-owner"))
        return self.uid

    def local_owner(self):
        """쿠키와 무관하게 '진짜 로컬(토큰)'인지 — 관리자 아이디 가입 허용 판정용"""
        return sessions.local_owner(self.client, self.headers.get("x-local-owner"))

    @property
    def is_owner(self):
        return accounts.is_admin(self.uid)

    @property
    def agent_user(self):
        return "owner" if self.is_owner else self.uid

    def theme(self):
        return pages.user_theme(self.uid, self.cookie)

    def html_page(self, html, kind, d="", v2=""):
        return pages.html_page(html, kind, self.theme(), d, v2)

    def help_html(self, d):
        return pages.help_html(d, self.agent_user)

    def need_login(self):
        if self.method == "GET" and "text/html" in (self.headers.get("accept") or ""):
            return self.bare(302, [("Location", "/login?next=" + urllib.parse.quote(self.raw, safe=""))])
        return self.send({"error": "로그인이 필요합니다", "login": "/login"}, code=401)

    def redirect(self, loc):
        hd = [("Location", urllib.parse.quote(loc, safe="/?&=#%:;@!$'()*+,~-._"))]
        if self.setcookie:
            hd.append(("Set-Cookie", self.setcookie))
        return self.bare(302, hd)


# ── 본문 읽기 ───────────────────────────────────────────────────────────
class TooBig(Exception):
    pass


async def drain(request, n):
    if n <= config.DRAIN_MAX:   # 보안 설계서: 상한 초과는 본문을 끝까지 읽어 버린 뒤 413(브라우저가 '연결 끊김' 대신 413 을 받게)
        try:
            async for _ in request.stream():
                pass
        except Exception:
            pass


def length(ctx):
    v = ctx.headers.get("content-length")
    if v is None:
        return None
    if not v.isdigit():
        raise ValueError
    return int(v)


async def read_body(ctx, cap):
    """Content-Length 또는 청크 본문을 cap 까지 읽음. 넘으면 끝까지 버리고 TooBig"""
    n = length(ctx)
    if n is not None and n > cap:
        await drain(ctx.request, n)
        raise TooBig
    buf = bytearray()
    async for chunk in ctx.request.stream():
        buf += chunk
        if len(buf) > cap:
            await drain(ctx.request, 0)
            raise TooBig
    return bytes(buf)


def too_big(ctx, cap):
    return ctx.send({"error": f"요청 본문이 너무 큽니다 (최대 {cap // (1024 * 1024)}MB)"}, code=413)


def parse_json(b):
    req = json.loads(b or b"{}")
    if not isinstance(req, dict):
        raise ValueError
    return req


def not_implemented(method):
    """portal.py(BaseHTTPRequestHandler)는 GET·POST 말고는 501 — 같은 응답(HEAD 는 uvicorn 이 본문을 뺌)"""
    from http.server import BaseHTTPRequestHandler as B
    from http import HTTPStatus
    body = (B.error_message_format % {"code": 501, "message": f"Unsupported method ('{method}')", "explain": HTTPStatus(501).description}).encode("utf-8", "replace")
    return Raw(body, 501, [("Connection", "close"), ("Content-Type", B.error_content_type)])
