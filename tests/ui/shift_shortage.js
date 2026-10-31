/* Owner today shows who is short and by how much. The shifts report shows the
   cashier and the reason. Run: node tests/ui/shift_shortage.js BASE OUT */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
const TEMP = 'temp-pass-9';
const NEXT = 'drawer-pass-9';

async function login(page, username, password) {
  await page.goto(base + '/');
  await page.waitForFunction(() => document.querySelector('.logout-btn') || document.querySelector('input[autocomplete=username]'));
  if (await page.locator('.logout-btn').count()) {
    await page.click('.logout-btn');
    await page.waitForSelector('input[autocomplete=username]');
  }
  await page.fill('input[autocomplete=username]', username);
  await page.fill('input[type=password]', password);
  await page.click('button[type=submit]');
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
    await page.locator('#today-attention').screenshot({ path: out + '/today-shortage-ar.png' });
    await page.screenshot({ path: out + '/today-owner-ar.png', fullPage: true });
    await page.evaluate(() => SM.go('reports'));
    await page.waitForSelector('#shifts-card');
    await page.waitForFunction(() => document.getElementById('shifts-card').innerText.includes('نقص في العد'));
    const shifts = await page.locator('#shifts-card').innerText();
    assert.ok(shifts.includes('ميدو') && shifts.includes('سارة'), shifts);
    assert.ok(shifts.includes('نقص في العد') && shifts.includes('باقي ناقص'), shifts);
    assert.ok(shifts.includes('-50.00') && shifts.includes('-5.00'), shifts);
    await page.locator('#shifts-card').scrollIntoViewIfNeeded();
    await page.locator('#shifts-card').screenshot({ path: out + '/shifts-report-ar.png' });
    assert.deepEqual(errors, []);
    console.log('shift shortage: owner today and shifts report passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
