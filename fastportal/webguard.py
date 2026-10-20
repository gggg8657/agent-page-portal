"""React 빌드 자산(/assets/* · /app/assets/*) 내보내기 권한 — 빌드의 .vite/manifest.json(agent-page-web vite.config.ts build.manifest)으로 판단.
  공개: 로그인 화면(index.html 진입 번들)이 정적으로 불러오는 JS·CSS·그림만 — 로그인 전에도 화면이 떠야 하므로
  로그인: 그 밖의 번들(셸·화면·도구) — 로그인 안 했으면 401
  권한: 관리·승인·개발자 화면 번들(ROLE_SRC 의 소스에서만 불리는 조각) — 권한이 없으면 404(있는지도 알리지 않음)
판단은 '확실히 관리·개발자 것'만 닫고(fail closed), manifest 에 없는 파일(배포 때 옮겨 온 옛 번들 등)은 로그인한 계정에 연다(fail open).
옛 빌드의 manifest(.vite/manifest.<시각>.json, deploy.sh 가 옮김)도 읽어 그 빌드의 권한 번들을 계속 가림. 공개 목록은 지금 빌드 것만.
manifest 가 없는 옛 빌드는 index.html 이 직접 부르는 파일만 공개(그 밖은 로그인).
배포(링크 바꿔 끼우기)는 dist 실제 경로·manifest 수정 시각이 바뀌므로 다음 요청에서 다시 읽음(포털 재시작 없음)."""
import glob, json, os, re, threading

# 소스 → 그 화면 번들을 받을 수 있는 역할. 화면 파일 이름이 바뀌면 여기도 같이(agent-page-web src/main.tsx·Create.tsx 의 lazy)
ROLE_SRC = {
    "src/pages/Admin.tsx": {"admin"},
    "src/pages/Approvals.tsx": {"admin", "head"},
    "src/pages/DevTools.tsx": {"admin", "developer"},
}

_LOCK = threading.Lock()
_CACHE = {}   # dist 실제 경로 → (서명, Policy)


class Policy:
    def __init__(self, public, roles, has_manifest):
        self.public = public            # 공개 파일(dist 기준 상대 경로, 예: assets/index-abc.js)
        self.roles = roles              # 파일 → 받을 수 있는 역할 집합
        self.has_manifest = has_manifest

    def need(self, rel):
        """'public' | 'login' | {역할…}"""
        if rel in self.public:
            return "public"
        r = self.roles.get(rel)
        return set(r) if r else "login"


def _files(man, key):
    e = man.get(key) or {}
    out = set()
    if e.get("file"):
        out.add(e["file"])
    out.update(e.get("css") or ())
    out.update(e.get("assets") or ())
    return out


def _closure(man, keys):
    """정적 import 를 따라간 manifest 키 집합(동적 import 는 안 따라감 — 그건 따로 불리는 화면)"""
    seen, todo = set(), list(keys)
    while todo:
        k = todo.pop()
        if k in seen or k not in man:
            continue
        seen.add(k)
        todo.extend(man[k].get("imports") or ())
    return seen


def analyze(man):
    """manifest 하나 → (공개 파일, 역할 파일 사전). 한 파일을 '역할 없는' 진입점이 하나라도 부르면 그 파일은 로그인만으로 열림"""
    entries = [k for k, v in man.items() if v.get("isEntry")]
    dyn = [k for k, v in man.items() if v.get("isDynamicEntry")]
    public = set()
    for k in _closure(man, entries):
        public |= _files(man, k)
    loaders = {}   # 파일 → [역할 집합 또는 None(제한 없음)]
    for d in dyn:
        role = None
        src = (man[d].get("src") or d).replace("\\", "/")
        for pat, r in ROLE_SRC.items():
            if src == pat or src.endswith("/" + pat):
                role = r
        for k in _closure(man, [d]):
            for f in _files(man, k):
                loaders.setdefault(f, []).append(role)
    roles = {}
    for f, rs in loaders.items():
        if f in public or any(r is None for r in rs):
            continue
        roles[f] = set().union(*rs)
    return public, roles


INDEX_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)


def _from_index(dist, prefix):
    """manifest 없는 옛 빌드: index.html 이 직접 부르는 파일만 공개"""
    try:
        html = open(os.path.join(dist, "index.html"), encoding="utf-8").read()
    except OSError:
        return set()
    out = set()
    for u in INDEX_REF.findall(html):
        u = u.split("?")[0].split("#")[0]
        if u.startswith(prefix + "/"):
            out.add(u[len(prefix) + 1:])
    return out


def policy(dist, prefix=""):
    """dist(실제 경로)의 판단표 — 배포로 바뀌면 다시 읽음"""
    vite = os.path.join(dist, ".vite")
    mans = sorted(glob.glob(os.path.join(vite, "manifest*.json")))
    try:
        sig = tuple((m, os.path.getmtime(m)) for m in mans) + ((os.path.getmtime(os.path.join(dist, "index.html")),) if os.path.exists(os.path.join(dist, "index.html")) else ())
    except OSError:
        sig = None
    with _LOCK:
        hit = _CACHE.get(dist)
        if hit and hit[0] == sig:
            return hit[1]
    cur = os.path.join(vite, "manifest.json")
    public, roles = set(), {}
    has = os.path.isfile(cur)
    for m in mans:
        try:
            man = json.load(open(m, encoding="utf-8"))
        except (OSError, ValueError) as e:
            print("webguard: manifest 읽기 실패", os.path.basename(m), type(e).__name__, flush=True)
            if m == cur:
                has = False
            continue
        pub, rl = analyze(man)
        for f, r in rl.items():
            roles.setdefault(f, set()).update(r)
        if m == cur:
            public = pub
    if not has:
        public = _from_index(dist, prefix)
    for f in public:   # 지금 빌드의 공개 파일은 공개(옛 manifest 의 권한 표시보다 우선 — 같은 이름이면 같은 내용)
        roles.pop(f, None)
    p = Policy(public, roles, has)
    with _LOCK:
        _CACHE[dist] = (sig, p)
    return p


def user_roles(ctx):
    """지금 계정의 역할 집합(admin·head·developer) — 권한 번들 요청 때만 부름(DB 한두 번)"""
    from . import accounts, devsubmit
    from .db import db
    r = set()
    if ctx.is_owner:
        r.add("admin")
    if ctx.uid and ctx.uid != "owner":
        with db() as c:
            if accounts.my_heads(c, ctx.uid):
                r.add("head")
            try:
                if devsubmit.is_developer(c, ctx.uid):
                    r.add("developer")
            except Exception:   # developers 표가 아직 없음
                pass
    return r
