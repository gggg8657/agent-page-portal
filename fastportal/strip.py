"""기존 도구 화면(/t/<도구>/ 첫 HTML)에서 주석을 걷어 내기 — 일반 사용자에게만(관리자는 원본). 동작은 바꾸지 않는 변환만 한다.
지우는 것: HTML 주석(<!-- -->, 조건부 주석·저작권/라이선스 주석은 남김), <script> 안 '줄 맨 앞' 의 // 줄 주석과 /* */ 블록,
<style> 안 '줄 맨 앞' 의 /* */ 블록. 줄 중간 주석·문자열·템플릿 리터럴·정규식 안의 글자는 건드리지 않음.
- 정규식이 아니라 작은 토크나이저: 문자열('·"·`, ${} 중첩)·정규식 리터럴(문자 클래스 포함)·주석 상태를 따라감.
  줄 맨 앞(코드 상태에서 공백만 지난 자리)의 // 와 /* 는 JS 문법상 언제나 주석이다(정규식은 / 다음이 * 나 / 일 수 없고 줄을 넘지 못함).
- 블록 주석이 줄바꿈을 품고 있었으면 그 자리에 줄바꿈이 남는다(자동 세미콜론 규칙이 그대로).
- 확신이 없으면(문자열·템플릿·주석이 닫히지 않음, 모르는 script type) 원본 그대로 — 실패는 늘 '안 지움' 쪽.
결과는 원본 해시로 캐시(도구 화면 수십 개라 작음)."""
import hashlib, re
from collections import OrderedDict

KEEP = re.compile(r"©|\(c\)|copyright|licen[cs]e|spdx|@preserve|저작", re.I)   # 저작권·라이선스 표기는 남김
JS_TYPES = {"", "text/javascript", "application/javascript", "module", "text/ecmascript", "application/ecmascript"}
RAW_TEXT = {"textarea", "title", "xmp"}   # 안의 '<!--' 는 주석이 아니라 글자
REGEX_AFTER_WORD = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do", "else", "yield", "await"}
REGEX_AFTER_PUNCT = set("(,=:[!&|?{};+-*%<>~^")


class Unsure(Exception):
    """토크나이저가 확신하지 못함 — 원본을 그대로 내보냄"""


def _drop_indent(o):
    """o(출력 조각 목록) 끝의 같은 줄 들여쓰기(공백·탭)를 지움 — 지운 주석 줄이 빈 들여쓰기로 남지 않게(부르는 쪽이 '이 줄 앞은 공백뿐'을 확인)"""
    while o:
        t = o[-1].rstrip(" \t")
        if t:
            o[-1] = t
            return
        o.pop()


def _skip_rest_blank(s, i):
    """i 부터 줄 끝까지 공백뿐이면 다음 줄 시작 위치, 아니면 None"""
    j = i
    while j < len(s) and s[j] in " \t\r":
        j += 1
    if j >= len(s):
        return j
    return j + 1 if s[j] == "\n" else None


