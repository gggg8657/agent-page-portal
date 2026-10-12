// 사용: node tools/preview.mjs parts/<file>.html → shots/_preview-<file>.png
import { chromium } from 'playwright'; import fs from 'fs'; import path from 'path'; import { fileURLToPath } from 'url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const part = path.resolve(root, process.argv[2]);
fs.writeFileSync(path.join(root,'_preview.html'), `<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap"><link rel="stylesheet" href="style.css"><body><div class="wrap">${fs.readFileSync(part,'utf8')}</div>`);
const b = await chromium.launch(); const p = await b.newPage({ viewport:{width:1280,height:900} });
await p.goto('file://'+path.join(root,'_preview.html'), {waitUntil:'networkidle'}); await p.waitForTimeout(500);
const out = path.join(root,'shots','_preview-'+path.basename(part,'.html')+'.png');
await p.screenshot({ path: out, fullPage: true }); console.log(out, await p.evaluate(()=>document.documentElement.scrollWidth));
await b.close();
