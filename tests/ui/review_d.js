// Aggressive test 31 Oct 2026 + second model's report: the screen side of every fix, in a real browser.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const res = {};
  const open = async (user, viewport = { width: 1366, height: 860 }) => {
    const page = await (await browser.newContext({ viewport })).newPage();
    page.on('pageerror', e => errors.push(user + ': ' + e.message));
    page.on('dialog', d => { errors.push(user + ': browser dialog ' + d.type()); d.dismiss(); });     // M9: there must be none
    await page.goto(base); await page.waitForSelector('input[autocomplete=username]');
    await page.fill('input[autocomplete=username]', user); await page.fill('input[type=password]', 'training-only');
    await page.click('button[type=submit]'); await page.waitForSelector('.sidebar');
    return page;
  };
  const T = (p, k, v) => p.evaluate(([k, v]) => SM.t(k, v), [k, v]);

  // M2: the counter counts blind; the stock page is not offered to him
  const counter = await open('counter');
  res.M2_counter_has_no_stock_page = await counter.locator('.nav a[data-view=stock]').count() === 0;

  // cashier: no shift -> one big button; unknown barcode -> no "null"; the Arabic hint is whole
  const cash = await open('cashier');
  // another test may have left this cashier's shift open on the shared training data: close it first
  await cash.evaluate(() => SM.api('POST', '/api/shift/close', { key: SM.newKey(), counted: 0, note: 'review_d' }).catch(() => {}));
  await cash.evaluate(() => SM.go('today')); await cash.evaluate(() => SM.go('sell')); await cash.waitForSelector('#open-shift-now');
  res.open_shift_is_a_big_button = (await cash.locator('#open-shift-now').boundingBox()).height >= 40;
  res.arabic_hint_not_in_monospace = await cash.evaluate(() => { const i = document.querySelector('.scan-input'); return !/mono|Consolas|Cascadia/i.test(getComputedStyle(i, '::placeholder').fontFamily) && getComputedStyle(i).direction === 'rtl'; });
  await cash.screenshot({ path: out + '/d-sell-no-shift.png' });
  await cash.click('#open-shift-now'); await cash.waitForTimeout(600);
  await cash.evaluate(async () => { await SM.api('POST', '/api/shift/open', { key: SM.newKey(), opening_float: 0 }).catch(() => {}); });
  await cash.evaluate(() => SM.go('sell')); await cash.waitForSelector('.scan-input');
  await cash.fill('.scan-input', '6229999999995'); await cash.press('.scan-input', 'Enter'); await cash.waitForTimeout(700);
  res.M1_no_null_after_unknown_barcode = !(await cash.locator('.scan-status').innerText()).includes('null');
  // M8: more than can be sold is shown on the line at once. Rice: 30 bags in the training seed; its barcode is scanned 31 times
  const riceCode = await cash.evaluate(() => SM.api('GET', '/api/products?q=1002').then(r => r[0].barcodes ? r[0].barcodes[0].code : null).catch(() => null))
    || await cash.evaluate(() => SM.api('GET', '/api/products/' + 2).then(p => p.barcodes[0].code));
  await cash.fill('.scan-input', riceCode); await cash.press('.scan-input', 'Enter'); await cash.waitForTimeout(500);
  for (let i = 0; i < 30; i++) await cash.locator('[data-guide="sell.more"]').first().click();
  await cash.waitForTimeout(1500);
  res.M8_short_shown_on_the_line = await cash.locator('.cart .badge.bad').count() === 1;
  await cash.screenshot({ path: out + '/d-sell-short.png' });

  // the till's scale: live weight in the weight window, one press takes it (scale answer is simulated)
  await cash.route('**/api/scale/weight', r => r.fulfill({ json: { has_scale: true, connected: true, grams: 1250, stable: true } }));
  await cash.evaluate(() => SM.go('sell')); await cash.waitForSelector('.scan-input');
  await cash.click('[data-guide="sell.search"]'); await cash.waitForTimeout(400);
  const searchBox = cash.locator('.dialog input').first();
  await searchBox.fill(await T(cash, 'x') === 'x' ? 'مفروم' : 'مفروم'); await cash.waitForTimeout(700);
  await cash.locator('.dialog .pick').first().click(); await cash.waitForSelector('#take-weight');
  await cash.waitForTimeout(500);
  res.scale_weight_shows = (await cash.locator('.scale-weight').innerText()).length > 0 && !(await cash.locator('#take-weight').isDisabled());
  await cash.screenshot({ path: out + '/d-sell-scale.png' });
  await cash.click('#take-weight'); await cash.waitForTimeout(400);
  res.scale_weight_taken_into_cart = (await cash.locator('.cart').innerText()).includes('1.250') || (await cash.locator('.cart').innerText()).includes('١٫٢٥');
  await cash.unroute('**/api/scale/weight');

  // owner: settings quick + advanced, the wrong box is pointed at (M5); no browser prompt (M9); scale sticker in 3 steps
  const owner = await open('owner');
  await owner.evaluate(() => SM.go('settings')); await owner.waitForSelector('#shop-settings');
  res.M7_advanced_folded = !(await owner.locator('#shop-advanced').evaluate(d => d.open));
  await owner.locator('#shop-settings input').first().fill('');
  await owner.locator('#shop-settings button.primary').click(); await owner.waitForTimeout(600);
  res.M5_wrong_box_marked = await owner.locator('#shop-settings .invalid').count() === 1;
  await owner.screenshot({ path: out + '/d-settings.png', fullPage: true });
  await owner.evaluate(() => SM.go('catalog')); await owner.waitForTimeout(700);
  await owner.locator('.page table tbody tr, .page .row-click').first().click().catch(() => {});
  await owner.waitForTimeout(500);
  const priceBtn = owner.locator('button:has-text("' + await T(owner, 'cat.change_price') + '")');
  if (await priceBtn.count()) {
    await priceBtn.first().click(); await owner.waitForSelector('#ask-input');
    res.M9_program_window_not_prompt = true;
    await owner.screenshot({ path: out + '/d-ask.png' });
    await owner.fill('#ask-input', '0'); await owner.click('#ask-ok'); await owner.waitForTimeout(300);
    res.M4_zero_price_asks_first = await owner.locator('.dialog h2:has-text("' + await T(owner, 'cat.zero_price_title') + '")').count() === 1;
    await owner.keyboard.press('Escape');
  } else res.M9_program_window_not_prompt = 'no product drawer found';
  await owner.evaluate(() => SM.go('scanner')); await owner.waitForSelector('#scale-sticker');
  await owner.fill('#scale-sticker', '2100123012503'); await owner.fill('#scale-printed', '1.250');
  await owner.click('#scale-detect'); await owner.waitForTimeout(600);
  res.scale_sticker_understood = (await owner.locator('#scale-answer').innerText()).includes('00123');
  await owner.locator('#scale-stickers').screenshot({ path: out + '/d-scale-sticker.png' });
  await owner.locator('#scale-answer button.primary').first().click(); await owner.waitForTimeout(800);
  res.scale_sticker_saved = await owner.locator('#scale-stickers .badge.ok').count() >= 1;

  // L1: an ended session says so on the sign-in screen
  await owner.context().clearCookies();
  await owner.evaluate(() => SM.api('GET', '/api/stock').catch(() => {})); await owner.waitForTimeout(800);
  res.L1_session_ended_said = await owner.locator('#session-ended').count() === 1;

  res.no_page_errors_no_browser_dialogs = errors.length === 0;
  console.log(JSON.stringify(res, null, 1), errors);
  await browser.close();
  if (Object.values(res).some(v => v !== true)) process.exit(1);
})();
