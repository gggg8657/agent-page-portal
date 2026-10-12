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
parts = sorted(glob.glob('parts/[0-9][0-9]-*.html'))
body = '\n'.join(open(f, encoding='utf-8').read() for f in parts)
try: titles = {t['dir']: t['title'] for t in json.load(open('../tools.json', encoding='utf-8'))}
except OSError: titles = {}
groups = {}
for m in re.finditer(r'<section class="tool" id="([^"]+)">\s*<p class="cat">([^<]*)</p>\s*<h2>(.*?)</h2>', body, re.S):
    d, cat, name = m.group(1), m.group(2).strip(), re.sub(r'<[^>]+>', '', m.group(3)).strip()
    t = titles.get(d, '')
    desc = t.split(' — ', 1)[1] if ' — ' in t else ''
    groups.setdefault(cat, []).append((d, name, desc))
toc = '<ul class="toc">\n' + '\n'.join(
    f'  <li><span>{html.escape(c)}</span><a href="#{d}"><b>{html.escape(n)}</b>{" · " + html.escape(s) if s else ""}</a></li>'
    for c, items in groups.items() for d, n, s in items) + '\n</ul>'
menu = ''.join(f'<div><span>{html.escape(c)}</span>' + ''.join(f'<a href="#{d}">{html.escape(n)}</a>' for d, n, _ in items) + '</div>'
               for c, items in groups.items())
n_tools = sum(len(v) for c, v in groups.items() if c != '포털')
today = datetime.date.today()
body = body.replace('{{TOC}}', toc).replace('{{N}}', str(n_tools)).replace('{{DATE}}', f'{today.year}. {today.month}. {today.day}.')
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
        if u: lic.append(f'  <li><b>{html.escape(n)}</b> · <a href="{u}">{html.escape(u.replace("https://", ""))}</a></li>')
closing = ('<section class="tool" id="licenses">\n  <h2>출처·라이선스</h2>\n'
           '  <p>도구마다 쓰는 오픈소스·모델의 출처와 라이선스는 각 저장소의 THIRD_PARTY_NOTICES.md 에 있습니다.</p>\n'
           '  <ul class="files">\n' + '\n'.join(lic) + '\n  </ul>\n</section>\n')
body += '\n' + closing
src = '<p class="src">예시 그림: Machala et al., Nature Communications 16, 988 (2025), Fig. 6e (CC BY 4.0)</p>\n' if 'Machala' in body else ''

# 3) 화면용 위쪽 막대(포털로 · 목차 · PDF) — 인쇄·PDF 에서는 숨김
bar_css = '''
.gbar{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(4px)}
.gbar .in{max-width:1000px;margin:0 auto;padding:8px 24px;display:flex;align-items:center;gap:14px;font-size:14px}
.gbar a{color:var(--ink);text-decoration:none}.gbar .t{font-weight:700;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gbar .back{color:var(--ink-2)}.gbar .back:hover,.gbar .menu a:hover{text-decoration:underline}
.gbar details{position:relative}.gbar summary{cursor:pointer;list-style:none;color:var(--ink-2)}.gbar summary::-webkit-details-marker{display:none}
.gbar .menu{position:absolute;right:0;top:30px;width:min(560px,calc(100vw - 32px));max-height:70vh;overflow:auto;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 24px rgba(0,0,0,.12);padding:10px 14px;columns:2;column-gap:24px}
.gbar .menu div{break-inside:avoid;margin-bottom:8px}.gbar .menu span{display:block;font-size:12px;color:#888}.gbar .menu a{display:block;padding:2px 0}
.gbar .pdf{border:1px solid var(--ink);border-radius:6px;padding:4px 12px;font-weight:500;white-space:nowrap}.gbar .pdf:hover{background:var(--ink);color:#fff}
.gbar .nd{font-size:12px;color:#999;white-space:nowrap}
.tool,#top{scroll-margin-top:60px}
@media screen and (max-width:760px){.gbar .in{padding:8px 16px;gap:10px}.gbar .t,.gbar .nd{display:none}.gbar details{margin-left:auto}.gbar .menu{position:fixed;left:16px;right:16px;top:44px;width:auto;columns:1}}
@media print{.gbar{display:none}}
'''
bar = ('<nav class="gbar"><div class="in"><a class="back" href="/">← 포털로</a><a class="t" href="#top">에이전트 포털 사용 설명서</a>'
       f'<details><summary>목차 ▾</summary><div class="menu">{menu}</div></details>'
       '<span class="nd">내부 시험용 · 배포용 아님</span><a class="pdf" href="agent-portal.pdf" download="에이전트-포털-사용-설명서.pdf">PDF 내려받기</a></div></nav>\n'
       '<script>addEventListener("click",e=>{const d=document.querySelector(".gbar details");if(d.open&&(e.target.closest(".menu a")||!e.target.closest(".gbar details")))d.open=false})</script>\n')

css = open('style.css', encoding='utf-8').read()
head = ('<title>에이전트 포털 사용 설명서</title>\n<meta name="author" content="DongJu Kim (gggg8657)">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap">\n'
        '<style>' + css + bar_css + '</style>\n')
frag = '<div class="wrap">\n' + body + '\n' + src + '</div>'
full = lambda s: ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  + head + '</head><body>' + s + '</body></html>')
open('index.html', 'w', encoding='utf-8').write(full(bar + frag))

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
    tmp = 'tools/_print.html'
    open(tmp, 'w', encoding='utf-8').write(full(re.sub(r'src="((?:img|ext)/[^"]+)"', emb, frag)))
    subprocess.run(['node', 'tools/pdf.mjs', tmp, 'agent-portal.pdf'], check=True)
    os.remove(tmp)
    try:  # PDF 정보에 작성자
        import logging; logging.getLogger('pypdf').setLevel(logging.ERROR)  # Chromium PDF 의 trailer 크기 경고
        from pypdf import PdfReader, PdfWriter
        w = PdfWriter(clone_from=PdfReader('agent-portal.pdf'))
        w.add_metadata({'/Title': '에이전트 포털 사용 설명서', '/Author': AUTHOR, '/Subject': COPY})
        w.write('agent-portal.pdf')
    except ImportError: pass
    print('agent-portal.pdf', round(os.path.getsize('agent-portal.pdf') / 1e6, 1), 'MB')
