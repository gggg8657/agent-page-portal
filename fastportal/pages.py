"""화면: HTML 틀·테마 끼워 넣기·저작자 표기·랜딩·사용법(?)·정적 파일(/guide · /theme · /landing) · React 빌드(WEB_DIST·WEB_ROOT).
응답은 넘겨받은 ctx(app.Ctx: send·bare·redirect·auth·html_page)로만 만든다 — 이 모듈은 ctx 를 import 하지 않음(순환 없음)."""
import base64, os, re, time, urllib.parse

from . import accounts, config, sessions, tools
from .config import THEME_DIR, V2_DIR, WEB_DIST, WEB_PREFIX, WEB_ROOT, WEB_ROOT_DIST
from .db import db


def _read(*p):
    return open(os.path.join(config.ROOT, *p), encoding="utf-8").read()


HTML, HELP, LOGIN, ACCOUNT, BOOT = _read("portal.html"), _read("help.html"), _read("login.html"), _read("account.html"), _read("theme", "boot.js")

# ── 저작권 표기 ──────────────────────────────────────────────────────────
_SIG = base64.b64decode("wqkgMjAyNiBnZ2dnODY1NyDCtyBkb25nanVraW0uZGV2QGdtYWlsLmNvbQ==").decode()
_SIG_A = base64.b64decode("Z2dnZzg2NTcgPGRvbmdqdWtpbS5kZXZAZ21haWwuY29tPg==").decode()


def signed(html):
    name, mail = _SIG.split(" · ")
    if 'name="author"' not in html:
        meta = f'<meta name="author" content="{name[7:]} <{mail}>">'
        if "<head>" in html:
            html = html.replace("<head>", "<head>" + meta, 1)
        else:
            m = re.match(r"\s*<!doctype[^>]*>", html, re.I)
            html = html[:m.end()] + meta + html[m.end():] if m else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name} · 내부 시험용 · 배포용 아님</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


# ── 화면 테마 ───────────────────────────────────────────────────────────
THEME_RE = re.compile(r"(?:^|;\s*)agtheme=([a-z0-9-]+)")


def user_theme(uid, cookie_header):
    t = None
    if uid and uid != "owner":
        with db() as c:
            r = c.execute("SELECT theme FROM users WHERE id=?", (uid,)).fetchone()
            t = r and r[0]
    if not t:
        m = THEME_RE.search(cookie_header or "")
        t = m and m[1]
    return t if t in config.THEMES else "classic-light"


def theme_files(pref, kind, d=""):
    if pref == "classic-light":
        return []
    f, light = ["tokens.css"], pref.endswith("-light")
    if kind == "tool":
        f += ({"trendy": ["base-trendy.css"], "v2": ["base-v2.css"]}.get(pref.split("-")[0], [])) + ([] if light else ["base-dark.css", "dark.js"]) + ["chrome.css"]
        f += [f"tools/{d}.css"] if os.path.isfile(os.path.join(THEME_DIR, "tools", f"{d}.css")) else []
    else:
        f += ["guide.css"] if kind == "guide" else ["portal-v2.css" if pref.startswith("v2") else "portal.css", "chrome.css"]
    return f


def theme_ver():
    return str(int(max(os.path.getmtime(os.path.join(r, x)) for r, _, fs in os.walk(THEME_DIR) for x in fs)))


def theme_head(pref, kind, d=""):
    v = theme_ver()
    tags = "".join(f'<script src="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}"></script>' if f.endswith(".js") else
                   f'<link rel="stylesheet" href="/theme/{f}?v={v}" id="ag-th-{re.sub(r"[^A-Za-z0-9_]", "-", f)}">' for f in theme_files(pref, kind, d))
    js = BOOT.replace("%KIND%", kind).replace("%DIR%", d).replace("%V%", v).replace("%PREF%", pref).replace("%TOOLCSS%", "true" if f"tools/{d}.css" in theme_files("trendy-dark", kind, d) else "false")
    if kind == "portal":
        tags += f'<script src="/theme/picker.js?v={v}" defer></script>'
        if pref.startswith("v2"):
            tags += f'<script src="/theme/palette-v2.js?v={v}" defer></script>'
    return f"<script>{js}</script>{tags}"


def v2_page(html, name):
    f = os.path.join(V2_DIR, f"{name}.html")
    a, b = html.find("<style>"), html.find("<script>")
    if not os.path.isfile(f) or a < 0 or b < a:
        return html
    return html[:a] + open(f, encoding="utf-8").read() + html[b:]


