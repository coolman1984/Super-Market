// UI smoke test against a running TRAINING server: node tests/ui/smoke.js http://127.0.0.1:8799 outdir
const { chromium } = require('playwright');
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
  const scan = async (code) => { await page.fill('.scan-input', code); await page.press('.scan-input', 'Enter'); await page.waitForTimeout(350); };
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
  await page.goto(base + '/#stock');
  await page.reload();
  await page.waitForSelector('.tbl');
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
  await page.waitForSelector('.side-foot .icon-btn');
  await page.click('.side-foot .icon-btn');
  await page.waitForSelector('input[type=password]');
  await page.fill('input[autocomplete=username]', 'owner');
  await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]');
  await page.waitForSelector('.sidebar');
  for (const v of ['today', 'reports', 'stock', 'prepare', 'receive', 'count', 'catalog', 'scanner', 'settings', 'help', 'returns', 'cash']) {
    await page.goto(base + '/#' + v);
    await page.waitForTimeout(600);
    const txt = await page.textContent('#view');
    if (/err\.|undefined|NaN|null/.test(txt)) errors.push(v + ': suspicious text ' + (txt.match(/.{0,30}(err\.|undefined|NaN|null).{0,30}/) || [''])[0]);
    if (['today', 'prepare', 'scanner', 'reports'].includes(v)) await page.screenshot({ path: out + '/' + v + '-ar.png', fullPage: true });
  }
  console.log(JSON.stringify({ status, qtyHasThree: /3/.test(qtyText), cartPreview: qtyText.slice(0, 200), cleared, receiptHasTraining: /تدريب/.test(receipt), overflow, errors }, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
