# parts/NN-*.html 을 이어 붙여 index.html(포털 /guide, 이미지 참조)과 agent-portal.pdf(이미지 JPEG 내장)를 만든다
# 사용: python3 tools/build.py [--no-pdf]   (PDF 는 tools/pdf.mjs + playwright)
import base64, datetime, glob, html, io, json, os, re, subprocess, sys
from PIL import Image
from PIL.PngImagePlugin import PngInfo

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); os.chdir(root)
AUTHOR, COPY = "DongJu Kim (gggg8657)", "© 2026 gggg8657"
PDF_W, PDF_Q = 1600, 80  # PDF 에 넣는 그림: 최대 폭 px, JPEG 품질

# 1) 그림 PNG 메타데이터에 작성자 (ext/ 그림은 그 도구 저장소 몫) (이미 있으면 그대로 둠 — 파일 시각 안 바뀌게)
stamped = 0
for f in sorted(glob.glob('img/*.png')):
    im = Image.open(f)
    if im.text.get('Author') == AUTHOR and im.text.get('Copyright') == COPY: continue
    meta = PngInfo()
    for k, v in im.text.items():
        if k not in ('Author', 'Copyright'): meta.add_text(k, v)
    meta.add_text('Author', AUTHOR); meta.add_text('Copyright', COPY)
    im.load(); im.save(f, pnginfo=meta); stamped += 1

# 2) 본문 + 도구 목차(분류별, parts 순서대로)
try: TOOLS = json.load(open('../tools.json', encoding='utf-8'))
except OSError: TOOLS = []
titles = {t['dir']: t['title'] for t in TOOLS}
hidden = {t['dir'] for t in TOOLS if t.get('hidden')}  # 포털에서 숨긴 도구는 설명서에도 안 넣음(parts 파일은 남겨 둠)
parts = [f for f in sorted(glob.glob('parts/[0-9][0-9]-*.html')) if os.path.basename(f)[3:-5] not in hidden]
body = '\n'.join(open(f, encoding='utf-8').read() for f in parts)
groups, order = {}, {}
for m in re.finditer(r'<section class="tool" id="([^"]+)">\s*<p class="cat">([^<]*)</p>\s*<h2>(.*?)</h2>', body, re.S):
    d, cat, name = m.group(1), m.group(2).strip(), re.sub(r'<[^>]+>', '', m.group(3)).strip()
    t = titles.get(d, '')
    desc = t.split(' — ', 1)[1] if ' — ' in t else ''
    groups.setdefault(cat, []).append((d, name, desc)); order[d] = len(order) + 1
# 분류 색 토큰(style.css 의 .c-<slug>) — 모르는 분류는 c-etc
SLUG = {'포털': 'portal', '문서': 'doc', '회의': 'meet', '음성': 'voice', '그림': 'art', '데이터': 'data',
        '연구': 'research', '원자력': 'nuclear', '재미': 'fun', '토이': 'toy'}
cc = lambda c: 'c-' + SLUG.get(c, 'etc')
toc = '<div class="toc">\n' + '\n'.join(
    f'  <div class="tg {cc(c)}"><p class="chip">{html.escape(c)}<i>{len(items)}</i></p><ol>'
    + ''.join(f'<li><a href="#{d}"><span class="no">{order[d]:02d}</span><span class="pg">{{{{PG:{d}}}}}</span><b>{html.escape(n)}</b>'
              f'{"<small>" + html.escape(s) + "</small>" if s else ""}</a></li>' for d, n, s in items) + '</ol></div>'
    for c, items in groups.items()) + '\n</div>'
cats = '<div class="cv-cats">' + ''.join(
    f'<a class="{cc(c)}" href="#{items[0][0]}"><b>{len(items)}</b><span>{html.escape(c)}</span></a>'
    for c, items in groups.items()) + '</div>'
