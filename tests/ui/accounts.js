// Accounts through the real screens (gap row 5): lock survives a reload, only the same
// PIN opens it; a new employee must choose his own password; a stopped one is out.
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
  const PINS = { owner: '159382', manager: '470296', cashier: '3826', butcher: '2964', counter: '4702' };
  const typePin = async (p, pin) => { for (const d of pin) await p.click('.pin-key[data-digit="' + d + '"]'); };
  const login = async (p, user, pass) => {
    await p.goto(base); await p.reload(); await p.waitForSelector('.sidebar, .who-list, input[type=password]');
    if (await p.locator('.side-foot .logout-btn').count()) await p.locator('.side-foot .logout-btn').click();
    await p.waitForSelector('[data-username="' + user + '"]');
    await p.click('[data-username="' + user + '"]');
    if (PINS[user] && await p.locator('.pin-pad').count()) {
      const pin = PINS[user];
      for (const d of pin.slice(0, -1)) await p.click('.pin-key[data-digit="' + d + '"]');
      await Promise.all([
        p.waitForResponse((r) => r.url().includes('/api/login') && r.request().method() === 'POST'),
        p.click('.pin-key[data-digit="' + pin.slice(-1) + '"]'),
      ]);
    } else {
      await p.fill('input[type=password]', pass);
      await Promise.all([
        p.waitForResponse((r) => r.url().includes('/api/login') && r.request().method() === 'POST'),
        p.click('#login-go'),
      ]);
    }
    await p.waitForFunction(() => document.querySelector('.sidebar') || document.querySelector('[data-guide="account.old"]'));
  };
  await login(page, 'owner', 'training-only');
  const T = (k, v) => page.evaluate(([k, v]) => SM.t(k, v), [k, v]);
  // lock, reload, still locked; wrong PIN refused; own PIN opens
  await page.click('.side-foot [aria-label="' + await T('lock.btn') + '"]');
  await page.waitForSelector('.auth-card');
  await page.reload();
  await page.waitForSelector('.auth-card');
  res.locked_after_reload = await page.locator('.auth-card').count() === 1 && !(await page.locator('.sidebar').count());
  await page.screenshot({ path: out + '/lock-ar.png' });
  await typePin(page, '111111');
  await page.waitForFunction(() => {
    const t = document.querySelector('.pin-block .field-error');
    return t && t.textContent.trim().length > 0;
  });
  res.wrong_refused = !(await page.locator('.sidebar').count());
  const ownerPin = '159382';
  for (const d of ownerPin.slice(0, -1)) await page.click('.pin-key[data-digit="' + d + '"]');
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/account/unlock') && r.request().method() === 'POST'),
    page.click('.pin-key[data-digit="' + ownerPin.slice(-1) + '"]'),
  ]);
  await page.waitForSelector('.sidebar');
  res.unlocked = await page.locator('.sidebar').count() === 1;
  // new employee from settings must change the password before working
  await page.evaluate(() => SM.api('POST', '/api/users', { username: 'newcash', name: 'كاشير جديد', role: 'cashier', password: 'temp-pass-1' }));
  const p2 = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
  p2.on('pageerror', e => errors.push('p2: ' + e.message));
  await login(p2, 'newcash', 'temp-pass-1');
  res.forced_screen = await p2.locator('.auth-card').count() === 1 && !(await p2.locator('.sidebar').count());
  await p2.screenshot({ path: out + '/forced-password-ar-phone.png' });
  await p2.waitForSelector('[data-guide="account.old"]');
  await p2.fill('[data-guide="account.old"]', 'temp-pass-1');
  await p2.fill('[data-guide="account.new"]', 'my-pass-123');
  await p2.locator('input[autocomplete="new-password"]').nth(1).fill('my-pass-123');
  const [changed] = await Promise.all([
    p2.waitForResponse((r) => r.url().includes('/api/account/password') && r.request().method() === 'POST'),
    p2.click('button[type=submit]'),
  ]);
  if (!changed.ok()) throw new Error('password change failed ' + changed.status() + ' ' + await changed.text());
  // the shell appears after the server answers; a scanner wait on the same process can hold the lock
  await p2.waitForSelector('.sidebar', { timeout: 60000 });
  res.after_change_in = await p2.locator('.sidebar').count() === 1;
  // owner stops him: his open page is out at the next request
  const uid = (await page.evaluate(() => SM.api('GET', '/api/users'))).find(u => u.username === 'newcash').id;
  await page.goto(base + '/#settings'); await page.waitForSelector('#view .tbl');
  await page.locator('#view tr', { hasText: 'newcash' }).locator('button.danger').click();
  await page.fill('#ask-input', 'ترك العمل');
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/active') && r.request().method() === 'POST'),
    page.click('#ask-ok'),
  ]);
  await p2.goto(base + '/#sell');
  await p2.waitForSelector('.who-list');
  res.stopped_is_out = await p2.locator('.who-list').count() === 1 && await p2.locator('[data-username=newcash]').count() === 0;
  res.users_state = (await page.evaluate(() => SM.api('GET', '/api/users'))).find(u => u.id === uid).active === 0;
  res.ok = ['locked_after_reload', 'wrong_refused', 'unlocked', 'forced_screen', 'after_change_in', 'stopped_is_out', 'users_state'].every(k => res[k] === true);
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