def first_html(rest):
    path, _, q = rest.partition("?")
    return path in ("", "index.html") and "1" not in urllib.parse.parse_qs(q).get("pet", [])


def themed(html, head):
    m = re.search(r"<meta\s+charset[^>]*>", html[:2048], re.I) or re.search(r"<head(\s[^>]*)?>", html[:4096], re.I) or re.search(r"<!doctype[^>]*>", html[:512], re.I)
    i = m.end() if m else 0
    return html[:i] + head + html[i:]


def html_page(html, kind, theme, d="", v2=""):
    if v2 and theme.startswith("v2"):
        html = v2_page(html, v2)
    return signed(themed(html, theme_head(theme, kind, d))).encode()


def help_html(d, agent_user):
    return HELP.replace("%DIR%", d).replace("%USER%", agent_user)


# ── 랜딩(로그인 화면) ────────────────────────────────────────────────────
def landing(html, who=""):
    gs = tools.landing_groups()
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    cards = "".join(f'<li class="lp-g"><div class="lp-gh"><b>{esc(k)}</b><span>{len(v)}</span></div><p>{esc(" · ".join(v[:5]))}{" …" if len(v) > 5 else ""}</p></li>' for k, v in gs)
    name, mail = _SIG.split(" · ")
    sig = f'<span data-sig title="{mail}">{name} · 내부 시험용 · 배포용 아님</span>'
    return (html.replace("%N_TOOLS%", str(sum(len(v) for _, v in gs))).replace("%N_GROUPS%", str(len(gs)))
            .replace("%GROUPS%", cards).replace("%SIG%", sig)
            .replace("%FOOT%", f'지금 <b>{esc(who)}</b> 로 접속 중 · <a href="/">포털로</a> · <a href="/account">계정 설정·PIN 바꾸기</a> · <a href="/logout">로그아웃</a>' if who else ""))


def login_page(ctx):
    """GET /login — 이미 로그인(계정)이면 next 로, 아니면 랜딩(로컬 주인이면 '접속 중' 표시)"""
    if ctx.auth() and ctx.uid != "owner":
        return ctx.redirect(sessions.safe_next(urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query).get("next", ["/"])[0]))
    who = ctx.uname if ctx.uid == "owner" else ""
    ctx.setcookie, ctx.uid = None, None
    return ctx.send(ctx.html_page(landing(LOGIN, who), "portal"), "text/html; charset=utf-8")


def home(ctx):
    tour = f'<script src="/theme/tour.js?v={theme_ver()}"></script>'
    return ctx.send(ctx.html_page(HTML + tour + ctx.help_html("portal"), "portal", v2="home"), "text/html; charset=utf-8")


def account_page(ctx, p):
    """/account · /approvals(부서장·관리자) · /admin(관리자) — 같은 화면(account.html), 권한 없으면 /account 로"""
    with db() as c:
        ok = p == "/account" or ctx.is_owner or (p == "/approvals" and accounts.my_heads(c, ctx.uid))
    if not ok:
        return ctx.bare(302, [("Location", "/account")])
    return ctx.send(ctx.html_page(ACCOUNT + ctx.help_html("portal"), "portal", v2="account"), "text/html; charset=utf-8")