menu = ''.join(f'<div class="{cc(c)}"><span>{html.escape(c)}</span>' + ''.join(f'<a href="#{d}">{html.escape(n)}</a>' for d, n, _ in items) + '</div>'
               for c, items in groups.items())
# 도구 섹션 머리: 분류 칩 · 번호 · 단계 수를 붙이고 번호를 01 꼴로 (parts 의 HTML 은 그대로)
def head(m):
    d, cat, name, one = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
    sec = body[m.end():body.find('</section>', m.end())]
    n = sec.count('class="step"')
    return (f'<section class="tool {cc(cat)}" id="{d}">\n  <header class="th"><div class="th-meta"><p class="cat chip">{cat}</p>'
            f'{f"<span class=th-n>{n}단계</span>" if n else ""}</div><span class="th-no">{order[d]:02d}</span>'
            f'<h2>{name}</h2><p class="one">{one}</p></header>')
body = re.sub(r'<section class="tool" id="([^"]+)">\s*<p class="cat">([^<]*)</p>\s*<h2>(.*?)</h2>\s*<p>(.*?)</p>', head, body, flags=re.S)
body = re.sub(r'<span class="n">(\d)</span>', r'<span class="n">0\1</span>', body)
n_tools = sum(len(v) for c, v in groups.items() if c != '포털')
today = datetime.date.today()
body = body.replace('{{TOC}}', toc).replace('{{CATS}}', cats).replace('{{N}}', str(n_tools)).replace('{{DATE}}', f'{today.year}. {today.month}. {today.day}.')
# 끝: 출처·라이선스 — 도구 저장소마다 THIRD_PARTY_NOTICES.md (없으면 저장소 첫 화면)
def repo(d):
    g = lambda *a: subprocess.run(['git', '-C', os.path.join('..', '..', d), *a], capture_output=True, text=True).stdout.strip()
    url = re.sub(r'\.git$', '', g('remote', 'get-url', 'origin'))
    if not url.startswith('https://github.com/'): return None, False
    has = os.path.exists(os.path.join('..', '..', d, 'THIRD_PARTY_NOTICES.md'))
    return (f"{url}/blob/{g('branch', '--show-current') or 'main'}/THIRD_PARTY_NOTICES.md" if has else url), has
lic, missing = [], []
for c, items in groups.items():
    for d, n, _ in items:
        u, has = repo(d)
        if not has: missing.append(d)
        if u: lic.append(f'  <li><b>{html.escape(n)}</b><a href="{u}">{html.escape(u.replace("https://", ""))}</a></li>')
src = '<p class="src">예시 그림: Machala et al., Nature Communications 16, 988 (2025), Fig. 6e (CC BY 4.0)</p>\n' if 'Machala' in body else ''
closing = ('<section class="tool closing c-etc" id="licenses">\n  <header class="th"><div class="th-meta"><p class="cat chip">부록</p></div>'
           '<h2>출처·라이선스</h2>\n  <p class="one">도구마다 쓰는 오픈소스·모델의 출처와 라이선스는 각 저장소의 THIRD_PARTY_NOTICES.md 에 있습니다.</p></header>\n'
           '  <ul class="lic">\n' + '\n'.join(lic) + '\n  </ul>\n' + src +
           '  <div class="colophon"><p>에이전트 포털 사용 설명서</p><p>인공지능응용연구실 · 2026 · 내부 시험용 · 배포용 아님</p></div>\n</section>\n')
body += '\n' + closing

# 3) 화면용 위쪽 막대(포털로 · 목차 · PDF) — 인쇄·PDF 에서는 숨김
bar = ('<nav class="gbar"><div class="in"><a class="back" href="/">← 포털로</a><a class="t" href="#top">에이전트 포털 사용 설명서</a>'
       f'<details><summary>목차</summary><div class="menu">{menu}</div></details>'
       '<span class="nd">내부 시험용 · 배포용 아님</span><a class="pdf" href="agent-portal.pdf" download="에이전트-포털-사용-설명서.pdf">PDF 내려받기</a></div></nav>\n'
       '<script>addEventListener("click",e=>{const d=document.querySelector(".gbar details");if(d.open&&(e.target.closest(".menu a")||!e.target.closest(".gbar details")))d.open=false})</script>\n')

