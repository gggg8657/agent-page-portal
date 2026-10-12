// 사용: node tools/page.mjs <html> <outprefix> [width]  → 전체 페이지를 높이 2400px 씩 잘라 저장
import { chromium } from 'playwright'; import path from 'path';
const [f, pre, w='1280'] = process.argv.slice(2);
const b = await chromium.launch(); const p = await b.newPage({ viewport:{width:+w,height:900} });
await p.goto('file://'+path.resolve(f), {waitUntil:'networkidle'}); await p.waitForTimeout(800);
const H = await p.evaluate(()=>document.documentElement.scrollHeight); let i=0;
for (let y=0; y<H; y+=2400, i++) await p.screenshot({ path:`${pre}-${String(i).padStart(2,'0')}.png`, fullPage:true, clip:{x:0,y,width:+w,height:Math.min(2400,H-y)} });
await b.close(); console.log(H, i);
