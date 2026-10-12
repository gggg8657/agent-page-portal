// 사용: node tools/pdf.mjs <html> <out.pdf>  — A4, 배경 포함
import { chromium } from 'playwright'; import path from 'path';
const [f, out] = process.argv.slice(2);
const b = await chromium.launch(); const p = await b.newPage();
await p.goto('file://' + path.resolve(f), { waitUntil: 'networkidle' }); await p.waitForTimeout(800);
await p.emulateMedia({ media: 'print' });
await p.pdf({ path: out, format: 'A4', printBackground: true, scale: +(process.env.SCALE||1), margin: { top: '16mm', bottom: '16mm', left: '18mm', right: '18mm' },
  displayHeaderFooter: true, headerTemplate: '<span></span>',
  footerTemplate: '<div style="width:100%;margin:0 18mm;font:8px sans-serif;color:#9aa1ab;display:flex;justify-content:space-between"><span>내부 시험용 · 배포용 아님 · © 2026 DongJu Kim (gggg8657)</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>' });
await b.close(); console.log(out);
