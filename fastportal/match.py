"""할 일로 도구 찾기 — capabilities.json(도구별 '할 수 있는 일')에 검색어를 맞춰 도구 순위를 낸다. GET /api/match?q=…[&ext=.pdf]

  신호 셋을 합친다:
    lexical  할 일·입출력·도구 이름에 검색어 낱말이 들어 있는지(한글은 자모로 풀어 비교 — '회의ㄹ'처럼 치는 중인 글자·띄어쓰기 차이도 맞음),
             낱말마다 흔한 정도(IDF)로 무게를 줌 + 글자 두 개 조각(bigram) 겹침을 약하게
    synonyms 할 일마다 적은 같은 뜻 표현(syn)과 공통 유의어 묶음(thesaurus)으로 넓힌 낱말
    semantic 임베딩(Ollama bge-m3 — EMBED_API·EMBED_BASE_URL·EMBED_MODEL, 도구들과 같은 env) 코사인. 할 일 벡터는 기동 때(카탈로그가
             바뀌면 다시) 뒤에서 만들어 _data/match/ 에 해시 이름으로 저장, 검색어 벡터는 잠깐(QCACHE_S) 기억. 임베딩 서버가 없거나 느리면
             lexical+synonyms 만으로(응답의 semantic=false)
  숨긴 도구는 빼고, owner_only(팀 밸런스 맵 saju-local — 주인의 개인 도구)는 관리자(주인)에게만.
  여러 단계를 뜻하면(예: 녹음해서 공문으로) chains 의 연결 흐름 어댑터 순서로 '흐름 제안'(만들기 → 에이전트 연결 화면에 미리 채움 — 저장은 사용자가)
env: MATCH_RATE_N(계정당 1분에 검색 수, 기본 120) MATCH_EMBED_TIMEOUT(검색어 임베딩 기다리는 초, 기본 1.2) MATCH_QCACHE_S(검색어 벡터 기억 초, 600)"""
import array, base64, hashlib, json, math, os, re, threading, time, unicodedata, urllib.parse, urllib.request

from . import config, tools

CATALOG = os.path.join(config.ROOT, "capabilities.json")
CACHE_DIR = os.path.join(config.DATA, "match")
RATE_N, RATE_S = int(os.environ.get("MATCH_RATE_N", "120")), 60
EMBED_TIMEOUT = float(os.environ.get("MATCH_EMBED_TIMEOUT", "1.2"))
QCACHE_S = float(os.environ.get("MATCH_QCACHE_S", "600"))
Q_MAX = 200
LIMIT = 8
# 점수 무게(평가 묶음 fastportal/tests/match_eval.json 으로 맞춤 — scripts/match_eval.py)
#   2026-10-20: 무게 3×3×3 · 코사인 범위 3×2 = 162가지를 두 묶음(queries·hard)에서 비교해 고름 — 차이는 작음(recall@3 ±0.01)
W_LEX, W_SYN, W_SEM = 0.4, 0.45, 0.7
SEM_LO, SEM_HI = 0.36, 0.66      # 이 코사인 아래는 0, 위는 1 로 펼침(bge-m3 한국어 짧은 문구 기준)
MIN_SCORE = 0.25                 # 이보다 낮으면 결과에서 뺌
MIN_SCORE_LEX = 0.15             # 임베딩 없이(lexical+synonyms 만) 셀 때의 하한 — 의미 점수 몫이 없으므로 낮춤
MIN_REL = 0.35                   # 1등 점수의 이 비율보다 낮은 도구도 뺌(검색 결과가 길게 늘어지지 않게)
TASK_SURE = 0.62                 # 물어보기: 이 이상이면 바로 도구 카드
TASK_MAYBE = 0.3                 # 물어보기: 이 사이면 LLM 에 '할 일이 있는 말인지' 물어봄(사용자 글은 데이터로), LLM 이 안 되면 TASK_FALLBACK
TASK_FALLBACK = 0.45

