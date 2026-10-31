// SM-02 / T-M4: zero asks once; unreadable price is refused by name; ٢٥ and 20,5 still save.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const res = {};
  const page = await (await browser.newContext({ viewport: { width: 1366, height: 860 }, locale: 'ar-EG' })).newPage();
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', d => { errors.push('browser dialog ' + d.type()); d.dismiss(); });
  const priceOf = (code) => page.evaluate(async (code) => {
    const rows = await SM.api('GET', '/api/products?limit=300');
    const row = rows.find(r => r.code === code);
    return row ? row.price : null;
  }, code);
  const count = () => page.evaluate(async () => (await SM.api('GET', '/api/products?limit=300')).length);
  const quiet = () => page.evaluate(() => { const t = document.getElementById('toasts'); if (t) t.replaceChildren(); });
  const closeDrawer = async () => {
    const btn = page.locator('.drawer .icon-btn');
    if (await btn.count()) { await btn.first().click(); await page.waitForTimeout(300); }
  };
  const openCode = async (code) => {
    await closeDrawer();
    const row = page.locator('.page table tbody tr', { hasText: code });
    await row.first().scrollIntoViewIfNeeded();
    await row.first().click();
    await page.waitForSelector('.drawer');
  };

  await page.goto(base);
  await page.waitForSelector('input[autocomplete=username]');
  await page.fill('input[autocomplete=username]', 'owner');
  await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]');
  await page.waitForSelector('.sidebar');
  await page.evaluate(() => SM.go('catalog'));
  await page.waitForTimeout(700);
  const before = await priceOf('1001');
  await openCode('1001');
  const change = page.locator('.drawer button', { hasText: 'تغيير السعر' });
  await change.first().click();
  await page.waitForSelector('#ask-input');
  await page.fill('#ask-input', '0');
  await page.click('#ask-ok');
  await page.waitForSelector('#confirm-yes');
  res.one_zero_dialog = await page.locator('[role=alertdialog]').count() === 1;
  res.zero_phrase = (await page.locator('#ask-layer h2').innerText()) === 'السعر صفر، متأكد؟';
  res.yes_no = (await page.locator('#confirm-yes').innerText()) === 'نعم' && (await page.locator('#confirm-no').innerText()) === 'لا';
  const yesBox = await page.locator('#confirm-yes').boundingBox();
  const noBox = await page.locator('#confirm-no').boundingBox();
  res.large_buttons = yesBox.height >= 48 && noBox.height >= 48;
  await page.screenshot({ path: out + '/after-zero-confirm.png' });
  await page.click('#confirm-no');
  await page.waitForTimeout(300);
  res.cancel_keeps_price = (await priceOf('1001')) === before;
  res.no_second_dialog = await page.locator('[role=alertdialog]').count() === 0;

  await change.first().click();
  await page.waitForSelector('#ask-input');
  await page.fill('#ask-input', '0');
  await page.click('#ask-ok');
  await page.waitForSelector('#confirm-yes');
  await page.click('#confirm-yes');
  await page.waitForTimeout(500);
  res.yes_saves_zero = (await priceOf('1001')) === 0;
  await quiet();

  await openCode('1001');
  await change.first().click();
  await page.waitForSelector('#ask-input');
  await page.fill('#ask-input', 'abc');
  await page.click('#ask-ok');
  await page.waitForTimeout(200);
  res.abc_keeps_the_box = await page.locator('#ask-input').count() === 1;
  res.abc_names_the_field = (await page.locator('#ask-layer .field-error').innerText()).includes('سعر البيع');
  res.abc_does_not_save = (await priceOf('1001')) === 0;
  await page.screenshot({ path: out + '/after-unreadable-change.png' });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);

  await change.first().click();
  await page.waitForSelector('#ask-input');
  await page.fill('#ask-input', '٢٥');
  await page.click('#ask-ok');
  await page.waitForTimeout(500);
  await page.waitForFunction(() => {
    const row = [...document.querySelectorAll('.page table tbody tr')].find(tr => tr.innerText.includes('1001'));
    return row && row.innerText.includes('25.00');
  });
  res.arabic_digits = (await priceOf('1001')) === 2500;
  await quiet();
  await page.screenshot({ path: out + '/after-arabic-digits.png' });

  await openCode('1001');
  await change.first().click();
  await page.waitForSelector('#ask-input');
  await page.fill('#ask-input', '20,5');
  await page.click('#ask-ok');
  await page.waitForTimeout(500);
  await page.waitForFunction(() => {
    const row = [...document.querySelectorAll('.page table tbody tr')].find(tr => tr.innerText.includes('1001'));
    return row && row.innerText.includes('20.50');
  });
  res.decimal_comma = (await priceOf('1001')) === 2050;
  await quiet();
  await page.screenshot({ path: out + '/after-comma.png' });

  const n = await count();
  await page.click('[data-guide="cat.add"]');
  await page.waitForSelector('.dialog [data-guide="cat.price"]');
  await page.fill('.dialog [data-guide="cat.name_ar"]', 'صنف بسعر مبهم');
  await page.fill('.dialog [data-guide="cat.price"]', 'abc');
  await page.click('.dialog footer .btn.primary');
  await page.waitForTimeout(300);
  res.new_abc_names_the_field = (await page.locator('.dialog .field-error').innerText()).includes('سعر البيع');
  res.new_abc_keeps_the_form = await page.locator('.dialog [data-guide="cat.name_ar"]').inputValue() === 'صنف بسعر مبهم';
  res.new_abc_not_saved = (await count()) === n;
  await page.screenshot({ path: out + '/after-unreadable-new.png' });

  res.server_refuses_text = await page.evaluate(async () => {
    try {
      await SM.api('POST', '/api/products', { name_ar: 'س', name_en: 'S', kind: 'grocery', measure: 'unit', price: 'abc' });
      return false;
    } catch (e) { return e.code === 'bad_price'; }
  });
  res.server_refuses_null = await page.evaluate(async () => {
    try {
      await SM.api('POST', '/api/products', { name_ar: 'س', name_en: 'S', kind: 'grocery', measure: 'unit', price: null });
      return false;
    } catch (e) { return e.code === 'bad_price'; }
  });
  res.no_page_errors = errors.length === 0;
  console.log(JSON.stringify(res, null, 1), errors);
  await browser.close();
  if (Object.values(res).some(v => v !== true)) process.exit(1);
})().catch(e => { console.error(e); process.exit(1); });
