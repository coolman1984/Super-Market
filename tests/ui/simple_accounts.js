// Password and account features are easy to reach (owner's "simplicity first" rule):
// first-run tip, "forgot your password?" on the sign-in screen, a ready temporary password,
// the roles note, the account card on the help page, and the new lessons' targets.
// Usage: simple_accounts.js <training url> <empty shop url>
const { chromium } = require('playwright');
const trainingBase = process.argv[2] || 'http://127.0.0.1:8799';
const freshBase = process.argv[3] || 'http://127.0.0.1:8798';
(async () => {
  const browser = await chromium.launch();
  const res = {}, errors = [];
  // 1. a brand new shop: the first screen explains itself, and the way back in exists before anyone needs it
  const fresh = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
  fresh.on('pageerror', e => errors.push(e.message));
  await fresh.goto(freshBase); await fresh.waitForSelector('.auth-card');
  res.setup_tip = await fresh.locator('.auth-card .tip').count() === 1 && /8/.test(await fresh.locator('.auth-card .tip').innerText());
  await fresh.screenshot({ path: (process.argv[4] || '/tmp') + '/setup-tip-ar.png' });
  const pw = fresh.locator('input[type=password]');
  await fresh.locator('.auth-card input.input').nth(0).fill('محل التجربة');
  await fresh.locator('.auth-card input.input').nth(1).fill('المالك');
  await fresh.locator('.auth-card input[autocomplete=username]').fill('boss');
  await pw.nth(0).fill('first-pass-123'); await pw.nth(1).fill('first-pass-123');
  await fresh.click('button[type=submit]'); await fresh.waitForSelector('#login-forgot');
  // 2. sign-in screen: one button explains forgotten passwords (staff, owner, wrong tries)
  await fresh.click('#login-forgot'); await fresh.waitForSelector('.dialog');
  const txt = await fresh.locator('.dialog').innerText();
  res.forgot_staff_owner_wrong = await fresh.locator('.dialog h4').count() === 3 && await fresh.locator('.dialog ol li').count() === 4 && /30/.test(txt);
  await fresh.waitForTimeout(500); await fresh.screenshot({ path: (process.argv[4] || '/tmp') + '/forgot-ar.png' });
  await fresh.click('#forgot-ok');
  // 3. the owner: add-staff form brings a ready temporary password, reset shows one too, roles are explained
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await ctx.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(trainingBase); await page.waitForSelector('[data-username=owner]');
  await page.click('[data-username=owner]');
  for (const d of '159382') await page.click('.pin-key[data-digit="' + d + '"]');
  await page.waitForSelector('.sidebar');
  await page.goto(trainingBase + '/#settings'); await page.waitForSelector('[data-guide="set.users"]');
  res.roles_note = (await page.locator('[data-guide="set.roles_help"]').innerText()).length > 40;
  const ready = await page.locator('[data-guide="set.password"]').inputValue();
  res.ready_temp_password = ready.length >= 8 && await page.locator('[data-guide="set.password"]').getAttribute('type') === 'text';
  await page.fill('[data-guide="set.username"]', 'helper1'); await page.selectOption('[data-guide="set.role"]', 'cashier');
  await page.click('[data-guide="set.add_user"]'); await page.waitForTimeout(900);
  let users = await page.evaluate(() => SM.api('GET', '/api/users'));
  res.added_must_change = users.find(u => u.username === 'helper1').must_change === 1;
  // reset: dialog with a ready password and one Save button (no browser prompt)
  let prompted = false; page.on('dialog', d => { prompted = true; d.dismiss(); });
  await page.locator('#view tr', { hasText: 'helper1' }).locator('[data-guide="set.reset_password"]').click();
  await page.waitForSelector('[data-guide="set.temp_pw"]');
  const temp = await page.locator('[data-guide="set.temp_pw"]').inputValue();
  res.reset_ready = temp.length >= 8 && !prompted;
  await page.click('#reset-save'); await page.waitForTimeout(900);
  res.reset_saved = !(await page.locator('[data-guide="set.temp_pw"]').count());
  const login = await (await browser.newContext()).newPage();
  await login.goto(trainingBase); await login.waitForSelector('.who-list');
  const r = await login.evaluate(([u, p]) => SM.api('POST', '/api/login', { username: u, password: p, workstation: 'x' }).then(() => true, () => false), ['helper1', temp]);
  res.temp_password_works = r;
  // 4. help page: the account card with its three buttons; the lock button really locks
  await page.goto(trainingBase + '/#help'); await page.waitForSelector('[data-guide="help.account"]');
  res.account_card = await page.locator('#help-account button').count() === 4;
  await page.click('#help-forgot'); await page.waitForSelector('.dialog h4'); await page.click('#forgot-ok');
  await page.click('#help-lock'); await page.waitForTimeout(700);
  res.help_lock_works = await page.locator('.auth-card').count() === 1 && !(await page.locator('.sidebar').count());
  // 5. the new lessons exist for the owner's course and target real things
  const cat = await (await page.request.get(trainingBase + '/guide/catalogue.json')).json();
  const owner = cat.roles.find(x => x.id === 'owner').path;
  res.lessons = ['owner-account', 'staff-roles', 'reset-password', 'refund-approval', 'lock-screen', 'wrong-password', 'forgot-owner-password'].every(g => owner.includes(g));
  res.cashier_lessons = ['refund-approval', 'wrong-password', 'lock-screen'].every(g => cat.roles.find(x => x.id === 'cashier').path.includes(g));
  res.ok = Object.keys(res).every(k => res[k] === true);
  console.log(JSON.stringify(Object.assign(res, { errors }), null, 1));
  await browser.close();
  if (!res.ok || errors.length) process.exit(1);
})().catch(e => { console.error(e); process.exit(1); });
