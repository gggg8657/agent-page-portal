// 화면 비교: 같은 화면을 지금 포털(:8700)과 새 포털(:8720)에서 열어 캡처하고, 새 포털의 콘솔 오류를 센다.
//   PW=<playwright 모듈 경로> DEMO=<id>:<pin> HOSTADDR=<이 서버의 바깥 주소> OUT=<캡처 폴더> node fastportal/tests/ui_compare.js
// 테마: classic-light · v2-dark. 로그인 화면은 손님(바깥 주소), 나머지는 데모 계정으로 화면에서 로그인.
// 데모 계정의 테마는 바꿨다가 끝에 원래 값으로 되돌린다(같은 DB 라 한 포털에서만).
const { chromium } = require(process.env.PW || 'playwright');
const fs = require('fs');
const [ID, PIN] = (process.env.DEMO || '').split(':');
const HOST = process.env.HOSTADDR, OUT = process.env.OUT || '/tmp/ui_compare';
const PORTS = { old: +(process.env.OLD || 8700), new: +(process.env.NEW || 8720) };
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const errors = { old: [], new: [] }, closes = { old: 0, new: 0 };
  const shots = [];
  let orig = null;
  for (const theme of ['classic-light', 'v2-dark']) {
    for (const side of ['old', 'new']) {
      const base = `http://${HOST}:${PORTS[side]}`;
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1 });
      await ctx.addCookies([{ name: 'agtheme', value: theme, url: base }]);
      const page = await ctx.newPage();
      page.on('console', m => { if (process.env.VERBOSE) console.log(side, theme, m.type(), m.text().slice(0,150)); if (m.type() === 'error') errors[side].push(`${theme} ${page.url()} ${m.text()}`); });
      page.on('pageerror', e => errors[side].push(`${theme} ${page.url()} pageerror ${e.message}`));
      page.on('crash', () => console.log('CRASH', side, theme, page.url())); page.on('close', () => console.log('CLOSE', side, theme));
      const snap = async (name) => { console.log('snap', side, theme, name);
        await page.waitForLoadState('networkidle').catch(() => {});
        await page.waitForTimeout(800);
        await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}' }).catch(() => {});
        const f = `${OUT}/${theme}-${name}-${side}.png`;
        await page.screenshot({ path: f });
        shots.push({ theme, name, side, f });
      };
      await page.goto(`${base}/login`); await snap('landing');
      await page.fill('#in input[name=id]', ID); await page.fill('#in input[name=pin]', PIN);
      await Promise.all([page.waitForURL(u => !String(u).includes('/login'), { timeout: 15000 }), page.click('#in button.lp-btn')]);
      if (orig === null) orig = (await (await page.request.get(`${base}/api/account/theme`)).json()).theme;
      await page.request.post(`${base}/api/account/theme`, { data: { theme } });
      await page.request.post(`${base}/api/account/onboarded`, { data: { done: true } });
      for (const [name, path] of [['home', '/'], ['account', '/account'], ['tool', '/t/kordoc-local/']]) {
        for (let tryN = 0; tryN < 3; tryN++) {   // 드물게 헤드리스 페이지가 닫힘(두 포털 모두에서 본 현상) — 새 탭으로 다시
          try { if (page.isClosed()) { page = await ctx.newPage(); closes[side]++; } await page.goto(base + path); await snap(name); break; }
          catch (e) { console.log('retry', side, theme, name, e.message.split('\n')[0]); }
        }
      }
      await page.goto(`${base}/logout`);
      await ctx.close();
    }
  }
  // 테마 되돌리기
  const ctx = await browser.newContext(); const page = await ctx.newPage();
  await page.goto(`http://${HOST}:${PORTS.new}/login`);
  const r = await page.request.post(`http://${HOST}:${PORTS.new}/api/login`, { data: { id: ID, pin: PIN, remember: false } });
  await page.request.post(`http://${HOST}:${PORTS.new}/api/account/theme`, { data: { theme: orig } });
  await page.goto(`http://${HOST}:${PORTS.new}/logout`);
  await browser.close();
  fs.writeFileSync(`${OUT}/result.json`, JSON.stringify({ shots, errors, restored: orig, login: r.status(), closes }, null, 1));
  console.log(JSON.stringify({ closes, n: shots.length, errors_old: errors.old.length, errors_new: errors.new.length, restored: orig }));
  for (const e of errors.new) console.log('new console error:', e);
  for (const e of errors.old) console.log('old console error:', e);
})();
