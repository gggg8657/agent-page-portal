// 펫(pet.js) 막고 캡처하는 예. 상호작용이 필요하면 이 파일을 복사해 쓰세요.
// 사용: node tools/shot.mjs <url> <out.png>
import { chromium } from 'playwright';
const [url, out] = process.argv.slice(2);
const b = await chromium.launch();
const ctx = await b.newContext({ viewport:{width:1440,height:900}, deviceScaleFactor:2 });
await ctx.route('**/padong-local/static/pet.js', r => r.abort());   // 파동이 펫 오버레이 제거 (필수)
const p = await ctx.newPage();
await p.goto(url, { waitUntil:'domcontentloaded' }); await p.waitForTimeout(1500);
await p.keyboard.press('Escape').catch(()=>{});                     // 처음 뜨는 사용법 팝업 닫기
await p.screenshot({ path: out }); await b.close();
