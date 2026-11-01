// UI smoke test against a running TRAINING server: node tests/ui/smoke.js http://127.0.0.1:8799 outdir
const { chromium } = require('playwright');
const { scanCode, viewText } = require('./wait');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });
  await page.goto(base);
  await page.fill('#' + (await page.locator('input[autocomplete=username]').getAttribute('id')), 'cashier');
  await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]');
  await page.waitForSelector('.sidebar');
  // open shift
  await page.goto(base + '/#cash');
  await page.waitForSelector('.input.big');
  await page.fill('.input.big', '500');
  await page.click('button.btn.primary.lg');
  await page.waitForSelector('.scan-input');
  // scan juice twice (two units), then carton, then a bad code
  const scan = (code) => scanCode(page, code);
  await scan('6220000000013');
  await scan('6220000000013');
  await scan('16220000000010');
  await scan('6220000000014');
  const status = await page.textContent('.scan-status');
  await page.screenshot({ path: out + '/sell-ar-day.png' });
  const qtyText = await page.textContent('.cart');
  // pay with F9
  await page.keyboard.press('F9');
  await page.waitForSelector('.pay-total');
  // a barcode typed into the cash field is refused
  await page.fill('.dialog .input.big', '6220000000013');
  await page.press('.dialog .input.big', 'Enter');
  const cleared = await page.inputValue('.dialog .input.big');
  await page.fill('.dialog .input.big', '600');
  await page.screenshot({ path: out + '/pay-ar.png' });
  await page.click('.dialog footer .btn.primary');
  await page.waitForSelector('.receipt');
  const receipt = await page.textContent('.receipt');
  await page.screenshot({ path: out + '/receipt-ar.png' });
  await page.click('.dialog footer .btn.primary');
  // dark + English + phone width
  await page.evaluate(() => { localStorage.setItem('sm.theme', '"night"'); localStorage.setItem('sm.lang', '"en"'); });
  await page.reload();
  await page.waitForSelector('.sidebar');
  await Promise.all([
    page.waitForResponse((r) => r.url().includes('/api/stock') && r.ok()),
    page.goto(base + '/#stock'),
  ]);
  await page.waitForSelector('#view .tbl');
  await page.screenshot({ path: out + '/stock-en-night.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(base + '/#sell');
  await page.reload();
  await page.waitForSelector('.scan-input');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await page.screenshot({ path: out + '/sell-en-phone.png' });
  await page.evaluate(() => { localStorage.setItem('sm.theme', '"daylight"'); localStorage.setItem('sm.lang', '"ar"'); });
  // owner views render without errors
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto(base);
  await page.reload();
  await page.waitForSelector('.side-foot .logout-btn');
  await page.click('.side-foot .logout-btn');
  await page.waitForSelector('input[type=password]');
  await page.fill('input[autocomplete=username]', 'owner');
  await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]');
  await page.waitForSelector('.sidebar');
  for (const v of ['today', 'reports', 'parties', 'stock', 'prepare', 'receive', 'count', 'catalog', 'scanner', 'settings', 'help', 'returns', 'cash']) {
    await page.goto(base + '/#' + v);
    await viewText(page, '#' + v);
    const txt = await page.textContent('#view');
    const rawKey = txt.match(/\b(?:nav|party|scn|safe|recall|refund|pay|stock|ret|rep|cash|cat|recv|prep|lots|count|set|lbl|help|pending|entry|scan|receipt|state|kind|role|waste|origin)\.[a-z_0-9.]+/);
    if (rawKey) errors.push(v + ': untranslated key ' + rawKey[0]);
    if (/err\.|undefined|NaN|null|\[object/.test(txt)) errors.push(v + ': suspicious text ' + (txt.match(/.{0,30}(err\.|undefined|NaN|null).{0,30}/) || [''])[0]);
    if (['today', 'prepare', 'scanner', 'reports', 'parties', 'cash'].includes(v)) await page.screenshot({ path: out + '/' + v + '-ar.png', fullPage: true });
  }
  // the same screens in English + night: every key has an English text
  await page.evaluate(() => { localStorage.setItem('sm.theme', '"night"'); localStorage.setItem('sm.lang', '"en"'); });
  await page.reload();
  await page.waitForSelector('.sidebar');
  for (const v of ['today', 'reports', 'parties', 'stock', 'prepare', 'receive', 'count', 'catalog', 'scanner', 'settings', 'help', 'returns', 'cash']) {
    await page.goto(base + '/#' + v);
    await viewText(page, '#' + v);
    const txt = await page.textContent('#view');
    if (/[\u0600-\u06FF]{3,}/.test(txt.replace(/سوبر ماركت التدريب|[\u0600-\u06FF ()١-٩]+تدريب[^a-z]*/g, '')) && ['help', 'scanner'].includes(v))   // other screens show shop data (names) in Arabic
      errors.push(v + ': Arabic text in English mode');
    if (/undefined|NaN|\[object/.test(txt)) errors.push(v + ' (en): suspicious text');
  }
  await page.screenshot({ path: out + '/cash-en-night.png', fullPage: true });
  await page.evaluate(() => { localStorage.setItem('sm.theme', '"daylight"'); localStorage.setItem('sm.lang', '"ar"'); });
  console.log(JSON.stringify({ status, qtyHasThree: /3/.test(qtyText), cartPreview: qtyText.slice(0, 200), cleared, receiptHasTraining: /تدريب/.test(receipt), overflow, errors }, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
