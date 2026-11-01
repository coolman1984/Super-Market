// ISSUE-009 resume, ISSUE-010 difference list, ISSUE-011 a 10× count is never silent.
// Training shop: rice (1002) sits at 30 on the grocery shelf. Counting 300 is the extra zero.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const res = {};
  const page = await (await browser.newContext({ viewport: { width: 1366, height: 768 }, locale: 'ar-EG' })).newPage();
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('dialog', d => { errors.push('browser dialog ' + d.type()); d.dismiss(); });
  const check = (name, ok) => { res[name] = !!ok; if (!ok) errors.push(name); };
  const pins = { counter: '4702', manager: '470296' };
  const login = async (user) => {
    await page.waitForSelector('.sidebar, .who-list, input[type=password]');
    if (await page.locator('.side-foot .logout-btn').count()) {
      await page.locator('.side-foot .logout-btn').click();
      await page.waitForSelector('.who-list');
    }
    await page.waitForSelector('[data-username="' + user + '"]');
    await page.click('[data-username="' + user + '"]');
    if (pins[user] && await page.locator('.pin-pad').count()) {
      for (const d of pins[user]) await page.click('.pin-key[data-digit="' + d + '"]');
    } else {
      await page.waitForSelector('input[type=password]');
      await page.fill('input[type=password]', 'training-only');
      await page.click('#login-go');
    }
    await page.waitForSelector('.sidebar');
  };
  const rice = () => page.locator('.count-row', { hasText: 'أرز' }).locator('input');
  const stock = async (code) => page.evaluate(async (code) => {
    const rows = await SM.api('GET', '/api/stock');
    const row = rows.find(r => r.code === code);
    return row ? row.qty : null;
  }, code);

  await page.goto(base);
  await login('counter');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('[data-guide="count.open"]');
  await page.click('[data-guide="count.open"]');
  await page.waitForSelector('.count-row input');
  check('sheet_hides_balance', !(await page.locator('.count-row').first().innerText()).includes('الرصيد'));
  const shot = async (name) => {
    await page.setViewportSize({ width: 1366, height: 768 });
    await page.waitForTimeout(150);
    await page.screenshot({ path: out + '/' + name + '-1366.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(150);
    await page.screenshot({ path: out + '/' + name + '-390.png' });
    await page.setViewportSize({ width: 1366, height: 768 });
  };
  await rice().fill('300');
  await rice().blur();
  await page.waitForSelector('.count-row .badge.ok');
  check('no_far_dialog', await page.locator('#ask-layer.on, #confirm-yes').count() === 0);
  check('counter_has_no_cancel', await page.locator('button', { hasText: 'إلغاء الجرد' }).count() === 0);
  check('saved_without_asking', (await rice().inputValue()) === '300');
  await shot('counter');

  await page.evaluate(() => SM.go('today'));
  await page.waitForSelector('.page h1');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('.count-row input');
  check('leave_returns_to_the_sheet', (await rice().inputValue()) === '300');
  const back = page.locator('button', { hasText: 'القائمة' });
  await back.click();
  await page.waitForSelector('.count-resume');
  check('row_is_marked', (await page.locator('.count-resume').innerText()).includes('افتح ورقة'));
  check('open_row_marked', await page.locator('tr.count-open').count() > 0);
  await page.screenshot({ path: out + '/count-resume-list.png' });
  await page.click('[data-guide="count.open"]');
  await page.waitForSelector('.count-row input');
  check('start_resumes', (await rice().inputValue()) === '300');
  check('start_does_not_error', await page.locator('.toast.bad').count() === 0);

  await page.reload();
  await page.waitForSelector('.count-row input');
  check('refresh_keeps_the_sheet', (await rice().inputValue()) === '300');
  await page.screenshot({ path: out + '/count-sheet-after-refresh.png' });

  await login('manager');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('[data-guide="count.approve"]');
  check('approver_sheet_kept', (await rice().inputValue()) === '300');
  const before = await page.evaluate(async () => {
    const open = (await SM.api('GET', '/api/counts')).find(r => r.status === 'open');
    const review = await SM.api('GET', '/api/counts/' + open.id + '/review');
    const line = review.lines.find(l => (l.name_ar || '').includes('أرز') && l.counted === 300);
    const rows = await SM.api('GET', '/api/stock');
    const row = line && rows.find(r => r.lot_id === line.lot_id);
    return { line, qty: row ? row.qty : null };
  });
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-diffs');
  const diffs = await page.locator('#count-diffs').innerText();
  const shows = (n) => new RegExp('(^|\\D)' + n + '(\\D|$)').test(diffs);
  check('lists_the_item', diffs.includes('أرز'));
  const systemOk = !!(before.line && shows(before.line.system));
  const diffOk = !!(before.line && shows(Math.abs(before.line.diff)));
  check('lists_system_qty', systemOk);
  check('lists_counted_qty', shows(300));
  check('lists_the_difference', diffOk);
  if (!systemOk || !diffOk) errors.push(JSON.stringify({ diffs, line: before.line }));
  check('marks_the_big_jump', diffs.includes('فرق كبير'));
  check('explicit_yes', (await page.locator('#count-approve-yes').innerText()) === 'نعم، الرقم صحيح');
  check('recount_button', (await page.locator('#count-approve-no').innerText()) === 'أعد العد');
  await page.waitForFunction(() => document.activeElement && document.activeElement.id === 'count-approve-no');
  check('recount_is_focused', true);
  await shot('approver');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  const qtyAfterEscape = await page.evaluate(async (lotId) => {
    if (!lotId) return null;
    const rows = await SM.api('GET', '/api/stock');
    const row = rows.find(r => r.lot_id === lotId);
    return row ? row.qty : 0;
  }, before.line && before.line.lot_id);
  check('escape_does_not_post', before.line && qtyAfterEscape === (before.qty === null ? 0 : before.qty));
  check('escape_closes', await page.locator('#count-diffs').count() === 0);
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-approve-yes');
  await page.click('#count-approve-yes');
  await page.waitForSelector('.toast');
  check('approved_stock_is_300', (await stock('1002')) === 300);

  await browser.close();
  console.log(JSON.stringify(res, null, 2));
  if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
