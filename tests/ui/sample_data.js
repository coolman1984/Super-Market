// Sample data: one button fills an EMPTY shop with a month of trading, a bar on every screen says so,
// and one button (with the owner's password) removes everything again. Usage: sample_data.js <empty shop url> [shots folder]
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8798';
const shots = process.argv[3] || '/tmp';
(async () => {
  const browser = await chromium.launch();
  const res = {}, errors = [];
  const page = await (await browser.newContext({ viewport: { width: 1366, height: 900 } })).newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base); await page.waitForSelector('.auth-card');
  const pw = page.locator('input[type=password]');
  await page.locator('.auth-card input.input').nth(0).fill('سوبر ماركت النور');
  await page.locator('.auth-card input.input').nth(1).fill('المالك');
  await page.locator('.auth-card input[autocomplete=username]').fill('boss');
  await pw.nth(0).fill('first-pass-123'); await pw.nth(1).fill('first-pass-123');
  await page.click('button[type=submit]'); await page.waitForSelector('#login-forgot');
  await page.evaluate(() => SM.api('POST', '/api/login', { username: 'boss', password: 'first-pass-123', workstation: 'till-1' }));
  await page.goto(base + '/#settings'); await page.reload(); await page.waitForSelector('#sample-card');
  res.card_offers_load = await page.locator('#sample-load').count() === 1 && await page.locator('#sample-clear').count() === 0;
  res.no_bar_before = await page.locator('#sample-bar').count() === 0;
  await page.screenshot({ path: shots + '/sample-before.png', fullPage: true });
  // load: one click, then the home screen shows a month of sales
  await page.click('#sample-load');
  await page.waitForSelector('#sample-bar', { timeout: 120000 });
  res.bar_after_load = await page.locator('#sample-bar').isVisible();
  const st = await page.evaluate(() => SM.api('GET', '/api/sample'));
  res.status_loaded = st.loaded && st.complete && st.mine.sales > 1000;
  const products = await page.evaluate(() => SM.api('GET', '/api/products?limit=500'));
  res.products = products.length > 40;
  await page.waitForFunction(() => location.hash === '#today' && document.querySelector('#view .page'), null, { timeout: 20000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: shots + '/sample-today.png', fullPage: true });
  for (const v of ['today', 'sell', 'returns', 'cash', 'stock', 'prepare', 'count', 'reports', 'parties', 'catalog', 'settings', 'help']) {
    await page.goto(base + '/#' + v); await page.waitForSelector('#view .page', { timeout: 20000 }); await page.waitForTimeout(600);
    await page.screenshot({ path: shots + '/sample-' + v + '.png', fullPage: true });
  }
  // the card now offers removal only
  await page.goto(base + '/#settings'); await page.waitForSelector('#sample-clear');
  res.card_offers_clear = await page.locator('#sample-load').count() === 0;
  await page.screenshot({ path: shots + '/sample-settings-on.png', fullPage: true });
  // a wrong password removes nothing
  await page.click('#sample-clear'); await page.waitForSelector('#sample-pw');
  await page.fill('#sample-pw', 'not-my-password'); await page.click('#sample-clear-go');
  await page.waitForTimeout(800);
  res.wrong_password_keeps = (await page.evaluate(() => SM.api('GET', '/api/sample'))).loaded === true;
  await page.screenshot({ path: shots + '/sample-clear-dialog.png' });
  await page.fill('#sample-pw', 'first-pass-123'); await page.click('#sample-clear-go');
  await page.waitForFunction(() => !document.getElementById('sample-bar'), null, { timeout: 60000 });
  const after = await page.evaluate(async () => ({ st: await SM.api('GET', '/api/sample'), products: await SM.api('GET', '/api/products?limit=500') }));
  res.cleared = !after.st.loaded && after.st.can_load && after.products.length === 0;
  const re = await page.evaluate(() => SM.api('GET', '/api/state'));
  res.owner_still_there = !!re.user && re.user.role === 'owner';
  await browser.close();
  const bad = Object.entries(res).filter(([, v]) => v !== true);
  console.log(JSON.stringify(res, null, 1), errors.length ? 'PAGE ERRORS: ' + errors.join(' | ') : '');
  process.exit(bad.length || errors.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
