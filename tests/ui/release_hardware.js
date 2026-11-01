// Current release acceptance: model setup, phone controls and collection labels.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.click('[data-username="owner"]');
    for (const digit of '159382') await page.click('.pin-key[data-digit="' + digit + '"]');
    await page.waitForSelector('.sidebar');
    const go = async view => {
      await page.evaluate(v => SM.go(v), view);
      await page.waitForFunction(v => SM.state.view === v && !document.querySelector('#view .skeleton'), view);
    };
    await go('scanner');
    await page.waitForSelector('#hardware-model');
    assert.equal(await page.locator('#hardware-model option').count(), 13);
    for (const family of ['zebra', 'honeywell', 'datalogic', 'newland']) {
      await page.selectOption('#hardware-model', family);
      await page.waitForSelector('#view textarea');
      assert.match(await page.locator('#hardware-model-help').innerText(), /لوحة المفاتيح/);
    }
    for (const family of ['rongta', 'cas', 'upos', 'digi', 'aclas']) {
      await page.selectOption('#hardware-model', family);
      assert.match(await page.locator('#hardware-model-help').innerText(), /ملصق/);
    }
    const overflow = () => page.evaluate(() => [...document.querySelectorAll('#view .card input,#view .card select')].filter(el => {
      const b = el.getBoundingClientRect(), c = el.closest('.card').getBoundingClientRect();
      return b.width && (b.left < c.left - 2 || b.right > c.right + 2);
    }).map(el => el.id || el.tagName));
    assert.deepEqual(await overflow(), []);
    await go('prepare');
    await page.waitForSelector('[data-guide="prep.loss"]');
    assert.deepEqual(await overflow(), []);
    await go('settings');
    await page.waitForSelector('.users-card .tbl');
    const hiddenActions = await page.locator('.users-card .tbl button').evaluateAll(buttons => buttons.filter(b => {
      const r = b.getBoundingClientRect(); return r.left < 0 || r.right > innerWidth;
    }).map(b => b.innerText));
    assert.deepEqual(hiddenActions, []);
    await go('reports');
    await page.waitForSelector('[data-guide="rep.collections"]');
    assert.match(await page.locator('[data-guide="rep.collections"]').innerText(), /التحصيل الفعلي/);
    assert.match(await page.locator('#view').innerText(), /وسائل سداد مبيعات اليوم/);
    assert.deepEqual(errors, []);
    fs.mkdirSync(out, { recursive: true });
    fs.writeFileSync(path.join(out, 'release-hardware.json'), JSON.stringify({
      modelFamilies: 12, scannerFamiliesChecked: 4, labelFamiliesChecked: 5,
      phoneFieldsContained: true, userActionsVisible: true, collectionBasisExplained: true,
      physicalHardwareTested: false, errors
    }, null, 2));
    console.log('Release hardware and phone acceptance passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
