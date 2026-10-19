"""폐쇄망 pip 패키지 목록(pkg_index) — '이 패키지를 폐쇄망 내부 저장소에서 설치할 수 있나'를 보는 공통 모듈.

  표: pkg_index(name PK = PEP 503 정규화 이름, versions = JSON 배열 또는 NULL(버전 모름), source, updated)
  채우기: 기동할 때 표가 비어 있고 _data/pkg-index/*.txt 가 있으면 가장 최근 파일을 읽어 넣음(seed). 그 뒤는 관리자 API 로 통째로 바꿈.
  목록 파일 형식(너그럽게): 이름만 / 이름==버전 / '이름 버전,버전' / pip list 표 / 'pip index versions' 출력('이름 (1.2)' + 'Available versions: …') /
          주석(#)·빈 줄·옵션(-i …)은 무시.
  검사(다른 모듈에서도 부름 — devsubmit 형식 검사, 이후 '아이디어로 에이전트 만들기'):
    parse_requirements(text) · requirements_from_pyproject(text) · requirements_from_setup_sh(text) → [Req]
    check_requirements(c, reqs, where) → (warnings, info) · scan_tree(c, root, names) → (warnings, info)
  경고만 낸다(오류 아님). 표가 비어 있으면 검사를 건너뛰고 안내만.
  내부 저장소 주소는 포털 설정 PORTAL_PIP_INDEX_URL(기본 빈 값) — 있을 때만 화면에 'pip install --index-url …' 안내."""
import glob, json, os, re, shlex

from . import config
from .db import now

SEED_DIR = os.path.join(config.DATA, "pkg-index")
SEARCH_MAX = 50
NAME_RE = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def norm(name):
    """PEP 503 정규화: 소문자, [-_.] 연속은 '-' 하나"""
    return re.sub(r"[-_.]+", "-", str(name or "").strip()).lower()


def init(c):
    c.execute("CREATE TABLE IF NOT EXISTS pkg_index(name TEXT PRIMARY KEY, versions TEXT, source TEXT, updated TEXT)")
    if c.execute("SELECT 1 FROM pkg_index LIMIT 1").fetchone():
        return
    files = sorted(glob.glob(os.path.join(SEED_DIR, "*.txt")))
    if not files:
        return
    try:
        with open(files[-1], encoding="utf-8", errors="replace") as f:
            entries = parse_list(f.read())
    except OSError:
        return
    if entries:
        replace(c, entries, os.path.basename(files[-1]))
        print(f"pkg_index: {os.path.basename(files[-1])} 에서 {len(entries)}개 읽음", flush=True)


# ── 목록 파일 읽기 ──────────────────────────────────────────────────────
def _vers(s):
    return [v for v in (x.strip().strip("()") for x in re.split(r"[,\s]+", s or "")) if v and re.match(r"^[0-9A-Za-z][0-9A-Za-z.+!_-]*$", v)]


def parse_list(text):
    """목록 파일 → {정규화 이름: set(버전) | None}"""
    out, last = {}, None

    def add(name, vs=()):
        n = norm(name)
        if not n:
            return None
        if vs:
            out[n] = (out.get(n) or set()) | set(vs)
        else:
            out.setdefault(n, None)
        return n

    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or re.fullmatch(r"[-=\s]+", line):
            continue
        m = re.match(r"^available versions\s*:\s*(.*)$", line, re.I)
        if m:
            if last:
                add(last, _vers(m.group(1)))
            continue
        if re.match(r"^(package|name)\s+version\b", line, re.I) or re.match(r"^(installed|latest)\s*:", line, re.I):
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*\(([^)]*)\)\s*$", line)   # pip index versions: 'name (1.2.3)'
        if m:
            last = add(m.group(1), _vers(m.group(2)))
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*(.+)$", line)   # name==1.0
        if m:
            last = add(m.group(1), _vers(m.group(2)))
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*@", line)   # name @ url
        if m:
            last = add(m.group(1)); continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(?:[<>!~]=?.*)?$", line)   # 이름만 / 이름>=1 → 버전 모름
        if m:
            last = add(m.group(1)); continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)[\s:]+(.+)$", line)   # 'name 1.0,1.1' · pip list 'name   1.0'
        if m:
            last = add(m.group(1), _vers(m.group(2)))
    return out


