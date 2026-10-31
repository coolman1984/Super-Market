// Review C through the real HTTP service and rendered Arabic screens.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.fill('input[autocomplete=username]', 'owner');
    await page.fill('input[type=password]', 'training-only');
    await page.click('button[type=submit]');
    await page.waitForSelector('.sidebar');
    const T = k => page.evaluate(k => SM.t(k), k);
    const data = await page.evaluate(async () => {
      const product = await SM.api('POST', '/api/products', { code: 'REVC-' + SM.newKey().slice(0, 8), name_ar: 'صنف مراجعة', name_en: 'Review product', kind: 'grocery', measure: 'unit', price: 2000 });
      const supplier = await SM.api('POST', '/api/parties', { kind: 'supplier', name: 'مورد مراجعة ثالثة' });
      const loc = (await SM.api('GET', '/api/locations')).find(x => x.kind === 'shelf');
      const r = await SM.command('/api/receipts', SM.newKey(), { supplier_id: supplier.id,
        lines: [{ product_id: product.id, qty: 10, line_cost: 10000, location_id: loc.id }] });
      const ret = await SM.command('/api/supplier-returns', SM.newKey(), undefined, {
        supplier_id: supplier.id, lot_id: r.lines[0].lot, location_id: loc.id, qty: 5, reason: 'quality' });
      await SM.command('/api/supplier-credit-notes', SM.newKey(), undefined, { return_id: ret.supplier_return, amount: 3000, reference: 'C-ONE' });
      const note = await SM.command('/api/supplier-credit-notes', SM.newKey(), undefined, { return_id: ret.supplier_return, amount: 2000, reference: 'C-TWO' });
      const guide = await SM.api('GET', '/api/guide/state');
      return { note, guide };
    });
    assert.equal(data.note.difference_from_cost, 0);
    assert.equal(data.note.credited_total, 5000);
    assert.ok(data.guide.course.items.some(x => x.id === 'add-product' && x.done && x.auto));
    await page.evaluate(() => SM.go('parties'));
    await page.getByText('مورد مراجعة ثالثة', { exact: true }).click();
    await page.waitForSelector('.drawer');
    assert.ok((await page.locator('.drawer').innerText()).includes(await T('party.credit_difference')));
    assert.ok((await page.locator('.drawer').innerText()).includes(await T('party.claim_covered')));
    assert.ok(await page.locator('[data-supplier-return]').evaluate(el => el.scrollWidth <= el.clientWidth + 1));
    await page.locator('.drawer').screenshot({ path: out + '/review-c-supplier-ar.png' });
    page.once('dialog', d => d.accept('تسوية نهائية بعد استلام الإشعارين'));
    await page.getByRole('button', { name: await T('party.close_claim'), exact: true }).click();
    await page.waitForSelector('.drawer', { state: 'detached' });
    await page.getByText('مورد مراجعة ثالثة', { exact: true }).click();
    await page.waitForSelector('.drawer');
    assert.ok((await page.locator('.drawer').innerText()).includes(await T('party.claim_closed')));
    assert.ok((await page.locator('.drawer').innerText()).includes('تسوية نهائية بعد استلام الإشعارين'));
    assert.equal(await page.locator('.drawer').getByRole('button', { name: await T('party.credit_note'), exact: true }).count(), 0);
    await page.locator('.drawer-head button').click();
    // Failed status lookup is unknown, never silently "disabled" or "saved".
    await page.route('**/api/cloud', r => r.fulfill({ status: 503, json: { error: 'unavailable' } }));
    await page.evaluate(() => SM.go('settings'));
    await page.waitForSelector('#cloud-card');
    assert.ok((await page.locator('#cloud-card').innerText()).includes(await T('set.cloud_unknown')));
    await page.unroute('**/api/cloud');
    await page.route('**/api/cloud', r => r.fulfill({ json: { enabled: true, queued: 1, last_ok_at: null, last_copy: null, error: 'offline' } }));
    await page.evaluate(() => SM.go('today'));
    await page.evaluate(() => SM.go('settings'));
    await page.waitForSelector('#cloud-card');
    assert.ok((await page.locator('#cloud-card').innerText()).includes(await T('set.cloud_never')));
    await page.setViewportSize({ width: 390, height: 844 });
    // The shell animates its grid and sidebar after a viewport change. Wait for
    // that real state before asserting geometry or capturing a mobile image.
    await page.waitForFunction(() => {
      const r = document.querySelector('.sidebar').getBoundingClientRect();
      return r.left >= innerWidth || r.right <= 0;
    });
    await page.locator('#cloud-card').scrollIntoViewIfNeeded();
    assert.ok(await page.locator('#cloud-card').evaluate(el => {
      const r = el.getBoundingClientRect();
      return r.left >= -1 && r.right <= innerWidth + 1 && el.scrollWidth <= el.clientWidth + 1;
    }));
    await page.screenshot({ path: out + '/review-c-cloud-mobile-ar.png' });
    assert.deepEqual(errors, []);
    console.log('Review C: cumulative credit, final settlement, guide evidence and cloud uncertainty passed.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
