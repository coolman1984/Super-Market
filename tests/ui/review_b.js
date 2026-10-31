// Review of 1170bf2, cases 1-5, through the real sell screen on a fresh TRAINING server.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const res = {};
  await page.goto(base); await page.reload();
  await page.waitForSelector('.sidebar, input[type=password]');
  if (await page.locator('.side-foot .logout-btn').count()) await page.click('.side-foot .logout-btn');
  await page.waitForSelector('input[type=password]');
  await page.fill('input[autocomplete=username]', 'cashier'); await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]'); await page.waitForSelector('.sidebar');
  await page.evaluate(() => SM.api('GET', '/api/shift').then(s => s && s.shift && !s.shift.closed_at ? null : SM.command('/api/shift/open', SM.newKey(), undefined, { opening_float: 10000 })).catch(() => null));
  await page.goto(base + '/#sell'); await page.waitForSelector('.scan-input');
  const scan = async (code) => { await page.fill('.scan-input', code); await page.press('.scan-input', 'Enter'); await page.waitForTimeout(350); };
  const qtyText = () => page.locator('.cart .qty-cell').first().textContent({ timeout: 1500 }).catch(() => '');
  // case 1: 3 pieces, minus -> 2 pieces, the line stays
  for (let i = 0; i < 3; i++) await scan('6220000000013');
  const before1 = await qtyText();
  await page.locator('.cart .qty-cell button').first().click(); await page.waitForTimeout(200);
  res.case1_lines = await page.locator('.cart tbody tr').count();
  res.case1_qty = (await qtyText()).replace(/\s+/g, ' ').trim();
  res.case1_ok = res.case1_lines === 1 && /2/.test(res.case1_qty) && /3/.test(before1);
  // case 2-3: decimal comma, Arabic comma and Arabic decimal mark
  res.case23 = await page.evaluate(() => ({
    kg_ar_comma: SM.parseKg('٢،٥'), kg_comma: SM.parseKg('2,5'), kg_ar_decimal: SM.parseKg('٢٫٥'), kg_dot: SM.parseKg('2.5'),
    money_comma: SM.parseMoney('12,50'), money_ar: SM.parseMoney('١٢٫٥٠'), money_two_marks: SM.parseMoney('1,000.50'), money_three_dec: SM.parseMoney('1,005'),
    whole: SM.parseMoney('150') }));
  const c = res.case23;
  res.case23_ok = c.kg_ar_comma === 2500 && c.kg_comma === 2500 && c.kg_ar_decimal === 2500 && c.kg_dot === 2500 &&
    c.money_comma === 1250 && c.money_ar === 1250 && c.money_two_marks === null && c.money_three_dec === null && c.whole === 15000;
  // case 4: reload keeps the unpaid cart
  await page.reload(); await page.waitForSelector('.scan-input'); await page.waitForTimeout(1200);
  res.case4_lines_after_reload = await page.locator('.cart tbody tr').count();
  res.case4_qty = (await qtyText()).replace(/\s+/g, ' ').trim();
  res.case4_ok = res.case4_lines_after_reload === 1 && /2/.test(res.case4_qty);
  await page.screenshot({ path: out + '/cart-after-reload-ar.png' });
  // case 5: hold 10 sales; the 11th is refused and nothing old is lost
  const holdBtn = await page.evaluate(() => SM.t('sell.hold'));
  for (let i = 0; i < 10; i++) {
    if (i || !(await page.locator('.cart tbody tr').count())) await scan('6220000000013');
    await page.click('button:has-text("' + holdBtn + '")'); await page.waitForTimeout(150);
  }
  await scan('6220000000013');
  await page.click('button:has-text("' + holdBtn + '")'); await page.waitForTimeout(300);
  res.case5_held = await page.evaluate(() => SM.prefs.get(Object.keys(localStorage).map(k => k.replace(/^sm\./, '')).find(k => k.startsWith('held.')) || 'x', []).length);
  res.case5_cart_kept = await page.locator('.cart tbody tr').count();
  res.case5_toast = (await page.locator('.toast').allTextContents()).pop();
  res.case5_ok = res.case5_held === 10 && res.case5_cart_kept === 1;
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