def replace(c, entries, source):
    ts = now()
    c.execute("DELETE FROM pkg_index")
    c.executemany("INSERT INTO pkg_index(name, versions, source, updated) VALUES(?,?,?,?)",
                  [(n, json.dumps(sorted(v)) if v else None, source, ts) for n, v in sorted(entries.items())])


def meta(c):
    r = c.execute("SELECT COUNT(*), MAX(source), MAX(updated) FROM pkg_index").fetchone()
    return {"count": r[0], "source": r[1] or "", "updated": r[2] or "", "index_url": config.PIP_INDEX_URL or ""}


def _row(r):
    return {"name": r[0], "versions": json.loads(r[1]) if r[1] else None}


def search(c, q, limit=SEARCH_MAX):
    """이름 접두 일치 먼저, 그다음 부분 일치(정규화 이름 기준)"""
    n = norm(q)
    if not n:
        return []
    esc = n.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pre = c.execute("SELECT name, versions FROM pkg_index WHERE name LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?", (esc + "%", limit)).fetchall()
    rest = c.execute("SELECT name, versions FROM pkg_index WHERE name LIKE ? ESCAPE '\\' AND name NOT LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?",
                     ("%" + esc + "%", esc + "%", max(0, limit - len(pre)))).fetchall()
    return [_row(r) for r in pre + rest]


def lookup(c, names):
    """{원래 이름: {name(정규화), found, versions}}"""
    out = {}
    for x in names:
        n = norm(x)
        if not n:
            continue
        r = c.execute("SELECT name, versions FROM pkg_index WHERE name=?", (n,)).fetchone()
        out[x] = {"name": n, "found": bool(r), "versions": (json.loads(r[1]) if r and r[1] else None)}
    return out


# ── 요구 패키지 읽기 ─────────────────────────────────────────────────────
class Req(dict):
    """{raw, name, versions(== 로 고정한 버전들), kind: pkg|git|url|path}"""


def _req(spec):
    s = spec.strip().strip("'\"")
    if not s:
        return None
    if s.startswith(("git+", "hg+", "svn+", "bzr+")) or re.search(r"@\s*git\+", s):
        return Req(raw=s, name="", versions=[], kind="git")
    if re.match(r"^[a-z]+://", s, re.I) or re.search(r"@\s*(https?|file)://", s, re.I):
        return Req(raw=s, name="", versions=[], kind="url")
    if s.startswith((".", "/", "~")) or s.endswith((".whl", ".tar.gz", ".zip")) or "/" in s.split(";")[0]:
        return Req(raw=s, name="", versions=[], kind="path")
    s = s.split(";", 1)[0].strip()   # 환경 표시(; python_version…) 버림
    m = NAME_RE.match(s)
    if not m:
        return None
    rest = s[m.end():]
    rest = re.sub(r"^\[[^\]]*\]", "", rest)
    return Req(raw=spec.strip(), name=m.group(1), versions=re.findall(r"===?\s*([0-9A-Za-z][0-9A-Za-z.+!_*-]*)", rest), kind="pkg")


