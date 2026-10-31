// Review findings on PR #10 (cart draft), through the real sell screen:
// A reload while the payment request is in flight never sells twice; a failed
// read keeps the saved draft; held sales saved under the old key are not lost.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const res = {};
  await page.goto(base); await page.reload();
  await page.waitForSelector('.sidebar, .who-list');
  if (await page.locator('.side-foot .logout-btn').count()) await page.click('.side-foot .logout-btn');
  await page.waitForSelector('[data-username=manager]');
  await page.click('[data-username=manager]'); await page.fill('input[type=password]', 'training-only');
  await page.click('#login-go'); await page.waitForSelector('.sidebar');
  await page.evaluate(() => SM.api('GET', '/api/shift').then(s => s && s.shift && !s.shift.closed_at ? null : SM.command('/api/shift/open', SM.newKey(), undefined, { opening_float: 10000 })).catch(() => null));
  const sales = () => page.evaluate(() => SM.api('GET', '/api/reports/day').then(r => r.sales_count));
  await page.goto(base + '/#sell'); await page.waitForSelector('.scan-input');
  const scan = async (code) => { await page.fill('.scan-input', code); await page.press('.scan-input', 'Enter'); await page.waitForTimeout(350); };
  // (a) the request leaves, the page reloads before the answer
  await scan('6220000000013');
  const before = await sales();
  await page.route('**/api/sales', async (route) => { await route.fetch(); await new Promise(() => {}); });   // saved on the server, answer never arrives
  await page.keyboard.press('F9'); await page.waitForSelector('.pay-total');
  await page.click('.dialog footer .btn.primary'); await page.waitForTimeout(1200);
  // reload while the answer is still held back; only then let requests through again
  await page.reload(); await page.unroute('**/api/sales'); await page.waitForSelector('.scan-input');
  await page.waitForSelector('.cart tbody tr', { timeout: 8000 }).catch(() => {});   // the draft is read back from the server
  res.a_saved_once = (await sales()) - before;
  const payBtn = page.locator('.pay-panel .btn.primary.lg');
  res.a_pay_blocked = await payBtn.isDisabled();
  res.a_check_shown = await page.locator('.pay-panel .warn-tip button').count() === 1;
  if (res.a_check_shown) { await page.click('.pay-panel .warn-tip button'); await page.waitForTimeout(1200); }
  res.a_still_once = (await sales()) - before;
  res.a_ok = res.a_saved_once === 1 && res.a_pay_blocked && res.a_check_shown && res.a_still_once === 1;
  await page.keyboard.press('Escape').catch(() => {});
  await page.goto(base + '/#sell'); await page.reload(); await page.waitForSelector('.scan-input'); await page.waitForTimeout(800);
  // (b) the product read fails on reload: the draft stays; retry brings it back
  if (!(await page.locator('.cart tbody tr').count())) await scan('6220000000013');
  await scan('6220000000013');
  await page.route('**/api/products/*', route => route.abort('connectionreset'));
  await page.reload(); await page.waitForSelector('.scan-input'); await page.waitForTimeout(1500);
  res.b_banner = await page.locator('.pay-panel .warn-tip').count() === 1;
  res.b_draft_kept = await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('sm.draft.')).map(k => JSON.parse(localStorage.getItem(k))).some(d => d && d.lines && d.lines.length));
  await page.unroute('**/api/products/*');
  const draftLines = await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith('sm.draft.')).map(k => JSON.parse(localStorage.getItem(k))).find(d => d && d.lines).lines.length);
  if (res.b_banner) {   // review on PR #11: two quick clicks on retry must not add the lines twice
    await page.route('**/api/products/*', async route => { await new Promise(r => setTimeout(r, 600)); await route.continue(); });
    await page.locator('.pay-panel .warn-tip button').click(); await page.locator('.pay-panel .warn-tip button').click({ force: true, timeout: 1000 }).catch(() => {});
    await page.waitForTimeout(2500); await page.unroute('**/api/products/*');
  }
  res.b_no_double = await page.locator('.cart tbody tr').count() === draftLines;
  res.b_restored = await page.locator('.cart tbody tr').count() >= 1;
  res.b_ok = res.b_banner && res.b_draft_kept && res.b_restored && res.b_no_double;
  // (c) a held sale saved under the old key (before the shop identity) is still there
  await page.evaluate(() => {
    const old = 'held.' + [SM.state.user.id, SM.workstation(), SM.state.mode].join('|');
    localStorage.setItem('sm.' + old, JSON.stringify([{ at: new Date().toISOString(), lines: [{ id: 1, qty: 1 }], invoiceDiscount: 0 }]));
  });
  await page.reload(); await page.waitForSelector('.scan-input'); await page.waitForTimeout(800);
  res.c_resume_btn = await page.locator('.pay-panel button', { hasText: '(' }).count() >= 1;
  res.c_old_key_gone = await page.evaluate(() => !Object.keys(localStorage).some(k => /^sm\.held\.\d+\|/.test(k)));
  res.c_ok = res.c_resume_btn && res.c_old_key_gone;
  // (d) review on PR #11: after a voluntary password change, scanning works without a reload
  await page.click('.side-foot [aria-label="' + await page.evaluate(() => SM.t('account.change')) + '"]');
  const pw = page.locator('.dialog input[type=password]');
  await pw.nth(0).fill('training-only'); await pw.nth(1).fill('training-new-1'); await pw.nth(2).fill('training-new-1');
  await page.click('.dialog button[type=submit]'); await page.waitForTimeout(1200);
  await page.goto(base + '/#sell'); await page.waitForSelector('.scan-input');
  const rows = await page.locator('.cart tbody tr').count();
  await scan('6220000000020');
  res.d_scan_after_password = (await page.locator('.cart tbody tr').count()) > rows || (await page.textContent('.scan-status')).length > 0;
  res.d_dialog_flag = await page.evaluate(() => SM.state.dialogOpen === false);
  res.d_ok = res.d_scan_after_password && res.d_dialog_flag;
  await page.evaluate(() => SM.api('POST', '/api/account/password', { old: 'training-new-1', new: 'training-only' }).catch(() => null));
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