def strip_js(s):
    o = []
    i, n = 0, len(s)
    tmpl = []            # 템플릿 ${ } 안: 각 단계의 { 깊이
    line_start = True    # 코드 상태에서 이 줄에 공백만 지났는지
    last, last2 = "", ""   # 앞의 의미 있는 토큰(정규식/나누기 판정)

    def scan_template(i):
        """` 안쪽(i 는 내용 시작)을 ` 또는 ${ 까지 그대로 복사 → (다음 위치, 끝났는지)"""
        while i < n:
            c = s[i]
            if c == "\\":
                o.append(s[i:i + 2]); i += 2; continue
            if c == "`":
                o.append(c); return i + 1, True
            if c == "$" and i + 1 < n and s[i + 1] == "{":
                o.append("${"); return i + 2, False
            o.append(c); i += 1
        raise Unsure("템플릿 리터럴이 닫히지 않음")

    while i < n:
        c = s[i]
        if c == "\n":
            o.append(c); i += 1; line_start = True; continue
        if c in " \t\r\f\v":
            o.append(c); i += 1; continue
        nx = s[i + 1] if i + 1 < n else ""
        if c == "/" and nx == "/":   # 줄 주석
            j = s.find("\n", i)
            j = n if j < 0 else j
            if line_start and not KEEP.search(s[i:j]):
                _drop_indent(o)
                i = j + 1 if j < n else n   # 그 줄(줄바꿈 포함)을 통째로 — 앞 줄의 줄바꿈이 남아 있음
                if not o or o[-1].endswith("\n"):
                    continue
                o.append("\n"); continue
            o.append(s[i:j]); i = j; continue   # 줄 중간 주석은 그대로(안의 글자를 해석하지 않음)
        if c == "/" and nx == "*":   # 블록 주석
            j = s.find("*/", i + 2)
            if j < 0:
                raise Unsure("블록 주석이 닫히지 않음")
            body = s[i:j + 2]
            if line_start and not KEEP.search(body):
                _drop_indent(o)
                k = _skip_rest_blank(s, j + 2)
                if k is not None:      # 주석 뒤가 빈 줄 → 줄째 지움(줄바꿈은 앞 줄 것이 남음)
                    i = k
                    if o and not o[-1].endswith("\n") and k <= n and s[k - 1:k] == "\n":
                        o.append("\n")
                    line_start = True
                    continue
                if "\n" in body and o and not o[-1].endswith("\n"):
                    o.append("\n")
                i = j + 2
                while i < n and s[i] in " \t":   # 주석 뒤 공백도(줄 맨 앞이 되므로)
                    i += 1
                continue               # 같은 줄에 코드가 이어짐 — 줄 맨 앞 상태 유지
            o.append(body); i = j + 2
            if "\n" in body:
                line_start = False
            continue
        line_start = False
        if c in "'\"":
            j = i + 1
            while j < n and s[j] != c:
                if s[j] == "\\":
                    j += 2; continue
                if s[j] == "\n":
                    raise Unsure("문자열이 줄을 넘음")
                j += 1
            if j >= n:
                raise Unsure("문자열이 닫히지 않음")
            o.append(s[i:j + 1]); i = j + 1; last2, last = last, "str"; continue
        if c == "`":
            o.append(c)
            i, done = scan_template(i + 1)
            if not done:
                tmpl.append(0); last2, last = last, "("; continue
            last2, last = last, "str"; continue
        if c == "{":
            if tmpl:
                tmpl[-1] += 1
            o.append(c); i += 1; last2, last = last, c; continue
        if c == "}":
            if tmpl and tmpl[-1] == 0:   # ${ … } 끝 → 템플릿 안으로
                tmpl.pop(); o.append(c)
                i, done = scan_template(i + 1)
                if not done:
                    tmpl.append(0); last2, last = last, "("; continue
                last2, last = last, "str"; continue
            if tmpl:
                tmpl[-1] -= 1
            o.append(c); i += 1; last2, last = last, c; continue
        if c == "/":
            postfix = last in ("+", "-") and last2 == last   # i++ / 2
            if (last == "" or last in REGEX_AFTER_PUNCT or last in REGEX_AFTER_WORD or last == "}") and not postfix:
                j, cls = i + 1, False
                while j < n:
                    d = s[j]
                    if d == "\\":
                        j += 2; continue
                    if d == "\n":
                        raise Unsure("정규식이 줄을 넘음")
                    if d == "[":
                        cls = True
                    elif d == "]":
                        cls = False
                    elif d == "/" and not cls:
                        break
                    j += 1
                if j >= n:
                    raise Unsure("정규식이 닫히지 않음")
                j += 1
                while j < n and (s[j].isalnum() or s[j] in "_$"):
                    j += 1
                o.append(s[i:j]); i = j; last2, last = last, "str"; continue
            o.append(c); i += 1; last2, last = last, c; continue
        if c.isalnum() or c in "_$" or ord(c) > 127:
            j = i
            while j < n and (s[j].isalnum() or s[j] in "_$" or ord(s[j]) > 127):
                j += 1
            w = s[i:j]
            o.append(w); i = j
            if w == "." or (w[0].isdigit()):
                last2, last = last, "num"
            else:
                last2, last = last, w
            continue
        o.append(c); i += 1
        if c == "." and last == "num":
            continue
        last2, last = last, (")" if c in ")]" else c)
    if tmpl:
        raise Unsure("템플릿 ${ 가 닫히지 않음")
    return "".join(o)


