// The eight matrix rows added after 31 Oct 2026, through the real screens on a
// fresh TRAINING server (run after flows.js): invoice before goods (2), gift line (5),
// cost correction (3), carton change (9), thaw (14), buy-get offer (26), day close (38).
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
  await page.fill('input[autocomplete=username]', 'owner'); await page.fill('input[type=password]', 'training-only');
  await page.click('button[type=submit]'); await page.waitForSelector('.sidebar');
  // a saved dialog closes; if it stays open the save failed and the toast says why
  const closed = () => page.waitForSelector('#overlay.on', { state: 'detached', timeout: 8000 }).catch(async () => {
    throw new Error('dialog still open: ' + (await page.locator('.toast').allTextContents()).join(' | ')); });
  const api = (m, p, b) => page.evaluate(([m, p, b]) => SM.api(m, p, b), [m, p, b]);
  const sup = (await api('GET', '/api/parties?kind=supplier'))[0] || { id: (await api('POST', '/api/parties', { kind: 'supplier', name: 'مورد التدريب' })).id };
  const juice = (await api('GET', '/api/products?q=1001'))[0];
  // ---- row 2: register the invoice in its dialog, then receive against it
  await page.goto(base + '/#receive'); await page.waitForSelector('#view select');
  await page.click('#view details.advanced > summary');          // an invoice before the goods is the exception: folded under its heading
  await page.click('text=' + await page.evaluate(() => SM.t('recv.register_invoice')));
  await page.waitForSelector('.dialog');
  const dlg = page.locator('.dialog');
  await dlg.locator('select').nth(0).selectOption(String(sup.id));
  await dlg.locator('input').nth(0).fill('UI-INV-1');
  await dlg.locator('select').nth(1).selectOption(String(juice.id));
  const qtyIn = dlg.locator('.grid.cols-4 input');
  await qtyIn.nth(0).fill('10'); await qtyIn.nth(2).fill('100');
  await page.screenshot({ path: out + '/invoice-before-goods-ar.png' });
  await dlg.locator('footer .btn.primary').click(); await closed();
  const invs = await api('GET', '/api/supplier-invoices');
  res.row02_invoice_registered = invs.some(i => i.number === 'UI-INV-1');
  await page.waitForSelector('#view select');
  const inv = invs.find(i => i.number === 'UI-INV-1');
  const before = (await api('GET', '/api/products?q=1001'))[0].available;
  res.row02_no_stock_yet = before === juice.available;
  if (!(await page.locator('#view details.advanced').evaluate(d => d.open))) await page.click('#view details.advanced > summary');
  await page.locator('#view details.advanced select').first().selectOption(String(inv.id));
  await page.click('text=' + await page.evaluate(() => SM.t('recv.add_product')));
  await page.waitForSelector('.dialog select');
  await page.locator('.dialog select').selectOption(String(juice.id));
  await page.click('.dialog footer .btn.primary');
  await page.locator('.recv-line input').first().fill('6');
  await page.locator('.recv-line input[type=date]').fill('2027-01-31');
  res.row02_cost_from_invoice = (await page.textContent('.recv-line')).includes(await page.evaluate(() => SM.t('recv.cost_from_invoice')));
  await page.click('#view .form-actions .btn.primary'); await page.waitForTimeout(800);
  res.row02_received_6 = (await api('GET', '/api/products?q=1001'))[0].available - before === 6;
  // ---- row 5: gift line on the same screen
  await page.goto(base + '/#receive'); await page.reload(); await page.waitForSelector('#view select');
  await page.click('text=' + await page.evaluate(() => SM.t('recv.add_product')));
  await page.waitForSelector('.dialog select'); await page.locator('.dialog select').selectOption(String(juice.id));
  await page.click('.dialog footer .btn.primary');
  await page.locator('.recv-line select').first().selectOption('gift');
  await page.locator('.recv-line input').first().fill('2');
  await page.locator('.recv-line input[type=date]').fill('2027-01-31');
  await page.click('#view .form-actions .btn.primary'); await page.waitForTimeout(800);
  res.row05_gift_lines = (await api('GET', '/api/reports/day')).gift_lines >= 1;
  // ---- row 3: invoice correction from the supplier statement
  await page.goto(base + '/#parties'); await page.waitForSelector('#view .tbl');
  await page.locator('#view .card').first().locator('tbody tr').first().click();
  await page.waitForSelector('.drawer');
  await page.click('.drawer >> text=' + await page.evaluate(() => SM.t('adj.correction')));
  await page.waitForSelector('.dialog');
  const ad = page.locator('.dialog');
  await ad.locator('select').nth(0).selectOption({ index: 1 });
  await ad.locator('input').nth(0).fill('6'); await ad.locator('input').nth(1).fill('UI-FINAL-1');
  await page.screenshot({ path: out + '/cost-correction-ar.png' });
  await ad.locator('footer .btn.primary').click(); await closed();
  res.row03_toast = await page.locator('.toast').last().textContent().catch(() => '');
  // ---- row 9: carton change from the product drawer (product with a carton in the seed, if any)
  const cartonId = (await api('POST', '/api/products', { code: 'CT1', name_ar: 'مياه معدنية', name_en: 'Water', kind: 'grocery', measure: 'unit',
    price: 1000, packagings: [{ name_ar: 'كرتونة', name_en: 'Carton', factor_num: 12, barcodes: ['6229990000010'] }] })).id;
  await page.goto(base + '/#catalog'); await page.waitForSelector('#view .tbl');
  await page.locator('#view .tbl tbody tr', { hasText: 'CT1' }).first().click();
  await page.waitForSelector('.drawer');
  await page.click('.drawer >> text=' + await page.evaluate(() => SM.t('cat.carton_change')));
  await page.waitForSelector('.dialog');
  await page.locator('.dialog input').nth(0).fill('10'); await page.locator('.dialog input').nth(1).fill('المورد غيّر التعبئة');
  await page.screenshot({ path: out + '/carton-change-ar.png' });
  await page.locator('.dialog footer .btn.primary').click(); await closed();
  const after9 = await api('GET', '/api/products/' + cartonId);
  res.row09_old_retired_new_10 = after9.packagings.length === 2 && !!after9.packagings[0].retired_op && after9.packagings[1].factor_num === 10;
  res.row09_scan_now_10 = (await api('POST', '/api/scan', { raw: '6229990000010' })).qty === 10;
  await page.goto(base + '/#catalog'); await page.waitForSelector('#view .tbl');
  // ---- row 26: offer from the product drawer, then a quote
  await page.locator('#view .tbl tbody tr', { hasText: juice.code }).first().click();
  await page.waitForSelector('.drawer');
  await page.click('.drawer >> text=' + await page.evaluate(() => SM.t('promo.add')));
  await page.waitForSelector('.dialog');
  await page.locator('.dialog footer .btn.primary').click(); await closed();
  const q = await api('POST', '/api/quote', { payload: { lines: [{ product_id: juice.id, qty: 3 }] } });
  res.row26_promo_on_quote = q.promo === juice.price;
  await page.goto(base + '/#catalog'); await page.waitForSelector('#view .tbl');
  res.row26_list_shown = (await page.textContent('#view')).includes(await page.evaluate(() => SM.t('promo.list')));
  // ---- row 14: thaw needs the owner's period; then the dialog moves frozen stock out
  const fz = (await api('POST', '/api/locations', { code: 'FZ', name_ar: 'المجمد', name_en: 'Freezer', kind: 'freezer', sellable: 0 })).id;
  const meat = (await api('GET', '/api/products?limit=300')).find(p => p.kind === 'raw_meat');
  res.row14_frozen_in = await page.evaluate(([m, fz]) => SM.command('/api/receipts', SM.newKey(), { lines: [{ product_id: m, gross_g: 2000, tare_g: 0, line_cost: 40000,
    location_id: fz, expires: '2027-01-01' }] }).then(r => r.lines.length, e => 'error ' + e.code), [meat.id, fz]);
  await api('POST', '/api/settings', { thaw_shelf_life_days: 2 });
  await page.goto(base + '/#stock'); await page.waitForSelector('#view .tbl');
  await page.click('text=' + await page.evaluate(() => SM.t('thaw.btn')));
  await page.waitForSelector('.dialog', { timeout: 5000 }).catch(async () => { console.log('no thaw dialog', res, await page.locator('.toast').allTextContents()); });
  await page.locator('.dialog select').nth(0).selectOption({ index: 1 });
  await page.locator('.dialog input').first().fill('1');
  await page.screenshot({ path: out + '/thaw-ar.png' });
  await page.locator('.dialog footer .btn.primary').click(); await closed();
  res.row14_toast = await page.locator('.toast').last().textContent().catch(() => '');
  // ---- row 38: the reports screen offers the day close with its explanation
  await page.goto(base + '/#reports'); await page.waitForSelector('#view .card');
  await page.waitForTimeout(800);
  res.row38_close_card = (await page.textContent('#view')).includes(await page.evaluate(() => SM.t('close.title')));
  await page.screenshot({ path: out + '/day-close-ar.png', fullPage: true });
  // no raw keys on the touched screens
  for (const v of ['receive', 'stock', 'parties', 'catalog', 'reports']) {
    await page.goto(base + '/#' + v); await page.waitForTimeout(500);
    const raw = (await page.textContent('#view')).match(/\b(?:adj|close|promo|thaw|recv|cat|rep|party)\.[a-z_.]+/);
    if (raw) errors.push(v + ': raw key ' + raw[0]);
  }
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
