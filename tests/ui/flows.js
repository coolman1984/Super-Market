// Owner flows through the real UI on a fresh TRAINING server (after smoke.js):
// return part of TR-000001, cut beef with a loss, make tagines with packs.
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8799';
const out = process.argv[3] || '.';
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base); await page.reload();
  await page.waitForSelector('[data-username=manager]');
  await page.click('[data-username=manager]');
  for (const d of '470296') await page.click('.pin-key[data-digit="' + d + '"]');
  await page.waitForSelector('.sidebar');
  // manager needs an open shift for a cash refund
  await page.goto(base + '/#cash'); await page.waitForSelector('.input.big');
  if (await page.locator('#view .narrow').count()) { await page.fill('.input.big', '100'); await page.click('button.btn.primary.lg'); await page.waitForTimeout(800); }
  await page.goto(base + '/#returns'); await page.waitForSelector('.input');
  await page.fill('#view .field input', 'TR-000001'); await page.press('#view .field input', 'Enter');
  await page.waitForSelector('#view .tbl');
  await page.fill('#view .tbl tbody tr:first-child input.input', '2');
  await page.click('#view .form-actions .btn.primary');
  await page.waitForSelector('.dialog'); const ret = await page.textContent('.dialog .body');
  await page.click('.dialog header .icon-btn');
  // cutting: 10 kg beef -> 7 cuts + 2 mince + 0.5 bone + 0.5 natural loss (training tolerance 8%)
  await page.goto(base + '/#prepare'); await page.waitForSelector('#view select');
  const sel = page.locator('#view select');
  await sel.nth(0).selectOption({ index: 1 });
  await page.locator('#view .card').nth(0).locator('input').first().fill('10');
  const opt = async (i, label) => { const s = sel.nth(i); const v = await s.locator('option', { hasText: label }).first().getAttribute('value'); await s.selectOption(v); };
  await opt(1, 'لحم بقري صافي');
  const outCard = page.locator('#view .card').nth(1);
  await outCard.locator('input').nth(0).fill('7');
  await outCard.locator('button').first().click(); await outCard.locator('button').first().click();
  const sels = outCard.locator('select');
  await sels.nth(2).selectOption((await sels.nth(2).locator('option', { hasText: 'لحم مفروم' }).first().getAttribute('value')));
  await outCard.locator('input').nth(1).fill('2');
  await sels.nth(4).selectOption((await sels.nth(4).locator('option', { hasText: 'عظم' }).first().getAttribute('value')));
  await outCard.locator('input').nth(2).fill('0.5');
  await page.locator('#view .card').nth(2).locator('input').first().fill('0.5');
  const mass = await page.textContent('.mass');
  await page.screenshot({ path: out + '/cutting-ar.png', fullPage: true });
  await page.click('#view .form-actions .btn.primary');
  await page.waitForSelector('.dialog'); const cut = await page.textContent('.dialog .body');
  await page.click('.dialog header .icon-btn');
  // tagines: recipe for 2 packs of 500 g
  await page.goto(base + '/#prepare'); await page.reload(); await page.waitForSelector('.seg');
  await page.click('.seg button:nth-child(2)');
  await page.waitForTimeout(300);
  const ps = page.locator('#view .card').first().locator('select');
  await ps.selectOption((await ps.locator('option', { hasText: 'طاجن' }).first().getAttribute('value')));
  await page.waitForTimeout(800);
  const ins = page.locator('#view .card').nth(1);
  const inSel = ins.locator('select');
  for (let i = 0; i < await inSel.count(); i++) await inSel.nth(i).selectOption({ index: 1 });
  const qtys = ['0.8', '0.2', '2'];
  for (let i = 0; i < 3; i++) await ins.locator('input').nth(i).fill(qtys[i]);
  const outs = page.locator('#view .card').nth(2);
  await outs.locator('input').nth(0).fill('1');
  await outs.locator('input').nth(1).fill('2');
  await outs.locator('input').nth(3).fill('0.04');
  await page.click('#view .form-actions .btn.primary');
  await page.waitForSelector('.dialog .pack-label', { timeout: 8000 }).catch(() => {});
  const tag = await page.textContent('.dialog .body').catch(() => 'no dialog');
  await page.screenshot({ path: out + '/tagine-labels-ar.png' });
  await page.click('.dialog header .icon-btn').catch(() => {});
  // ---- audit R17: a cart never passes to the next user ----
  const login = async (user) => {
    await page.goto(base); await page.reload();
    await page.waitForSelector('.sidebar, .who-list');      // the app has rendered one or the other
    if (await page.locator('.side-foot .logout-btn').count()) { await page.click('.side-foot .logout-btn'); }
    await page.waitForSelector('[data-username="' + user + '"]', { timeout: 8000 }).catch(async (e) => {
      await page.screenshot({ path: out + '/login-stuck.png' }); console.error('stuck', await page.textContent('body').then(x => x.slice(0, 300))); throw e; });
    const pins = { owner: '159382', manager: '470296', cashier: '3826', butcher: '2964', counter: '4702' };
    await page.click('[data-username="' + user + '"]');
    for (const d of pins[user]) await page.click('.pin-key[data-digit="' + d + '"]');
    await page.waitForSelector('.sidebar');
  };
  await login('cashier');
  await page.goto(base + '/#sell'); await page.waitForSelector('.scan-input');
  await page.fill('.scan-input', '6220000000013'); await page.press('.scan-input', 'Enter'); await page.waitForTimeout(400);
  const cashierCart = await page.textContent('.cart');
  await page.click('.side-foot .logout-btn'); await page.waitForSelector('[data-username=manager]');
  await page.click('[data-username=manager]');
  for (const d of '470296') await page.click('.pin-key[data-digit="' + d + '"]');
  await page.waitForSelector('.sidebar');
  await page.goto(base + '/#sell'); await page.waitForSelector('.scan-input');
  const managerCart = await page.textContent('.cart');
  // ---- audit S03: the sale is saved but the answer is lost ----
  const before = (await page.evaluate(() => SM.api('GET', '/api/reports/day'))).sales_count;
  await page.fill('.scan-input', '6220000000013'); await page.press('.scan-input', 'Enter'); await page.waitForTimeout(400);
  let cut_replies = 0;
  await page.route('**/api/sales', async (route) => {
    if (cut_replies < 3) { cut_replies++; await route.fetch(); await route.abort('connectionreset'); } else await route.continue();
  });
  await page.keyboard.press('F9'); await page.waitForSelector('.pay-total');
  await page.click('.dialog footer .btn.primary');
  await page.waitForSelector('.pay-panel .warn-tip', { timeout: 15000 });
  const unknownShown = await page.textContent('.pay-panel .warn-tip');
  await page.unroute('**/api/sales');
  await page.click('.pay-panel .warn-tip button');
  await page.waitForSelector('.receipt');
  const after = (await page.evaluate(() => SM.api('GET', '/api/reports/day'))).sales_count;
  await page.screenshot({ path: out + '/lost-reply-receipt-ar.png' });
  console.log(JSON.stringify({ ret, mass, cut: cut.slice(0, 200), tag: tag.slice(0, 300),
    r17_cashier_cart_had_item: /عصير/.test(cashierCart), r17_manager_cart_clean: !/عصير/.test(managerCart),
    s03_unknown_banner: unknownShown.length > 0, s03_sales_added: after - before, errors }, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
