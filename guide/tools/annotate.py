"""캡처 자르기 + 얇은 빨간 상자 (번호 없음).
사용: python3 tools/annotate.py <원본.png> <out.png> <x0,y0,x1,y1> [x,y,w,h ...]
좌표는 모두 CSS px (뷰포트 1440x900, 캡처는 DPR 2). 상자는 Playwright boundingBox 값을 그대로 넣는다."""
import sys
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo
src, out, crop, *boxes = sys.argv[1:]
s = 2; x0, y0, x1, y1 = map(float, crop.split(','))
im = Image.open(src).convert('RGB'); s = im.width / 1440 if im.width in (1440, 2880) else 2
im = im.crop(tuple(int(v * s) for v in (x0, y0, x1, y1))); d = ImageDraw.Draw(im)
for b in boxes:
    x, y, w, h = map(float, b.split(','))
    d.rounded_rectangle(((x-x0)*s-4, (y-y0)*s-4, (x-x0+w)*s+4, (y-y0+h)*s+4), radius=10, outline=(214, 48, 36), width=5)
meta = PngInfo(); meta.add_text('Author', 'DongJu Kim (gggg8657)'); meta.add_text('Copyright', '© 2026 gggg8657')
im.save(out, pnginfo=meta); print(out, im.size)
