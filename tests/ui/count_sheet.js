// ISSUE-009 resume, ISSUE-010 difference list, ISSUE-011 a 10× count is never silent.
// An isolated product avoids depending on stock consumed by earlier journeys.
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
    await page.waitForSelector('[data-username="' + user + '"]');
    await page.click('[data-username="' + user + '"]');
    const pins = { owner: '159382', manager: '470296', counter: '4702' };
    for (const digit of pins[user]) await page.click('.pin-key[data-digit="' + digit + '"]');
    await page.waitForSelector('.sidebar');
  };
  const testCode = 'COUNT-' + Date.now();
  const rice = () => page.locator('.count-row', { hasText: 'أرز اختبار الجرد' }).locator('input');
  const stock = async (code) => page.evaluate(async (code) => {
    const rows = await SM.api('GET', '/api/stock');
    const row = rows.find(r => r.code === code);
    return row ? row.qty : null;
  }, code);

  await page.goto(base);
  await login('manager');
  await page.evaluate(async code => {
    const item = await SM.api('POST', '/api/products', { code, name_ar: 'أرز اختبار الجرد', name_en: 'Count test rice', kind: 'grocery', measure: 'unit', price: 1000 });
    const shelf = (await SM.api('GET', '/api/locations')).find(l => l.kind === 'shelf');
    await SM.command('/api/receipts', SM.newKey(), { lines: [{ product_id: item.id, qty: 30, line_cost: 3000, location_id: shelf.id }] });
  }, testCode);
  const initialRice = await stock(testCode);
  if (initialRice !== 30) throw new Error('isolated count fixture was not received');
  await page.click('[aria-label="تبديل المستخدم"]');
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

  await page.click('[aria-label="تبديل المستخدم"]');
  await login('manager');
  await page.evaluate(() => SM.go('count'));
  await page.waitForSelector('[data-guide="count.approve"]');
  check('approver_sheet_kept', (await rice().inputValue()) === '300');
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-diffs');
  const diffs = await page.locator('#count-diffs').innerText();
  check('lists_the_item', diffs.includes('أرز'));
  check('lists_system_qty', diffs.includes(String(initialRice)));
  check('lists_counted_qty', diffs.includes('300'));
  check('lists_the_difference', diffs.includes(String(300 - initialRice)));
  check('marks_the_big_jump', diffs.includes('فرق كبير'));
  check('explicit_yes', (await page.locator('#count-approve-yes').innerText()) === 'نعم، الرقم صحيح');
  check('recount_button', (await page.locator('#count-approve-no').innerText()) === 'أعد العد');
  await page.waitForFunction(() => document.activeElement && document.activeElement.id === 'count-approve-no');
  check('recount_is_focused', true);
  await shot('approver');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(300);
  check('escape_does_not_post', (await stock(testCode)) === initialRice);
  check('escape_closes', await page.locator('#count-diffs').count() === 0);
  await page.click('[data-guide="count.approve"]');
  await page.waitForSelector('#count-approve-yes');
  await page.click('#count-approve-yes');
  await page.waitForSelector('.toast');
  check('approved_stock_is_300', (await stock(testCode)) === initialRice0);

  await browser.close();
  console.log(JSON.stringify(res, null, 2));
  if (errors.length) { console.error(errors.join('\n')); process.exit(1); }
})().catch(e => { console.error(e); process.exit(1); });