# ── 정적 파일 (/guide · /theme · /landing/home.png) ─────────────────────────
def guide_file(ctx, p):
    if p == "/guide":
        return ctx.bare(302, [("Location", "/guide/")])
    rel = urllib.parse.unquote(p[len("/guide/"):]) or "index.html"
    m = re.fullmatch(r"index\.html|agent-portal\.pdf|img/[A-Za-z0-9][\w.-]*\.(png|jpe?g|svg|webp)|fonts/[A-Za-z0-9][\w.-]*\.woff2|fonts/OFL\.txt", rel)
    top = config.GUIDE_DIR
    e = re.fullmatch(r"ext/([a-z0-9-]+-local)/([A-Za-z0-9][\w.-]*\.(png|jpe?g|webp))", rel)
    if e:
        m, top, rel = e, os.path.join(config.BASE, e[1], "static", "guide"), e[2]
    f = os.path.join(top, rel)
    if not m or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
        return ctx.send({"error": "not found"}, code=404)
    st = os.stat(f); lm = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(st.st_mtime)); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    cc = ("Cache-Control", "no-cache" if rel == "index.html" else "private, max-age=3600")
    if ctx.headers.get("if-none-match") == etag:
        return ctx.bare(304, [("ETag", etag), cc])
    ext = rel.rsplit(".", 1)[1]
    ctype = {"html": "text/html; charset=utf-8", "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "svg": "image/svg+xml", "webp": "image/webp", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[ext]
    hd = [cc, ("ETag", etag), ("Last-Modified", lm), ("X-Content-Type-Options", "nosniff")]
    if ext == "pdf":
        hd.append(("Content-Disposition", "attachment; filename=agent-portal.pdf; filename*=UTF-8''" + urllib.parse.quote("에이전트-포털-사용-설명서.pdf")))
    body = open(f, "rb").read()
    return ctx.send(ctx.html_page(body.decode("utf-8"), "guide") if ext == "html" else body, ctype, headers=hd)


def theme_file(ctx, p):
    if p == config.LANDING_IMG[0]:
        f = config.LANDING_IMG[1]
        st = os.stat(f); etag = f'"{int(st.st_mtime)}-{st.st_size}"'; cc = ("Cache-Control", "public, max-age=86400")
        if ctx.headers.get("if-none-match") == etag:
            return ctx.bare(304, [("ETag", etag), cc])
        return ctx.send(open(f, "rb").read(), "image/png", headers=[cc, ("ETag", etag), ("X-Content-Type-Options", "nosniff")])
    rel = urllib.parse.unquote(p[len("/theme/"):])
    m = re.fullmatch(r"[a-z][a-z0-9-]*\.(css|js)|tools/[a-z0-9][a-z0-9_-]*\.(css)|fonts/([A-Za-z0-9][\w.-]*\.(woff2|txt))", rel)
    top = os.path.join(config.GUIDE_DIR, "fonts") if m and m[3] else THEME_DIR
    f = os.path.join(top, m[3] if m and m[3] else rel)
    if not m or rel == "boot.js" or not os.path.isfile(f) or not os.path.realpath(f).startswith(os.path.realpath(top) + os.sep):
        return ctx.send({"error": "not found"}, code=404)
    st = os.stat(f); etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    cc = ("Cache-Control", "public, max-age=86400")
    if ctx.headers.get("if-none-match") == etag:
        return ctx.bare(304, [("ETag", etag), cc])
    ctype = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8", "woff2": "font/woff2", "txt": "text/plain; charset=utf-8"}[f.rsplit(".", 1)[1]]
    return ctx.send(open(f, "rb").read(), ctype, headers=[cc, ("ETag", etag), ("X-Content-Type-Options", "nosniff")])


# ── React 화면(agent-page-web 빌드) 같은 출처로 내보내기 ─────────────────────────
# WEB_DIST(빌드 폴더)가 있으면 WEB_PREFIX(기본 /app) 아래에서 dist 를 내보냄. 파일이 없는 경로는 index.html(SPA 폴백),
# 없는 자산(assets/·확장자 있는 파일)은 404. assets/ 는 해시 붙은 이름이라 1년 캐시, 그 밖(index.html 등)은 no-cache.
# 로그인 화면(/app/login)과 자산은 로그인 없이 내보내고, 그 밖의 화면 주소는 로그인 전이면 /app/login?next= 로 302. 규칙은 agent-page-web/server.py static() 과 같음
# '/' 전환(WEB_ROOT=1): 포털 화면 주소(/ · /login · /all · /fav · /recent · /account · /approvals · /admin · /tool/*)와
# 빌드 자산(/assets/* · /theme-boot.js · /fonts.css)을 WEB_ROOT_DIST(agent-page-web `npm run build:root`·deploy.sh --root)에서 내보낸다.
# /api · /t · /theme · /guide · /logout · /landing 과 /app(WEB_DIST)은 그대로.
WEB_SPA = re.compile(r"/($|[?#]|(all|fav|recent|account|approvals|admin)([/?#]|$)|tool/)")   # React 라우터 경로(agent-page-web Landing.tsx SPA 와 같음)
WEB_ROOT_PATH = re.compile(r"/($|login|(all|fav|recent|account|approvals|admin)/?|tool/.+|assets/.+|theme-boot\.js|fonts\.css)")
WEB_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; "
           "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'self'; form-action 'self'")
WEB_TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
             ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
             ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".webmanifest": "application/manifest+json", ".wasm": "application/wasm"}


