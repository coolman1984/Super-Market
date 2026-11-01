// Aggressive test 31 Oct 2026 + second model's report: the screen side of every fix, in a real browser.
const { chromium } = require('playwright');
const { scanCode } = require('./wait');
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
    await page.goto(base); await page.waitForSelector('[data-username="' + user + '"]');
    const pins = { owner: '159382', manager: '470296', cashier: '3826', butcher: '2964', counter: '4702' };
    await page.click('[data-username="' + user + '"]');
    for (const d of pins[user]) await page.click('.pin-key[data-digit="' + d + '"]');
    await page.waitForSelector('.sidebar');
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
  await cash.click('#open-shift-now');
  await cash.waitForSelector('.input.big');
  await cash.evaluate(async () => { await SM.api('POST', '/api/shift/open', { key: SM.newKey(), opening_float: 0 }).catch(() => {}); });
  await cash.evaluate(() => SM.go('sell')); await cash.waitForSelector('.scan-input');
  await scanCode(cash, '6229999999995');
  res.M1_no_null_after_unknown_barcode = !(await cash.locator('.scan-status').innerText()).includes('null');
  // M8: more than can be sold is shown on the line at once. Rice: 30 bags in the training seed; its barcode is scanned 31 times
  const riceCode = await cash.evaluate(() => SM.api('GET', '/api/products?q=1002').then(r => r[0].barcodes ? r[0].barcodes[0].code : null).catch(() => null))
    || await cash.evaluate(() => SM.api('GET', '/api/products/' + 2).then(p => p.barcodes[0].code));
  const rice = await cash.evaluate(() => SM.api('GET', '/api/products?q=1002').then((r) => r[0]));
  await scanCode(cash, riceCode);
  for (let i = 1; i < rice.available + 3; i++) await cash.locator('[data-guide="sell.more"]').first().click();
  const shown = (await cash.locator('.cart .qty-cell').first().innerText()).replace(/[^\d]/g, '');
  res.M8_refused_when_added = shown === String(rice.available);
  res.M8_message = (await cash.locator('.toast').last().innerText()).includes('لم يُضف');
  await cash.evaluate(async () => {
    const p = (await SM.api('GET', '/api/products?q=1002'))[0];
    const payload = { lines: [{ product_id: p.id, qty: p.available }] };
    const quote = await SM.api('POST', '/api/quote', { payload });
    payload.cash_tendered = quote.total;
    payload.expected_total = quote.total;
    payload.quote_hash = quote.quote_hash;
    await SM.command('/api/sales', SM.newKey(), payload);
  });
  await cash.locator('.cart .qty-cell button').first().click();
  await cash.waitForSelector('.cart .badge.bad');
  res.M8_short_shown_on_the_line = await cash.locator('.cart .badge.bad').count() === 1;
  res.M8_pay_disabled_while_over = await cash.locator('[data-guide="sell.pay"]').isDisabled();
  await cash.screenshot({ path: out + '/d-sell-short.png' });

  // the till's scale: live weight in the weight window, one press takes it (scale answer is simulated)
  await cash.route('**/api/scale/weight', r => r.fulfill({ json: { has_scale: true, connected: true, grams: 1250, stable: true } }));
  await cash.evaluate(() => SM.go('sell')); await cash.waitForSelector('.scan-input');
  await cash.click('[data-guide="sell.search"]');
  await cash.waitForSelector('.dialog input');
  const searchBox = cash.locator('.dialog input').first();
  await searchBox.fill('مفروم');
  await cash.waitForSelector('.dialog .pick');
  await cash.locator('.dialog .pick').first().click(); await cash.waitForSelector('#take-weight');
  await cash.waitForFunction(() => {
    const live = document.querySelector('.scale-weight');
    const take = document.querySelector('#take-weight');
    return live && live.textContent.trim().length > 0 && take && !take.disabled;
  });
  res.scale_weight_shows = true;
  await cash.screenshot({ path: out + '/d-sell-scale.png' });
  await cash.click('#take-weight');
  await cash.waitForFunction(() => /1\.250|١/.test((document.querySelector('.cart') || {}).innerText || ''));
  res.scale_weight_taken_into_cart = true;
  await cash.unroute('**/api/scale/weight');

  // owner: settings quick + advanced, the wrong box is pointed at (M5); no browser prompt (M9); scale sticker in 3 steps
  const owner = await open('owner');
  await owner.evaluate(() => SM.go('settings')); await owner.waitForSelector('#shop-settings');
  res.M7_advanced_folded = !(await owner.locator('#shop-advanced').evaluate(d => d.open));
  await owner.locator('#shop-settings input').first().fill('');
  await owner.locator('#shop-settings button.primary').click();
  await owner.waitForSelector('#shop-settings .invalid');
  res.M5_wrong_box_marked = await owner.locator('#shop-settings .invalid').count() === 1;
  await owner.screenshot({ path: out + '/d-settings.png', fullPage: true });
  await owner.evaluate(() => SM.go('catalog'));
  await owner.waitForSelector('#view .tbl tbody tr, #view .empty');
  await owner.locator('#view tbody tr').first().click();
  await owner.waitForSelector('.drawer');
  const priceBtn = owner.locator('button:has-text("' + await T(owner, 'cat.change_price') + '")');
  if (await priceBtn.count()) {
    await priceBtn.first().click(); await owner.waitForSelector('#ask-input');
    res.M9_program_window_not_prompt = true;
    await owner.screenshot({ path: out + '/d-ask.png' });
    await owner.fill('#ask-input', '0');
    await owner.click('#ask-ok');
    await owner.waitForSelector('#confirm-yes');
    res.M4_zero_price_asks_first = (await owner.locator('#ask-layer h2').innerText()) === await T(owner, 'cat.zero_price_title');
    await owner.keyboard.press('Escape');
    await owner.waitForSelector('#confirm-yes', { state: 'detached' });
  } else res.M9_program_window_not_prompt = 'no product drawer found';
  // review PR #18: cancelling the zero-price question keeps the new-item form and what was typed
  await owner.evaluate(() => { const p = document.getElementById('panels'); if (p) SM.put(p); });
  await owner.waitForFunction(() => !document.querySelector('.drawer-scrim'));
  await owner.locator('[data-guide="cat.add"]').click({ force: true });
  await owner.waitForSelector('.dialog [data-guide="cat.name_ar"]');
  await owner.fill('.dialog [data-guide="cat.name_ar"]', 'صنف بسعر صفر'); await owner.fill('.dialog [data-guide="cat.price"]', '0');
  await owner.click('.dialog footer .btn.primary');
  await owner.waitForSelector('#confirm-yes');
  await owner.keyboard.press('Escape');
  await owner.waitForSelector('#confirm-yes', { state: 'detached' });
  res.zero_price_cancel_keeps_the_form = await owner.locator('.dialog [data-guide="cat.name_ar"]').inputValue().catch(() => '') === 'صنف بسعر صفر';
  await owner.keyboard.press('Escape');
  await owner.waitForSelector('.dialog', { state: 'detached' });
  await owner.evaluate(() => SM.go('scanner')); await owner.waitForSelector('#scale-sticker');
  await owner.fill('#scale-sticker', '2100123012503'); await owner.fill('#scale-printed', '1.250');
  await owner.click('#scale-detect');
  await owner.waitForSelector('#scale-answer button, #scale-answer .badge');
  res.scale_sticker_understood = (await owner.locator('#scale-answer').innerText()).includes('00123');
  await owner.locator('#scale-stickers').screenshot({ path: out + '/d-scale-sticker.png' });
  await owner.locator('#scale-answer button.primary').first().click();
  await owner.waitForSelector('#scale-stickers .badge.ok');
  res.scale_sticker_saved = await owner.locator('#scale-stickers .badge.ok').count() >= 1;
  // review PR #18: a scale on a second till gets its own id and keeps the first till's scale
  await owner.evaluate(() => SM.api('POST', '/api/scanner/devices', { devices: [{ id: 'scale1', type: 'serial', role: 'scale', port: 'COM7', poll: 'none', workstation: 'other-till' }] }));
  await owner.evaluate(() => SM.go('today')); await owner.evaluate(() => SM.go('scanner')); await owner.waitForSelector('#scale-save');
  await Promise.all([
    owner.waitForResponse((r) => r.url().includes('/api/scanner/devices') && r.request().method() === 'POST'),
    owner.click('#scale-save'),
  ]);
  const devs = await owner.evaluate(() => SM.api('GET', '/api/settings').then(s => s.scanner_devices.filter(d => d.role === 'scale').map(d => d.id + '@' + d.workstation)));
  res.second_till_scale_keeps_the_first = devs.length === 2 && devs.includes('scale1@other-till');

  // L1: an ended session says so on the sign-in screen
  await owner.context().clearCookies();
  await owner.evaluate(() => SM.api('GET', '/api/stock').catch(() => {}));
  await owner.waitForSelector('#session-ended');
  res.L1_session_ended_said = await owner.locator('#session-ended').count() >= 1;

  res.no_page_errors_no_browser_dialogs = errors.length === 0;
  console.log(JSON.stringify(res, null, 1), errors);
  await browser.close();
  if (Object.values(res).some(v => v !== true)) process.exit(1);
})();