def parse_requirements(text):
    out = []
    for raw in (text or "").replace("\\\n", " ").splitlines():
        line = re.split(r"\s+#", " " + raw, 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-e ", "--editable")):
            r = _req(line.split(None, 1)[1] if " " in line else "")
            if r:
                out.append(r)
            continue
        if line.startswith("-"):
            continue   # -r·-c·--index-url 등 옵션
        r = _req(line)
        if r:
            out.append(r)
    return out


def requirements_from_pyproject(text):
    try:
        import tomllib
        d = tomllib.loads(text or "")
    except Exception:
        return []
    deps = (d.get("project") or {}).get("dependencies") or []
    return [r for r in (_req(x) for x in deps if isinstance(x, str)) if r]


_OPT_ARG = {"-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--target", "-t",
            "--prefix", "--root", "--python", "--trusted-host", "--platform", "--python-version", "--only-binary", "--no-binary", "--cache-dir", "--src"}


def requirements_from_setup_sh(text):
    """setup.sh 의 pip install 줄 → ([Req], [요구 파일 경로(-r)])"""
    reqs, files = [], []
    for line in (text or "").replace("\\\n", " ").splitlines():
        line = line.split("#", 1)[0] if not line.strip().startswith("#") else ""
        for part in re.split(r"&&|\|\||;", line):
            m = re.search(r"(?:^|\s)(?:\S*/)?(?:pip3?|pip3\.\d+)\s+install\s+(.*)$|(?:python3?|\S*/python3?)\s+-m\s+pip\s+install\s+(.*)$|uv\s+pip\s+install\s+(.*)$", part)
            if not m:
                continue
            args = m.group(1) or m.group(2) or m.group(3) or ""
            try:
                toks = shlex.split(args)
            except ValueError:
                toks = args.split()
            i = 0
            while i < len(toks):
                t = toks[i]
                if t in ("-r", "--requirement") and i + 1 < len(toks):
                    files.append(toks[i + 1]); i += 2; continue
                if t in ("-e", "--editable") and i + 1 < len(toks):
                    r = _req(toks[i + 1]); reqs += [r] if r else []; i += 2; continue
                if t in _OPT_ARG:
                    i += 2; continue
                if t.startswith("-") or "$" in t:
                    i += 1; continue
                r = _req(t)
                if r:
                    reqs.append(r)
                i += 1
    return reqs, files


# ── 검사 ────────────────────────────────────────────────────────────────
def check_requirements(c, reqs, where=""):
    """[(Req, 파일)] 또는 [Req] → (warnings, info). 표가 비어 있으면 건너뜀(안내만)"""
    warn, info = [], []
    m = meta(c)
    if not m["count"]:
        return [], ["폐쇄망 패키지 목록이 아직 없어 설치 가능 여부 검사를 건너뜀(관리자가 목록을 올리면 검사함)"]
    seen = set()
    for item in reqs:
        r, f = item if isinstance(item, tuple) else (item, where)
        at = f" ({f})" if f else ""
        if r["kind"] in ("git", "url"):
            warn.append(f"폐쇄망에서 설치 불가: {r['raw'][:80]} — {'git' if r['kind'] == 'git' else 'URL'} 설치는 내부 저장소에서 받을 수 없음{at}")
            continue
        if r["kind"] == "path":
            warn.append(f"로컬 경로·파일 설치: {r['raw'][:80]} — 제출 안에 그 파일이 있는지 확인{at}")
            continue
        n = norm(r["name"])
        key = (n, tuple(r["versions"]))
        if key in seen:
            continue
        seen.add(key)
        row = c.execute("SELECT versions FROM pkg_index WHERE name=?", (n,)).fetchone()
        if not row:
            warn.append(f"폐쇄망에서 설치 불가: {r['name']}{at}")
            continue
        have = json.loads(row[0]) if row[0] else None
        for v in r["versions"]:
            if have is not None and v not in have and not ("*" in v and any(h.startswith(v.split("*")[0]) for h in have)):
                warn.append(f"버전 없음: {r['name']}=={v} — 폐쇄망 목록의 버전 {', '.join(have[-5:])}{at}")
    info.append(f"폐쇄망 패키지 목록({m['source'] or '이름 없음'} · {m['count']}개)으로 설치 가능 여부 확인")
    return warn, info


def scan_tree(c, root, names):
    """제출 폴더(root, 상대 경로 목록 names)의 requirements*.txt · pyproject.toml · setup.sh → (warnings, info)"""
    if not meta(c)["count"]:
        return check_requirements(c, [])   # 목록이 비면 안내만
    reqs = []

    def read(rel):
        try:
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                return f.read(1024 * 1024)
        except OSError:
            return None
    found = False
    for rel in sorted(names):
        base = rel.rsplit("/", 1)[-1]
        if re.fullmatch(r"requirements[\w.-]*\.(txt|in)", base):
            t = read(rel)
            if t is not None:
                found = True
                reqs += [(r, rel) for r in parse_requirements(t)]
        elif base == "pyproject.toml":
            t = read(rel)
            if t is not None:
                found = True
                reqs += [(r, rel) for r in requirements_from_pyproject(t)]
        elif base == "setup.sh":
            t = read(rel)
            if t is not None:
                rs, files = requirements_from_setup_sh(t)
                found = found or bool(rs or files)
                reqs += [(r, rel) for r in rs]
                d = rel.rsplit("/", 1)[0] if "/" in rel else ""
                for fpath in files:
                    p = os.path.normpath(os.path.join(d, fpath))
                    if p in names and not re.fullmatch(r"requirements[\w.-]*\.(txt|in)", p.rsplit("/", 1)[-1]):
                        t2 = read(p)
                        reqs += [(r, p) for r in parse_requirements(t2 or "")]
    if not found:
        return [], ["설치할 패키지 목록(requirements*.txt·pyproject.toml·setup.sh 의 pip install)이 없습니다"]
    return check_requirements(c, reqs)
