// Accounts through the real screens (gap row 5): lock survives a reload, only the same
// password opens it; a new employee must choose his own password; a stopped one is out.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const res = {};
  const login = async (p, user, pass) => {
    await p.goto(base); await p.reload(); await p.waitForSelector('.sidebar, input[type=password]');
    if (await p.locator('.side-foot .logout-btn').count()) await p.locator('.side-foot .logout-btn').click();
    await p.waitForSelector('input[autocomplete=username]');
    await p.fill('input[autocomplete=username]', user); await p.fill('input[type=password]', pass);
    await p.click('button[type=submit]'); await p.waitForTimeout(800);
  };
  await login(page, 'owner', 'training-only');
  const T = (k, v) => page.evaluate(([k, v]) => SM.t(k, v), [k, v]);
  // lock, reload, still locked; wrong password refused; own password opens
  await page.click('.side-foot [aria-label="' + await T('lock.btn') + '"]'); await page.waitForTimeout(500);
  await page.reload(); await page.waitForTimeout(800);
  res.locked_after_reload = await page.locator('.auth-card').count() === 1 && !(await page.locator('.sidebar').count());
  await page.screenshot({ path: out + '/lock-ar.png' });
  await page.fill('input[type=password]', 'wrong-pass'); await page.click('button[type=submit]'); await page.waitForTimeout(600);
  res.wrong_refused = !(await page.locator('.sidebar').count());
  await page.fill('input[type=password]', 'training-only'); await page.click('button[type=submit]'); await page.waitForTimeout(800);
  res.unlocked = await page.locator('.sidebar').count() === 1;
  // new employee from settings must change the password before working
  await page.evaluate(() => SM.api('POST', '/api/users', { username: 'newcash', name: 'كاشير جديد', role: 'cashier', password: 'temp-pass-1' }));
  const p2 = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
  p2.on('pageerror', e => errors.push('p2: ' + e.message));
  await login(p2, 'newcash', 'temp-pass-1');
  res.forced_screen = await p2.locator('.auth-card').count() === 1 && !(await p2.locator('.sidebar').count());
  await p2.screenshot({ path: out + '/forced-password-ar-phone.png' });
  const pw = p2.locator('input[type=password]');
  await pw.nth(0).fill('temp-pass-1'); await pw.nth(1).fill('my-pass-123'); await pw.nth(2).fill('my-pass-123');
  await p2.click('button[type=submit]'); await p2.waitForTimeout(900);
  res.after_change_in = await p2.locator('.sidebar').count() === 1;
  // owner stops him: his open page is out at the next request
  const uid = (await page.evaluate(() => SM.api('GET', '/api/users'))).find(u => u.username === 'newcash').id;
  await page.goto(base + '/#settings'); await page.waitForSelector('#view .tbl');
  await page.locator('#view tr', { hasText: 'newcash' }).locator('button.danger').click();
  await page.fill('#ask-input', 'ترك العمل'); await page.click('#ask-ok'); await page.waitForTimeout(800);   // the program's own question window (review M9)
  await p2.goto(base + '/#sell'); await p2.reload(); await p2.waitForTimeout(900);
  res.stopped_is_out = await p2.locator('input[autocomplete=username]').count() === 1;
  res.users_state = (await page.evaluate(() => SM.api('GET', '/api/users'))).find(u => u.id === uid).active === 0;
  res.ok = ['locked_after_reload', 'wrong_refused', 'unlocked', 'forced_screen', 'after_change_in', 'stopped_is_out', 'users_state'].every(k => res[k] === true);
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
