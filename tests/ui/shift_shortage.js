/* Owner today shows who is short and by how much. The shifts report shows the
   cashier and the reason. Run: node tests/ui/shift_shortage.js BASE OUT */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
const TEMP = 'temp-pass-9';
const NEXT = 'drawer-pass-9';
const PINS = { owner: '159382', manager: '470296' };

async function login(page, username, password) {
  await page.goto(base + '/');
  await page.reload();
  await page.waitForSelector('.sidebar, .who-list, input[type=password]');
  if (await page.locator('.side-foot .logout-btn').count()) {
    await page.locator('.side-foot .logout-btn').click();
    await page.waitForSelector('.who-list, [data-username="' + username + '"]');
  }
  await page.waitForSelector('[data-username="' + username + '"]');
  await page.click('[data-username="' + username + '"]');
  if (PINS[username] && await page.locator('.pin-pad').count()) {
    for (const d of PINS[username]) await page.click('.pin-key[data-digit="' + d + '"]');
  } else {
    await page.waitForSelector('input[type=password]');
    await page.fill('input[type=password]', password);
    await page.click('#login-go');
  }
  await page.waitForFunction(() => document.querySelector('.sidebar') || document.querySelector('[data-guide="account.new"]'));
  if (await page.locator('[data-guide="account.new"]').count()) {
    await page.fill('[data-guide="account.old"]', TEMP);
    await page.fill('[data-guide="account.new"]', NEXT);
    await page.locator('input[type=password]').nth(2).fill(NEXT);
    await page.click('button[type=submit]');
    await page.waitForSelector('.sidebar');
  }
}

(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await login(page, 'owner', 'training-only');
    await page.evaluate(async (temp) => {
      await SM.api('POST', '/api/users', { username: 'meedo', name: 'ميدو', role: 'cashier', password: temp });
      await SM.api('POST', '/api/users', { username: 'sara', name: 'سارة', role: 'cashier', password: temp });
    }, TEMP);
    await login(page, 'meedo', TEMP);
    await page.evaluate(async () => {
      await SM.command('/api/shift/open', SM.newKey(), undefined, { opening_float: 10000 });
      await SM.command('/api/shift/close', SM.newKey(), undefined, { counted: 5000, note: 'نقص في العد' });
    });
    await login(page, 'sara', TEMP);
    await page.evaluate(async () => {
      await SM.command('/api/shift/open', SM.newKey(), undefined, { opening_float: 2000 });
      await SM.command('/api/shift/close', SM.newKey(), undefined, { counted: 1500, note: 'باقي ناقص' });
    });
    await login(page, 'owner', 'training-only');
    await page.evaluate(() => SM.go('today'));
    await page.waitForFunction(() => {
      const v = document.getElementById('view');
      return v && v.innerText.includes('ميدو: عجز 50.00 ج.م') && v.innerText.includes('سارة: عجز 5.00 ج.م');
    });
    const today = await page.locator('#view').innerText();
    assert.ok(!today.includes('افتح الوردية'), today);
    const doneH = await page.locator('#today-attention .gap-done').first().evaluate(el => el.getBoundingClientRect().height);
    assert.ok(doneH >= 44, 'done button ' + doneH);
    await page.locator('#today-attention').screenshot({ path: out + '/today-shortage-ar.png' });
    await page.screenshot({ path: out + '/today-owner-ar.png', fullPage: true });
    await page.locator('#today-attention a').first().click();
    await page.waitForFunction(() => location.hash === '#reports');
    await page.waitForSelector('#shifts-card');
    await page.waitForFunction(() => document.getElementById('shifts-card').innerText.includes('نقص في العد'));
    const shifts = await page.locator('#shifts-card').innerText();
    assert.ok(shifts.includes('ميدو') && shifts.includes('سارة'), shifts);
    assert.ok(shifts.includes('نقص في العد') && shifts.includes('باقي ناقص'), shifts);
    assert.ok(shifts.includes('-50.00') && shifts.includes('-5.00'), shifts);
    const seen = await page.locator('#shifts-card').evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.top < innerHeight && r.bottom > 0;
    });
    assert.equal(seen, true);
    await page.locator('#shifts-card').screenshot({ path: out + '/shifts-report-ar.png' });
    const desk = await page.locator('#shifts-card').evaluate(el => ({
      w: el.clientWidth, sw: el.scrollWidth,
      table: getComputedStyle(el.querySelector('.table-wrap')).display,
      margin: parseFloat(getComputedStyle(el).scrollMarginTop),
      dir: el.querySelector('bdi') && el.querySelector('bdi').dir,
    }));
    assert.ok(desk.margin >= 44, 'scroll margin ' + desk.margin);
    assert.equal(desk.dir, 'ltr');
    assert.ok(desk.sw <= desk.w + 1, JSON.stringify(desk));
    assert.equal(desk.table, 'block');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => {
      const el = document.getElementById('shifts-card');
      const cards = el.querySelector('.shift-cards');
      const wrap = el.querySelector('.table-wrap');
      return getComputedStyle(wrap).display === 'none' && getComputedStyle(cards).display !== 'none'
        && el.scrollWidth <= el.clientWidth + 1 && cards.innerText.includes('الفرق') && cards.innerText.includes('السبب')
        && cards.innerText.includes('-50.00') && cards.innerText.includes('نقص في العد');
    });
    await page.locator('#shifts-card').screenshot({ path: out + '/shifts-390-ar.png' });
    assert.deepEqual(errors, []);
    console.log('shift shortage: owner today and shifts report passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