# ── 한글 자모 ───────────────────────────────────────────────────────────
_L = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_V = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_T = ["", *"ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"]
_KEEP = re.compile(r"[0-9a-zㄱ-ㆎ가-힣]+")


def jamo(s):
    """글 → 비교용 자모 문자열(소문자, 글자·숫자만, 띄어쓰기·기호 없음). 완성형 음절은 초·중·종성 호환 자모로 풂"""
    s = unicodedata.normalize("NFKC", s or "").lower()
    out = []
    for ch in "".join(_KEEP.findall(s)):
        o = ord(ch) - 0xAC00
        if 0 <= o < 11172:
            out.append(_L[o // 588] + _V[(o % 588) // 28] + _T[o % 28])
        else:
            out.append(ch)
    return "".join(out)


def compact(s):
    return "".join(_KEEP.findall(unicodedata.normalize("NFKC", s or "").lower()))


def bigrams(s):
    s = compact(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else ({s} if s else set())


# 낱말 끝 조사·어미(길이 순) — '회의록을' → '회의록', '정리해서' → '정리'
_SUFFIX = sorted(["으로", "에서", "에게", "해서", "하고", "하는", "해줘", "해 줘", "해주세요", "하기", "부터", "까지", "처럼", "이랑", "랑", "을", "를", "이", "가", "은",
                  "는", "로", "와", "과", "의", "에", "도", "좀", "만", "할", "한", "된", "기"], key=len, reverse=True)
_STOP = {"하고", "싶어", "싶다", "싶은데", "싶어요", "하고싶어", "해줘", "주세요", "알려줘", "알려", "어떻게", "어디서", "무엇", "뭐", "좀", "그냥", "하는", "법",
         "방법", "있나요", "있어", "있을까", "할까", "해", "줘", "수", "것", "거", "도구", "에이전트", "어떤", "쓰면", "돼", "되나요", "나", "내", "제", "저", "우리"}


_WISH = re.compile(r"\s*(보고|하고|해 ?보고|알고|쓰고)?\s*싶\S*|\s*(해|아|어|여)\s*(줘|주세요|줄래|주실래요|주면)\S*")


def words(q):
    """검색어 → 낱말(조사·어미 떼고, 불용어 빼고). '…보고 싶어'·'…해 줘' 같은 바람·부탁 끝말은 먼저 지움"""
    out = []
    q = _WISH.sub(" ", unicodedata.normalize("NFKC", q or "").lower())
    for w in re.findall(r"[0-9a-z]+|[ㄱ-ㆎ가-힣]+", q):
        if w in _STOP:
            continue
        for sfx in _SUFFIX:
            if len(w) > len(sfx) + 1 and w.endswith(sfx):
                w = w[: -len(sfx)]
                break
        if w and w not in _STOP and w not in out:
            out.append(w)
    return out


# ── 카탈로그 ────────────────────────────────────────────────────────────
class Catalog:
    def __init__(self, raw):
        self.raw = raw
        self.links = raw.get("links") or {}
        self.chains = raw.get("chains") or []
        self.thes = {}
        for grp in raw.get("thesaurus") or []:
            for w in grp:
                self.thes.setdefault(compact(w), set()).update(compact(x) for x in grp if compact(x) != compact(w))
        self.tools = {t["dir"]: t for t in raw.get("tools") or []}
        self.caps = []   # (dir, cap, 필드 자모)
        for t in raw.get("tools") or []:
            for c in t.get("caps") or []:
                self.caps.append({"dir": t["dir"], "cap": c, "task": jamo(c["task"]), "syn": [jamo(x) for x in c.get("syn") or []],
                                  "io": jamo(f"{c.get('in', '')} {c.get('out', '')}"), "bi": bigrams(c["task"]) | set().union(*(bigrams(x) for x in c.get("syn") or [])),
                                  "files": [x.lower() for x in (c.get("files") or t.get("files") or [])]})
        self.texts = [f"{x['cap']['task']}. {', '.join(x['cap'].get('syn') or [])}. {x['cap'].get('in', '')} → {x['cap'].get('out', '')}" for x in self.caps]
        self.hash = hashlib.sha256(json.dumps(self.texts, ensure_ascii=False).encode()).hexdigest()[:16]
        n = max(1, len(self.caps))
        self._df = {}
        self._n = n

    def idf(self, wj):
        """낱말(자모) 하나의 흔함 — 할 일 문서 몇 개에 들어 있나(없으면 가장 드문 값)"""
        v = self._df.get(wj)
        if v is None:
            df = sum(1 for x in self.caps if wj in x["task"] or any(wj in s for s in x["syn"]) or wj in x["io"])
            v = self._df[wj] = math.log(1 + self._n / (1 + df))
        return v


_CAT = {"m": None, "v": None}
_CAT_LOCK = threading.Lock()


def catalog():
    m = os.stat(CATALOG).st_mtime_ns
    with _CAT_LOCK:
        if _CAT["m"] != m:
            _CAT.update(m=m, v=Catalog(json.load(open(CATALOG, encoding="utf-8"))))
            _EMB.update(hash=None)   # 할 일 벡터 다시
        return _CAT["v"]


# ── 임베딩 ──────────────────────────────────────────────────────────────
_EMB = {"hash": None, "vecs": None, "busy": False, "fail_t": 0.0, "err": ""}
_EMB_LOCK = threading.Lock()
_QV = {}   # 검색어 → (시각, 벡터)
_NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _embed_conf():
    api = (os.environ.get("EMBED_API") or "ollama").strip()
    base = (os.environ.get("EMBED_BASE_URL") or "").rstrip("/")
    model = os.environ.get("EMBED_MODEL") or "bge-m3"
    return api, base, model


def embed_request(texts):
    """(url, body, headers) — Ollama /api/embed 또는 OpenAI 호환 /v1/embeddings"""
    api, base, model = _embed_conf()
    if not base:
        return None
    if api == "openai":
        url = base + ("" if base.endswith("/v1") else "/v1") + "/embeddings"
        body = {"model": model, "input": texts}
    else:
        url = base + "/api/embed"
        body = {"model": model, "input": texts}
    key = os.environ.get("EMBED_API_KEY", "")
    return url, body, {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}


def parse_embed(j):
    if isinstance(j, dict) and isinstance(j.get("embeddings"), list):
        vs = j["embeddings"]
    elif isinstance(j, dict) and isinstance(j.get("data"), list):
        vs = [d.get("embedding") for d in sorted(j["data"], key=lambda d: d.get("index", 0))]
    else:
        raise ValueError("임베딩 응답 모양이 다릅니다")
    return [_unit(v) for v in vs]


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array.array("f", (x / n for x in v))


def embed_sync(texts, timeout):
    req = embed_request(texts)
    if not req:
        raise OSError("EMBED_BASE_URL 이 없습니다")
    url, body, hd = req
    with _NOPROXY.open(urllib.request.Request(url, json.dumps(body).encode(), hd), timeout=timeout) as r:
        return parse_embed(json.loads(r.read()))


def _cache_file(h):
    _, _, model = _embed_conf()
    return os.path.join(CACHE_DIR, f"caps-{re.sub(r'[^A-Za-z0-9_.-]', '_', model)}-{h}.json")


def _load_cache(h):
    try:
        j = json.load(open(_cache_file(h), encoding="utf-8"))
        vecs = []
        for b in j["vecs"]:
            a = array.array("f")
            a.frombytes(base64.b64decode(b))
            vecs.append(a)
        return vecs
    except (OSError, ValueError, KeyError):
        return None


def _save_cache(h, vecs):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_file(h) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"model": _embed_conf()[2], "hash": h, "dim": len(vecs[0]) if vecs else 0, "vecs": [base64.b64encode(v.tobytes()).decode() for v in vecs]}, f)
    os.replace(tmp, _cache_file(h))
    for x in os.listdir(CACHE_DIR):   # 예전 해시 파일 정리
        if x.startswith("caps-") and h not in x and x.endswith(".json"):
            try:
                os.remove(os.path.join(CACHE_DIR, x))
            except OSError:
                pass


def _build(cat):
    try:
        vecs = _load_cache(cat.hash)
        if vecs is None or len(vecs) != len(cat.texts):
            vecs = []
            for i in range(0, len(cat.texts), 32):
                vecs += embed_sync(cat.texts[i:i + 32], 120)
            _save_cache(cat.hash, vecs)
        with _EMB_LOCK:
            _EMB.update(hash=cat.hash, vecs=vecs, err="")
    except Exception as e:   # 임베딩 서버 없음·느림 — lexical 로만. 잠시 뒤 다시
        with _EMB_LOCK:
            _EMB.update(fail_t=time.time(), err=f"{type(e).__name__}")
        print("match: 할 일 임베딩 실패 —", type(e).__name__, str(e)[:200], flush=True)
    finally:
        with _EMB_LOCK:
            _EMB["busy"] = False


def cap_vectors():
    """할 일 벡터(준비 안 됐으면 None — 그때 뒤에서 만들기 시작)"""
    cat = catalog()
    with _EMB_LOCK:
        if _EMB["hash"] == cat.hash and _EMB["vecs"] is not None:
            return _EMB["vecs"]
        if _EMB["busy"] or time.time() - _EMB["fail_t"] < 60:
            return None
        _EMB["busy"] = True
    threading.Thread(target=_build, args=(cat,), daemon=True, name="match-embed").start()
    return None


def warm():
    """기동 때 — 할 일 벡터를 뒤에서 준비(캐시 파일이 있으면 바로)"""
    try:
        cap_vectors()
    except OSError:
        pass


def query_vector(q):
    """검색어 벡터 — 잠깐 기억. 실패·느림이면 None(그 검색은 lexical 로만). 임베딩이 막 실패했으면 1분 쉬고 다시"""
    k = compact(q)
    t = time.time()
    hit = _QV.get(k)
    if hit and t - hit[0] < QCACHE_S:
        return hit[1]
    if t - _EMB["fail_t"] < 60 and _EMB["vecs"] is None:
        return None
    try:
        v = embed_sync([q], EMBED_TIMEOUT)[0]
    except Exception:
        return None
    if len(_QV) > 512:
        for kk in sorted(_QV, key=lambda x: _QV[x][0])[:128]:
            _QV.pop(kk, None)
    _QV[k] = (t, v)
    return v


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def sims(qvec, vecs):
    """검색어 벡터와 할 일 벡터들의 코사인(둘 다 길이 1) — 할 일 155개 × 1024차원, 파이썬으로 약 10~20ms"""
    return [_dot(qvec, v) for v in vecs]


# ── 점수 ────────────────────────────────────────────────────────────────
def _contains(field, wj):
    return wj and wj in field


def user_hidden(t):
    """사용자 화면에서 빠지는 도구 — 숨김(hidden), 관리자가 끈 도구(toolstate.py 가 있으면 그 판정, 관리자에게도 안 보임)"""
    from . import toolstate
    return bool(toolstate.user_hidden(t))


def visible_dirs(is_owner):
    out = {}
    for t in tools.tools():
        if user_hidden(t):
            continue
        out[t["dir"]] = t
    cat = catalog()
    for d, e in cat.tools.items():
        if e.get("owner_only") and not is_owner:
            out.pop(d, None)
    return out


def rank(q, is_owner=False, ext=None, qvec=None, limit=LIMIT, use_sem=True):
    """검색어(와 파일 확장자) → [{dir, score, caps: [...], reason, signals}] 점수 순. qvec: 미리 만든 검색어 벡터(None 이면 lexical 만)"""
    cat = catalog()
    q = (q or "").strip()[:Q_MAX]
    ext = (ext or "").lower().strip()
    if ext and not ext.startswith("."):
        ext = "." + ext
    ws = words(q)
    qj, qc = jamo(q), compact(q)
    qbi = bigrams(q)
    wj = [(w, jamo(w), cat.idf(jamo(w))) for w in ws]
    tot = sum(i for _, _, i in wj) or 1.0
    vis = visible_dirs(is_owner)
    vecs = cap_vectors() if (use_sem and qvec is not None) else None
    cos_all = sims(qvec, vecs) if vecs is not None else None
    per = {}
    for i, x in enumerate(cat.caps):
        d = x["dir"]
        if d not in vis:
            continue
        t = vis[d]
        name = jamo(f"{t.get('name', '')} {t.get('title', '')} {' '.join(t.get('tags') or [])} {d}")
        lex = syn = 0.0
        hit_lex, hit_syn = [], []
        for w, j, idf in wj:
            if len(w) < 2:   # 한 글자 낱말(조사를 떼고 남은 '잡'·'써' 등)은 거의 아무 데나 맞음
                continue
            a = 1.0 if _contains(x["task"], j) else 0.7 if _contains(name, j) else 0.5 if _contains(x["io"], j) else 0.0
            b = 1.0 if any(_contains(s, j) for s in x["syn"]) else 0.0
            if not b:   # 유의어 묶음으로 넓힘
                for alt in cat.thes.get(compact(w), ()):
                    aj = jamo(alt)
                    if _contains(x["task"], aj) or any(_contains(s, aj) for s in x["syn"]):
                        b = 0.75
                        break
            lex += a * idf
            syn += b * idf
            if a:
                hit_lex.append(w)
            if b:
                hit_syn.append(w)
        lex /= tot
        syn /= tot
        if qj and len(qj) >= 3 and (qj in x["task"] or any(qj == s for s in x["syn"])):   # 검색어 통째가 할 일·같은 뜻 표현에
            syn = min(1.0, syn + 0.35)
        if qbi:   # 글자 조각 겹침(오타·어미 차이)
            ov = len(qbi & x["bi"]) / len(qbi)
            lex = max(lex, 0.6 * ov)
        sem = 0.0
        if cos_all is not None and i < len(cos_all):
            cos = cos_all[i]
            sem = min(1.0, max(0.0, (cos - SEM_LO) / (SEM_HI - SEM_LO)))
        fil = 0.0
        if ext:
            fil = 0.5 if ext in x["files"] else 0.0
        s = W_LEX * lex + W_SYN * syn + W_SEM * sem + fil
        if not q and ext:
            s = fil
        r = per.setdefault(d, {"caps": []})
        r["caps"].append((s, x["cap"], lex, syn, sem, hit_lex, hit_syn, fil))
    out = []
    for d, r in per.items():
        cs = sorted(r["caps"], key=lambda c: -c[0])
        best = cs[0]
        score = best[0] + 0.15 * (cs[1][0] if len(cs) > 1 else 0)
        if score < (MIN_SCORE if cos_all is not None else MIN_SCORE_LEX):
            continue
        good = [c for c in cs if c[0] >= max(MIN_SCORE, best[0] * 0.6)][:3]
        t = vis[d]
        out.append({"dir": d, "name": t.get("name") or d, "title": t.get("title") or d, "score": round(score, 3),
                    "reason": _reason(q, best, ext), "link": _link(d, best[1]),
                    "signals": {"lexical": round(best[2], 3), "synonyms": round(best[3], 3), "semantic": round(best[4], 3), "file": round(best[7], 3)},
                    "caps": [{"id": c[1]["id"], "task": c[1]["task"], "in": c[1].get("in", ""), "out": c[1].get("out", ""), "link": _link(d, c[1])} for c in good]})
    out.sort(key=lambda r: -r["score"])
    if out:
        out = [r for r in out if r["score"] >= out[0]["score"] * MIN_REL]
    return out[:limit]


def _reason(q, best, ext):
    s, cap, lex, syn, sem, hl, hs, fil = best
    if fil and not q:
        return f"{ext} 파일을 받는 기능: {cap['task']}"
    parts = []
    if hs and syn >= lex:
        parts.append(f"‘{hs[0]}’{' 외' if len(hs) > 1 else ''} — 같은 뜻 표현이 있는 기능")
    elif hl:
        parts.append(f"‘{hl[0]}’{' 외' if len(hl) > 1 else ''} 낱말이 맞는 기능")
    elif sem >= 0.5:
        parts.append("뜻이 비슷한 기능")
    else:
        parts.append("비슷한 글자가 있는 기능")
    if fil:
        parts.append(f"{ext} 파일을 받음")
    return " · ".join(parts) + f": {cap['task']}"


def _link(d, cap):
    """도구 화면 주소 — link.query 가 있으면 그 값을 붙임(그 도구 화면이 읽는 값만, links 표)"""
    q = ((cap.get("link") or {}).get("query")) or {}
    return f"/tool/{d}" + (("?" + urllib.parse.urlencode(q)) if q else "")


# ── 여러 단계(연결 흐름) ─────────────────────────────────────────────────
_ORDER_HINT = re.compile(r"(→|->|>|다음|그리고|해서|한 뒤|한뒤|하고 나서|후에|으로|로)")


def chain(q):
    """검색어가 두 단계 이상을 뜻하면 연결 흐름 제안 — 어댑터가 글에 나온 순서. 없으면 None"""
    from . import flows
    qc = compact(q)
    if len(qc) < 4:
        return None
    hits = []
    for c in catalog().chains:
        a = flows.ADAPTERS.get(c["adapter"])
        t = tools.tool(a["tool"]) if a else None
        if not a or not t or user_hidden(t):   # 숨긴·관리자가 끈 도구의 단계는 제안하지 않음
            continue
        pos = [qc.find(compact(t)) for t in c["terms"] if compact(t) and compact(t) in qc]
        if pos:
            hits.append((min(pos), c["adapter"]))
    hits.sort()
    seen, steps = set(), []
    for _, aid in hits:
        tool = flows.ADAPTERS[aid]["tool"]
        if aid in seen or any(flows.ADAPTERS[s]["tool"] == tool for s in seen):   # 한 도구는 한 번
            continue
        seen.add(aid)
        steps.append(aid)
    if len(steps) < 2 or not _ORDER_HINT.search(q):
        return None
    steps = steps[:flows.MAX_STEPS]
    over = {}
    for i, aid in enumerate(steps):
        if aid == "kordoc.draft":
            pre = next((p for p in flows.PRESETS if p in q), None) or ("회의록" if "meeting.minutes" in steps else None)
            if pre:
                over[f"o{i}.preset"] = pre
    try:   # 저장 규칙과 같은지(첫 단계만 파일 등) — 실제 저장은 화면에서 사용자가
        flows._clean_steps([{"adapter": a} for a in steps])
    except ValueError:
        return None
    name = " → ".join(flows.ADAPTERS[a]["title"] for a in steps)
    qs = urllib.parse.urlencode({"tab": "make", "m": "flow", "steps": ",".join(steps), "name": name[:60], **over})
    return {"name": name, "link": f"/create?{qs}",
            "steps": [{"adapter": a, "title": flows.ADAPTERS[a]["title"], "tool": flows.ADAPTERS[a]["tool"], "link": f"/tool/{flows.ADAPTERS[a]['tool']}"} for a in steps]}


# ── 속도 제한·요청 처리 ───────────────────────────────────────────────────
_RATE = {}
_RATE_LOCK = threading.Lock()


def allow(uid):
    t = time.time()
    with _RATE_LOCK:
        v = [x for x in _RATE.get(uid, ()) if x > t - RATE_S]
        if len(v) >= RATE_N:
            _RATE[uid] = v
            return False
        v.append(t)
        _RATE[uid] = v
        if len(_RATE) > 5000:
            for k in [k for k, x in _RATE.items() if not x or x[-1] < t - RATE_S]:
                _RATE.pop(k, None)
        return True


def search(q, is_owner, ext=None, limit=LIMIT):
    """검색 하나(동기 — 스레드에서): 검색어 벡터(기억·짧은 시간 제한) + 순위 + 흐름 제안"""
    t0 = time.time()
    qv = query_vector(q) if q.strip() else None
    vecs = cap_vectors()
    res = rank(q, is_owner, ext, qv if vecs is not None else None, limit)
    return {"q": q, "ext": ext or None, "tools": res, "flow": chain(q) if q else None,
            "semantic": qv is not None and vecs is not None, "ms": int((time.time() - t0) * 1000)}


def handle(ctx):
    """GET /api/match?q=…&ext=.pdf&limit=8"""
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(ctx.raw).query)
    q = (qs.get("q", [""])[0] or "").strip()
    ext = (qs.get("ext", [""])[0] or "").strip()[:12]
    if ext and not re.fullmatch(r"\.?[A-Za-z0-9]{1,10}", ext):
        return ctx.send({"error": "ext 는 .pdf 같은 확장자", "code": "bad_request"}, code=400)
    try:
        lim = max(1, min(20, int(qs.get("limit", [str(LIMIT)])[0])))
    except ValueError:
        return ctx.send({"error": "limit 은 숫자", "code": "bad_request"}, code=400)
    if len(q) > Q_MAX:
        return ctx.send({"error": f"검색어는 {Q_MAX}자 이하", "code": "bad_request"}, code=400)
    if not q and not ext:
        return ctx.send({"q": "", "ext": None, "tools": [], "flow": None, "semantic": False, "ms": 0})
    if not allow(ctx.uid):
        return ctx.send({"error": "검색이 너무 잦습니다 — 잠시 뒤 다시 해 주세요", "code": "rate_limited"}, code=429, headers=[("Retry-After", "10")])
    return ctx.send(search(q, ctx.is_owner, ext, lim))


