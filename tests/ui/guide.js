// The Yousef-style guide through the real screens (training copy): the «الدليل» button shows my
// course; a lesson's coach outlines the real button, moves on by itself, and the lesson ends done.
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
  await page.waitForSelector('.sidebar, .who-list');
  if (await page.locator('.side-foot .logout-btn').count()) await page.click('.side-foot .logout-btn');
  await page.waitForSelector('[data-username=cashier]');
  await page.click('[data-username=cashier]');
  for (const d of '3826') await page.click('.pin-key[data-digit="' + d + '"]');
  await page.waitForSelector('.sidebar');
  // earlier test scripts may have left this cashier's shift open: close it so the lesson starts from the beginning
  await page.evaluate(async () => { const s = await SM.api('GET', '/api/shift').catch(() => null);
    if (s && s.shift) await SM.command('/api/shift/close', SM.newKey(), undefined, { counted: s.expected || 0 }).catch(() => null); });
  await page.reload(); await page.waitForSelector('.sidebar');
  await page.waitForSelector('[data-afg="fab"]', { timeout: 8000 });
  res.fab = (await page.textContent('[data-afg="fab"]')).trim();
  await page.click('[data-afg="fab"]'); await page.waitForSelector('[data-afg="panel"]:not([hidden])');
  res.course_title = await page.locator('[data-afg="panel"] [data-afg="course"] h3').textContent().catch(() => '');
  await page.screenshot({ path: out + '/guide-panel-ar.png' });
  // lesson 1 first (the course is in order): start it and press Next to the end
  await page.locator('[data-afg="panel"] li', { hasText: 'تعرّف على الشاشة الرئيسية' }).locator('[data-afg="start"]').first().click();
  await page.waitForSelector('[data-afg="coach"]:not([hidden])');
  for (let i = 0; i < 8; i++) {
    if (await page.locator('[data-afg="finish"]').count()) { await page.click('[data-afg="finish"]'); break; }
    if (await page.locator('[data-afg="there"]').count()) await page.click('[data-afg="there"]');
    await page.waitForTimeout(400);
    if (await page.locator('[data-afg="next"]').count()) await page.click('[data-afg="next"]');
    await page.waitForTimeout(300);
  }
  res.first_look_done = (await page.evaluate(() => SM.api('GET', '/api/guide/state'))).course.items.find(i => i.id === 'first-look').done;
  await page.click('[data-afg="fab"]'); await page.waitForSelector('[data-afg="panel"]:not([hidden])');
  // start "open your shift" from the course (the cashier has no shift yet in training)
  const lesson = page.locator('[data-afg="panel"] li', { hasText: 'افتح ورديتك' });
  await lesson.locator('[data-afg="start"]').first().click();
  await page.waitForSelector('[data-afg="coach"]:not([hidden])');
  // step 1 is "go to the drawer page": the coach offers to take me there
  if (await page.locator('[data-afg="there"]').count()) await page.click('[data-afg="there"]');
  await page.waitForTimeout(900);
  res.step_after_go = (await page.textContent('[data-afg="coach"]')).replace(/\s+/g, ' ').slice(0, 140);
  res.target_outlined = await page.locator('.afg-target[data-guide="cash.float"]').count() === 1;
  await page.screenshot({ path: out + '/guide-coach-ar.png' });
  await page.fill('[data-guide="cash.float"]', '500'); await page.waitForTimeout(900);
  res.moved_on_by_itself = await page.locator('.afg-target[data-guide="cash.open"]').count() === 1;
  await page.click('[data-guide="cash.open"]'); await page.waitForTimeout(1500);
  res.coach_text_end = (await page.textContent('[data-afg="coach"]').catch(() => '')).replace(/\s+/g, ' ').slice(0, 140);
  if (await page.locator('[data-afg="finish"]').count()) await page.click('[data-afg="finish"]');
  await page.waitForTimeout(800);
  const st = await page.evaluate(() => SM.api('GET', '/api/guide/state'));
  res.open_shift_done = st.course.items.find(i => i.id === 'open-shift').done;
  res.fab_after = (await page.textContent('[data-afg="fab"]')).trim();
  // an error offers «ماذا أفعل؟» that opens its explanation
  await page.evaluate(() => SM.fail({ code: 'insufficient_stock', status: 400 }));
  res.error_button = await page.locator('.toast [data-afg="explain"]').count() === 1;
  if (res.error_button) { await page.click('.toast [data-afg="explain"]'); await page.waitForTimeout(500);
    res.problem_open = (await page.textContent('[data-afg="panel"]')).includes('الكمية لا تكفي'); }
  await page.screenshot({ path: out + '/guide-problem-ar.png' });
  // the per-page "?" exists in the top bar
  res.page_help = await page.locator('.topbar [data-afg="help"]').count() === 1;
  res.ok = res.first_look_done && res.target_outlined && res.moved_on_by_itself && res.open_shift_done && res.error_button && res.problem_open && res.page_help;
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
