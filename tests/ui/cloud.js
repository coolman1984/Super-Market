// Cloud copy card (owner order 31 Oct 2026): the owner sees when the last copy went up,
// what waits on this PC and a plain warning; the cashier does not get the status at all.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1366, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const login = async (p, user) => {
    await p.goto(base); await p.waitForSelector('[data-username="' + user + '"]');
    await p.click('[data-username="' + user + '"]');
    const pins = { owner: '159382', manager: '470296', cashier: '3826', butcher: '2964', counter: '4702' };
    for (const d of pins[user]) await p.click('.pin-key[data-digit="' + d + '"]');
    await p.waitForSelector('.sidebar');
  };
  await login(page, 'owner');
  const T = (k, v) => page.evaluate(([k, v]) => SM.t(k, v), [k, v]);
  const res = {};
  await page.evaluate(() => SM.go('settings')); await page.waitForSelector('#cloud-card');
  res.training_says_off = (await page.locator('#cloud-card').innerText()).includes(await T('set.cloud_off'));
  res.training_has_no_button = await page.locator('#cloud-now').count() === 0;
  // the real shop: the same card with a waiting copy and no internet
  await page.route('**/api/cloud', r => r.fulfill({ json: { enabled: true, connected: true, queued: 1, last_ok_at: '2026-10-31T13:00:00+03:00', error: 'offline', pc: 'abc123' } }));
  let sent = 0;
  await page.route('**/api/cloud/now', r => { sent++; r.fulfill({ json: { started: true } }); });
  await page.evaluate(() => SM.go('home')); await page.evaluate(() => SM.go('settings')); await page.waitForSelector('#cloud-now');
  const text = await page.locator('#cloud-card').innerText();
  res.shows_last_ok = text.includes('2026-10-31 13:00');
  res.shows_waiting = text.includes(await T('set.cloud_wait', { n: 1 }));
  res.shows_plain_warning = text.includes(await T('cloud.offline'));
  res.no_secret_or_address = !/workers\.dev|secret|SMC1-/i.test(text);
  await page.locator('#cloud-card').screenshot({ path: out + '/cloud-card-ar.png' });
  await page.click('#cloud-now'); await page.waitForTimeout(300);
  res.send_now_called = sent === 1;
  // first minutes of a new install: it connects by itself, nothing to press, nothing technical
  await page.unroute('**/api/cloud');
  await page.route('**/api/cloud', r => r.fulfill({ json: { enabled: false, enrolling: true, available: true, error: 'offline' } }));
  await page.evaluate(() => SM.go('home')); await page.evaluate(() => SM.go('settings')); await page.waitForSelector('#cloud-card');
  const enr = await page.locator('#cloud-card').innerText();
  res.enrolling_says_it_is_automatic = enr.includes(await T('set.cloud_enrolling'));
  res.enrolling_has_no_button = await page.locator('#cloud-now').count() === 0 && await page.locator('#cloud-recovery').count() === 0;
  // connected: the owner can read his recovery code (and only that)
  await page.unroute('**/api/cloud');
  await page.route('**/api/cloud', r => r.fulfill({ json: { enabled: true, connected: true, queued: 0, last_ok_at: '2026-10-31T13:00:00', error: null, recovery_available: true } }));
  await page.route('**/api/cloud/recovery', r => r.fulfill({ json: { code: 'ABCDE-FGHJK-LMNPQ-23456' } }));
  await page.evaluate(() => SM.go('home')); await page.evaluate(() => SM.go('settings')); await page.waitForSelector('#cloud-recovery');
  res.recovery_hidden_until_asked = !(await page.locator('#cloud-card').innerText()).includes('ABCDE');
  await page.click('#cloud-recovery'); await page.waitForTimeout(300);
  res.recovery_shown = (await page.locator('#cloud-recovery-code').innerText()).includes('ABCDE-FGHJK-LMNPQ-23456');
  await page.locator('#cloud-card').screenshot({ path: out + '/cloud-card-recovery-ar.png' });
  await page.unroute('**/api/cloud/recovery');
  await page.unroute('**/api/cloud'); await page.unroute('**/api/cloud/now');
  // a cashier cannot read the cloud status from the server
  const p2 = await (await browser.newContext()).newPage();
  await login(p2, 'cashier');
  res.cashier_refused = await p2.evaluate(() => SM.api('GET', '/api/cloud').then(() => false, e => true));
  res.no_page_errors = errors.length === 0;
  console.log(JSON.stringify(res, null, 1), errors);
  await browser.close();
  if (Object.values(res).some(v => v !== true)) process.exit(1);
})();
