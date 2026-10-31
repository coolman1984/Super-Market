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
  const login = async (user) => {
    await page.waitForSelector('input[autocomplete=username]');
    await page.fill('input[autocomplete=username]', user);
    await page.fill('input[type=password]', 'training-only');
    await page.click('button[type=submit]');
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
  await rice().fill('300');
  await rice().blur();
  await page.waitForSelector('#confirm-no');
  check('far_title', (await page.locator('#ask-layer h2').innerText()) === 'الرقم بعيد');
  check('far_asks_to_recount', (await page.locator('#ask-layer .body').innerText()).includes('أعد العد'));
  check('recount_is_focused', await page.evaluate(() => document.activeElement && document.activeElement.id === 'confirm-no'));
  await page.screenshot({ path: out + '/count-far-warning.png' });
  await page.click('#confirm-no');
  await page.waitForTimeout(200);
  check('recount_clears_the_number', (await rice().inputValue()) === '');
  check('recount_does_not_save', await page.evaluate(async () => {
    const open = (await SM.api('GET', '/api/counts')).find(r => r.status === 'open');
    if (!open) return false;
    const sheet = await SM.api('GET', '/api/counts/' + open.id);
    return sheet.lots.every(l => l.counted === null);
  }));

  await rice().fill('300');
  await rice().blur();
  await page.waitForSelector('#confirm-yes');
  await page.click('#confirm-yes');
  await page.waitForSelector('.count-row .badge.ok');
  check('keep_saves_300', (await rice().inputValue()) === '300');

  await page.evaluate(() => SM.go('today'));
  await page.waitForSelector('.page h1');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('.count-row input');
  check('leave_returns_to_the_sheet', (await rice().inputValue()) === '300');
  const back = page.locator('button', { hasText: 'القائمة' });
  await back.click();
  await page.waitForSelector('.count-resume');
  check('row_is_marked', (await page.locator('.count-resume').innerText()) === 'أكمل الجرد');
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

  await page.click('[aria-label="تبديل المستخدم"]');
  await login('manager');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('[data-guide="count.approve"]');
  check('approver_sheet_kept', (await rice().inputValue()) === '300');
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-diffs');
  const diffs = await page.locator('#count-diffs').innerText();
  check('lists_the_item', diffs.includes('أرز'));
  check('lists_system_qty', diffs.includes('30'));
  check('lists_counted_qty', diffs.includes('300'));
  check('lists_the_difference', diffs.includes('270'));
  check('marks_the_big_jump', diffs.includes('فرق كبير'));
  check('explicit_yes', (await page.locator('#count-approve-yes').innerText()) === 'نعم، الرقم صحيح');
  await page.waitForFunction(() => document.activeElement && document.activeElement.id === 'count-approve-no');
  check('cancel_is_focused', true);
  await page.screenshot({ path: out + '/count-approve-diffs.png' });
  await page.click('#count-approve-no');
  await page.waitForTimeout(300);
  check('cancel_leaves_stock', (await stock('1002')) === 30);
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-approve-yes');
  await page.click('#count-approve-yes');
  await page.waitForSelector('.toast');
  check('approved_stock_is_300', (await stock('1002')) === 300);

  await browser.close();
  console.log(JSON.stringify(res, null, 2));
  if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