css = open('style.css', encoding='utf-8').read()
head = ('<title>에이전트 포털 사용 설명서</title>\n<meta name="author" content="DongJu Kim (gggg8657)">\n'
        '<link rel="preload" href="fonts/PretendardVariable.woff2" as="font" type="font/woff2" crossorigin>\n'
        '<style>' + css + '</style>\n')
frag = '<div class="wrap">\n' + body + '\n</div>'
full = lambda s: ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  + head + '</head><body>' + s + '</body></html>')
pg = lambda s, f: re.sub(r'\{\{PG:([^}]+)\}\}', lambda m: f(m.group(1)), s)  # 목차 쪽번호 자리 (화면은 비움)
open('index.html', 'w', encoding='utf-8').write(full(bar + pg(frag, lambda d: '')))

# 4) PDF: 그림을 JPEG 로 줄여 넣은 임시 HTML → tools/pdf.mjs
def emb(m):  # img/<그림> 또는 ext/<도구>/<그림>(= ../../<도구>/static/guide/, 공개 저장소에 안 넣는 그림)
    f = m.group(1)
    if f.startswith('ext/'): _, d, n = f.split('/', 2); f = os.path.join('..', '..', d, 'static', 'guide', n)
    im = Image.open(f).convert('RGB')
    if im.width > PDF_W: im = im.resize((PDF_W, round(im.height * PDF_W / im.width)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, 'JPEG', quality=PDF_Q, optimize=True, progressive=True)
    return 'src="data:image/jpeg;base64,' + base64.b64encode(b.getvalue()).decode() + '"'
print(len(parts), 'parts ·', n_tools, 'tools ·', stamped, 'img stamped')
if missing: print('THIRD_PARTY_NOTICES.md 없음:', ' '.join(missing))
if '--no-pdf' not in sys.argv:
    tmp, pfrag = 'tools/_print.html', re.sub(r'src="((?:img|ext)/[^"]+)"', emb, frag)
    def pdf(f):
        fonts = 'file://' + os.path.abspath('fonts') + '/'  # 임시 HTML 은 tools/ 에 있으므로 글꼴은 절대 경로로
        open(tmp, 'w', encoding='utf-8').write(full(pg(pfrag, f)).replace('url(fonts/', 'url(' + fonts).replace('href="fonts/', 'href="' + fonts))
        subprocess.run(['node', 'tools/pdf.mjs', tmp, 'agent-portal.pdf'], check=True)
    pdf(lambda d: '00')  # 1차: 자리만 잡고 찍어서 섹션 시작 쪽을 PDF 이름표(#id)에서 읽는다
    try:
        import logging; logging.getLogger('pypdf').setLevel(logging.ERROR)
        from pypdf import PdfReader
        r = PdfReader('agent-portal.pdf')
        pages = {k.lstrip('/'): r.get_destination_page_number(v) + 1 for k, v in r.named_destinations.items()}
        pdf(lambda d: str(pages.get(d, '')))
    except ImportError: pass
    os.remove(tmp)
    try:  # PDF 정보에 작성자
        import logging; logging.getLogger('pypdf').setLevel(logging.ERROR)  # Chromium PDF 의 trailer 크기 경고
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader('agent-portal.pdf'))
        w.add_metadata({'/Title': '에이전트 포털 사용 설명서', '/Author': AUTHOR, '/Subject': COPY})
        w.write('agent-portal.pdf')
    except ImportError: pass
    print('agent-portal.pdf', round(os.path.getsize('agent-portal.pdf') / 1e6, 1), 'MB')