def status():
    cat = catalog()
    return {"caps": len(cat.caps), "tools": len(cat.tools), "embed_ready": _EMB["hash"] == cat.hash and _EMB["vecs"] is not None, "embed_error": _EMB["err"] or None}


def internal_post(ctx, body):
    """POST /api/internal/match {tool, user, q, limit?} — 도구(파동이 펫 등)가 쿠키 없이 부르는 할 일 검색. 접속지 + 그 도구의 토큰(jobs.internal_ok).
    user 는 도구가 받은 X-Agent-User(주인은 owner) — 팀 밸런스 맵(owner_only)은 주인·관리자일 때만"""
    from . import accounts, jobs
    try:
        req = json.loads(body or b"{}")
        if not isinstance(req, dict):
            raise ValueError
    except ValueError:
        return ctx.send({"error": "요청 본문은 JSON 객체여야 합니다"}, code=400)
    d = str(req.get("tool") or "")
    if not tools.tool(d) or not jobs.internal_ok(ctx.client, ctx.headers.get("x-portal-jobs-token"), d):
        return ctx.send({"error": "forbidden"}, code=403)
    q = req.get("q")
    if not isinstance(q, str) or not q.strip() or len(q) > Q_MAX:
        return ctx.send({"error": f"q 는 1~{Q_MAX}자 글"}, code=400)
    user = str(req.get("user") or "")[:80]
    if not allow("tool:" + d):
        return ctx.send({"error": "검색이 너무 잦습니다", "code": "rate_limited"}, code=429, headers=[("Retry-After", "10")])
    try:
        lim = max(1, min(8, int(req.get("limit") or 3)))
    except (TypeError, ValueError):
        lim = 3
    owner = user == "owner" or (bool(user) and accounts.is_admin(user))
    r = search(q.strip(), owner, None, lim)
    return ctx.send({"tools": [{k: x[k] for k in ("dir", "name", "score", "reason", "link")} | {"caps": [c["task"] for c in x["caps"]]} for x in r["tools"]],
                     "task": bool(r["tools"]) and r["tools"][0]["score"] >= TASK_SURE, "semantic": r["semantic"]})