def web_path(p):
    return (bool(WEB_DIST) and (p == WEB_PREFIX or p.startswith(WEB_PREFIX + "/") or p == "/web/landing.json")) or \
        (WEB_ROOT and (p == "/web/landing.json" or bool(WEB_ROOT_PATH.fullmatch(p))))


def web_landing(ctx):
    """로그인 전 랜딩의 분류·도구 수(포털 /login 화면과 같은 값, 숨긴 도구 빼고) — React Landing 이 읽음(server.py /web/landing.json 과 같은 모양)"""
    gs = [{"name": k, "n": len(v), "names": " · ".join(v[:5]) + (" …" if len(v) > 5 else "")} for k, v in tools.landing_groups()]
    return ctx.send({"groups": gs, "tools": sum(g["n"] for g in gs)}, "application/json; charset=utf-8", headers=[("Cache-Control", "no-cache")])


def web_file(ctx, p):
    if p == "/web/landing.json" or p == WEB_PREFIX + "/web/landing.json":
        return web_landing(ctx)
    prefix, dist_env = (WEB_PREFIX, WEB_DIST) if WEB_DIST and (p == WEB_PREFIX or p.startswith(WEB_PREFIX + "/")) else ("", WEB_ROOT_DIST)   # /app 아래 또는 '/' 전환(WEB_ROOT)
    if prefix and p == prefix:
        return ctx.bare(302, [("Location", prefix + "/" + ("?" + ctx.raw.split("?", 1)[1] if "?" in ctx.raw else ""))])
    rel = urllib.parse.unquote(p[len(prefix) + 1:])
    if rel == "login" and sessions.SID_RE.search(ctx.cookie) and ctx.auth() and ctx.uid != "owner":   # 이미 로그인 → 랜딩 대신 바로(포털 /login·server.py 와 같음)
        nxt = sessions.safe_next(urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query).get("next", ["/"])[0])
        m = re.fullmatch(r"/t/([a-z0-9-]+)/?", nxt)
        if m:
            nxt = f"/tool/{m[1]}"
        if WEB_SPA.match(nxt):   # React 의 next 는 라우터 기준(/all, /tool/x) — 화면 주소로
            nxt = prefix + nxt
        return ctx.redirect(nxt)
    dist = os.path.realpath(dist_env)
    f = os.path.realpath(os.path.join(dist, rel)) if rel and "\x00" not in rel else ""
    if not (f and f.startswith(dist + os.sep) and os.path.isfile(f)):
        if rel.startswith("assets/") or re.search(r"\.[A-Za-z0-9]{1,12}$", rel.rsplit("/", 1)[-1]):
            return ctx.send({"error": "not found"}, code=404)   # 없는 자산은 index.html 이 아니라 404(옛 해시 파일 요청 등)
        f = os.path.join(dist, "index.html")               # SPA 라우팅(/app/login, /app/tool/…, /app/account 등)
        if not os.path.isfile(f):
            return ctx.send({"error": ("WEB_DIST" if prefix else "WEB_ROOT_DIST") + " 에 index.html 이 없습니다"}, code=503)
        if rel != "login" and not ctx.auth():   # 로그인 전 화면 주소 → 로그인으로(포털 / 처럼 302 — 화면이 401 을 받고 옮기는 것보다 빠르고 콘솔 오류 없음)
            q = ctx.raw.split("?", 1)[1] if "?" in ctx.raw else ""
            return ctx.bare(302, [("Location", prefix + "/login?next=" + urllib.parse.quote("/" + rel + ("?" + q if q else ""), safe=""))])
    ext = os.path.splitext(f)[1].lower()
    st = os.stat(f)
    etag = f'"{int(st.st_mtime)}-{st.st_size}"'
    immutable = f.startswith(os.path.join(dist, "assets") + os.sep)
    hd = [("ETag", etag), ("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-cache"), ("X-Content-Type-Options", "nosniff"),
          ("Referrer-Policy", "same-origin")]
    if ext == ".html":
        hd += [("Content-Security-Policy", WEB_CSP), ("X-Frame-Options", "SAMEORIGIN")]
    if ctx.headers.get("if-none-match") == etag:
        return ctx.bare(304, hd)
    with open(f, "rb") as fh:
        body = fh.read()
    ctx.setcookie = None   # 정적 파일 응답엔 세션 연장 쿠키를 싣지 않음(캐시되는 응답)
    return ctx.send(body, WEB_TYPES.get(ext, "application/octet-stream"), headers=hd)