def strip_css(s):
    """줄 맨 앞의 /* */ 만(문자열 안 글자는 그대로). 줄 중간 주석은 남김"""
    o, i, n, line_start = [], 0, len(s), True
    while i < n:
        c = s[i]
        if c == "\n":
            o.append(c); i += 1; line_start = True; continue
        if c in " \t\r\f":
            o.append(c); i += 1; continue
        if c == "/" and s[i + 1:i + 2] == "*":
            j = s.find("*/", i + 2)
            if j < 0:
                raise Unsure("CSS 주석이 닫히지 않음")
            body = s[i:j + 2]
            if line_start and not KEEP.search(body):
                _drop_indent(o)
                k = _skip_rest_blank(s, j + 2)
                if k is not None:
                    i = k; continue
                i = j + 2; continue
            o.append(body); i = j + 2; continue
        line_start = False
        if c in "'\"":
            j = i + 1
            while j < n and s[j] != c:
                if s[j] == "\\":
                    j += 2; continue
                if s[j] == "\n":
                    raise Unsure("CSS 문자열이 줄을 넘음")
                j += 1
            if j >= n:
                raise Unsure("CSS 문자열이 닫히지 않음")
            o.append(s[i:j + 1]); i = j + 1; continue
        o.append(c); i += 1
    return "".join(o)


TAG = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)
TYPE_ATTR = re.compile(r"""\btype\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)


def strip_html(h):
    o, i, n = [], 0, len(h)
    while i < n:
        j = h.find("<", i)
        if j < 0:
            o.append(h[i:]); break
        o.append(h[i:j]); i = j
        if h.startswith("<!--", i):
            k = h.find("-->", i + 4)
            if k < 0:
                raise Unsure("HTML 주석이 닫히지 않음")
            body = h[i:k + 3]
            if body.startswith("<!--[if") or body.startswith("<!--<![") or KEEP.search(body):
                o.append(body); i = k + 3; continue
            # 주석만 있는 줄이면 줄째(앞 들여쓰기·뒤 줄바꿈까지), 아니면 주석만
            ls = h.rfind("\n", 0, i) + 1
            k2 = _skip_rest_blank(h, k + 3)
            if h[ls:i].strip(" \t") == "" and k2 is not None:
                _drop_indent(o)
                i = k2
            else:
                i = k + 3
            continue
        m = TAG.match(h, i)
        if not m:
            k = h.find(">", i)   # </…>, <!doctype …>, 맨 '<' 글자
            if h.startswith("</", i) or h.startswith("<!", i) or h.startswith("<?", i):
                k = n - 1 if k < 0 else k
                o.append(h[i:k + 1]); i = k + 1
            else:
                o.append("<"); i += 1
            continue
        name = m[1].lower()
        o.append(m[0]); i = m.end()
        if name in ("script", "style") or name in RAW_TEXT:
            close = re.compile(r"</" + name + r"\s*>", re.I).search(h, i)
            if not close:
                raise Unsure(f"<{name}> 가 닫히지 않음")
            inner = h[i:close.start()]
            if name == "script":
                t = TYPE_ATTR.search(m[2] or "")
                typ = ((t[1] or t[2] or t[3]) if t else "").strip().lower()
                if typ in JS_TYPES:
                    inner = strip_js(inner)
            elif name == "style":
                inner = strip_css(inner)
            o.append(inner); o.append(close[0]); i = close.end()
    return "".join(o)


_CACHE = OrderedDict()
CACHE_MAX = 96


def stripped(html):
    """주석을 걷은 HTML(캐시). 확신이 없으면 원본 — 실패를 늘 '안 지움' 쪽으로"""
    key = hashlib.sha1(html.encode("utf-8", "surrogatepass")).digest()
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    try:
        out = strip_html(html)
    except Unsure as e:
        print("strip: 원본 그대로 —", e, flush=True)
        out = html
    except Exception as e:   # 예상 못 한 경우도 원본(화면을 깨뜨리지 않음)
        print("strip: 오류, 원본 그대로 —", type(e).__name__, e, flush=True)
        out = html
    _CACHE[key] = out
    if len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)
    return out
