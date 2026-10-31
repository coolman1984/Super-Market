/* Shell: setup, login, sidebar, topbar, training banner, hash routing. */
'use strict';

(function () {
  const { h, t, icon } = SM;
  const NAV = [
    ['work', ['today', 'sell', 'returns', 'cash']],
    ['goods', ['receive', 'stock', 'prepare', 'count']],
    ['manage', ['reports', 'parties', 'catalog', 'scanner', 'settings', 'help']],
  ];
  const PERM = { sell: 'sell', returns: 'refund', cash: 'shift', receive: 'receive', prepare: ['cutting', 'produce'], count: 'count',
    reports: 'reports', scanner: 'scanner_setup', parties: ['receive', 'supplier_pay', 'sell'] };
  const allowed = (v) => { const p = PERM[v]; return !p || (Array.isArray(p) ? p.some(SM.can) : SM.can(p)); };
  let current = null;

  SM.go = (view) => { if (location.hash === '#' + view) route(); else location.hash = view; };

  async function boot() {
    SM.applyPrefs();
    let state;
    try { state = await SM.api('GET', '/api/state'); } catch (e) { SM.put(document.body, h('p', { class: 'empty', text: t('err.server_down') })); return; }
    SM.state.mode = state.mode; SM.state.instance = state.instance; SM.state.businessDay = state.business_day; SM.state.version = state.version; SM.state.trainingLogin = state.training_login;
    document.title = (state.shop_name || t('app.name')) + (state.mode === 'training' ? ' — ' + t('mode.training') : '');
    if (state.clock_behind) return clockScreen(state);
    if (state.setup_needed) return setupScreen(state);
    if (!state.user) return loginScreen(state);
    SM.state.user = state.user;
    SM.state.session = (SM.state.session || 0) + 1;   // late answers of a previous session are ignored
    if (state.user.locked) return lockScreen(state);
    if (state.user.must_change) return passwordScreen(state, true);
    SM.state.settings = await SM.api('GET', '/api/settings').catch(() => ({}));
    SM.scanner.installGlobalOnce();
    await SM.scanner.load();
    SM.renderShell();
    if (SM.guide) SM.guide.mount();
  }
  /* Audit R17: nothing typed or scanned by one user survives for the next. */
  SM.onLoggedOut = () => {
    if (SM.guide) SM.guide.unmount();          // nothing of one person's guide stays for the next
    SM.state.user = null; SM.state.session = (SM.state.session || 0) + 1;
    SM.scanner.stop(); SM.scanner.handler = null;
    for (const reset of SM.draftResets) { try { reset(); } catch (e) { /* ignore */ } }
    boot();
  };
  SM.onAccountGate = () => { if (!gateShown) { gateShown = true; boot().finally(() => { gateShown = false; }); } };
  let gateShown = false;

  /* Locked till: only the SAME user's password opens it; "switch user" logs out (drafts stay per user). */
  /* Review M5: the PC date is behind a closed day: say so plainly instead of refusing every sale with a vague error. */
  function clockScreen(state) {
    const c = state.clock_behind;
    authFrame(t('clock.title'), t('clock.body', { today: '\u2066' + c.today + '\u2069', closed: '\u2066' + c.closed + '\u2069' }),
      h('button', { class: 'btn primary lg', id: 'clock-retry', text: t('clock.retry'), onclick: () => boot() }));
  }

  function lockScreen(state) {
    const pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
    SM.scanner.handler = null;
    authFrame(t('lock.title', { name: state.user.name }), t('lock.sub'), h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      try { await SM.api('POST', '/api/account/unlock', { password: pass.value }); boot(); } catch (err) { pass.value = ''; SM.fail(err); }
    } }, SM.field(t('password'), pass), h('button', { class: 'btn primary lg', type: 'submit', text: t('lock.open') }),
      h('button', { class: 'btn ghost', type: 'button', text: t('lock.switch'), onclick: async () => { await SM.api('POST', '/api/logout', {}).catch(() => {}); SM.onLoggedOut(); } })));
    pass.focus();
  }

  /* Own password. Forced after the owner typed it (new employee or reset). */
  function passwordScreen(state, forced) {
    let dlg = null;   // closed before the shell is rebuilt, so the scanner and F-keys work again
    const old = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', 'data-guide': 'account.old' });
    const pw = h('input', { class: 'input', type: 'password', autocomplete: 'new-password', 'data-guide': 'account.new' }), pw2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
    const form = h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      if (pw.value !== pw2.value) return SM.toast(t('setup.mismatch'), 'bad');
      try { await SM.api('POST', '/api/account/password', { old: old.value, new: pw.value }); if (dlg) dlg.close(); SM.toast(t('account.changed')); boot(); } catch (err) { SM.fail(err); }
    } }, SM.field(forced ? t('account.temp_password') : t('account.old_password'), old), SM.field(t('account.new_password'), pw, t('set.pw_help')),
      SM.field(t('setup.repeat'), pw2), h('button', { class: 'btn primary lg', type: 'submit', text: t('account.change') }),
      forced ? h('button', { class: 'btn ghost', type: 'button', text: t('logout'), onclick: async () => { await SM.api('POST', '/api/logout', {}).catch(() => {}); SM.onLoggedOut(); } }) : null);
    if (forced) { authFrame(t('account.forced_title'), t('account.forced_sub'), form); old.focus(); return; }
    dlg = SM.dialog({ title: t('account.change'), body: [form] });
    return dlg;
  }
  SM.passwordDialog = () => passwordScreen(null, false);

  SM.scanner.installGlobalOnce = function () { if (!this._installed) { this._installed = true; this.installGlobal(); } };

  function authFrame(title, sub, form) {
    SM.put(document.body, h('main', { class: 'auth-wrap' },
      SM.state.mode === 'training' ? h('div', { class: 'train-bar' }, icon('school'), h('span', { text: t('mode.training_bar') })) : null,
      h('div', { class: 'card auth-card' }, h('div', { class: 'brand-mark' }, icon('cart', 'lg')), h('h1', { text: title }), h('p', { class: 'muted', text: sub }), form,
        h('div', { class: 'row auth-foot' }, h('button', { class: 'btn ghost sm', onclick: () => { SM.prefs.set('lang', SM.state.lang === 'ar' ? 'en' : 'ar'); SM.applyPrefs(); boot(); } }, icon('lang', 'sm'), SM.state.lang === 'ar' ? 'English' : 'العربية')))));
  }

  function setupScreen() {
    const shop = h('input', { class: 'input' }), user = h('input', { class: 'input', autocomplete: 'username' }), name = h('input', { class: 'input' });
    const pass = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' }), pass2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
    authFrame(t('setup.title'), t('setup.sub'), h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      if (pass.value !== pass2.value) return SM.toast(t('setup.mismatch'), 'bad');
      try { await SM.api('POST', '/api/setup', { shop_name: shop.value, username: user.value, name: name.value || user.value, password: pass.value }); SM.toast(t('setup.done')); boot(); } catch (err) { SM.fail(err); }
    } }, SM.field(t('set.shop_name'), shop), SM.field(t('setup.owner_name'), name), SM.field(t('username'), user), SM.field(t('password'), pass, t('set.pw_help')),
      SM.field(t('setup.repeat'), pass2), h('button', { class: 'btn primary lg', type: 'submit', text: t('setup.go') })));
  }

  function loginScreen(state) {
    const user = h('input', { class: 'input', autocomplete: 'username', autofocus: true }), pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
    const tl = state.training_login;
    authFrame(state.shop_name || t('app.name'), t('login.sub'), h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      try { await SM.api('POST', '/api/login', { username: user.value, password: pass.value, workstation: SM.workstation() }); boot(); } catch (err) { SM.fail(err); }
    } }, SM.field(t('username'), user), SM.field(t('password'), pass), h('button', { class: 'btn primary lg', type: 'submit', text: t('login.go') }),
      tl ? h('div', { class: 'tip' }, h('div', {}, h('b', { text: t('login.training_users') }), ' ', tl.users.join(' · '), h('br'), t('password'), ': ', h('code', { class: 'ltr', text: tl.password }))) : null));
    user.focus();
  }

  SM.renderShell = function () {
    const u = SM.state.user;
    SM.state.dialogOpen = false;     // a new shell has no dialog: a stale flag would block every scan
    const nav = h('nav', { class: 'nav', 'aria-label': t('nav.label') }, NAV.map(([group, views]) => h('div', { class: 'nav-group' }, h('span', { text: t('nav.g.' + group) }),
      views.filter(allowed).map(v => h('a', { href: '#' + v, 'data-view': v }, icon(SM.views[v].icon), h('span', { text: t('nav.' + v) }))))));
    const side = h('aside', { class: 'sidebar' }, h('div', { class: 'brand' }, h('div', { class: 'mark' }, icon('cart')), h('div', { class: 'name' }, SM.state.settings.shop_name || t('app.name'), h('small', { text: t('app.tagline') }))),
      nav, h('div', { class: 'side-foot' }, h('div', { class: 'who' }, h('b', { text: u.name }), h('small', { text: t('role.' + u.role) })),
        h('button', { class: 'icon-btn', 'data-guide': 'shell.lock', 'aria-label': t('lock.btn'), title: t('lock.btn'), onclick: async () => { await SM.api('POST', '/api/account/lock', {}).catch(() => {}); boot(); } }, icon('lock')),
        h('button', { class: 'icon-btn', 'data-guide': 'shell.password', 'aria-label': t('account.change'), title: t('account.change'), onclick: () => SM.passwordDialog() }, icon('key')),
        h('button', { class: 'icon-btn logout-btn', 'aria-label': t('lock.switch'), title: t('lock.switch'), onclick: async () => { await SM.api('POST', '/api/logout', {}).catch(() => {}); SM.onLoggedOut(); } }, icon('out'))));
    const title = h('div', { class: 'crumbs' }, h('b', { id: 'view-title' }));
    const themeBtn = h('button', { class: 'icon-btn', 'aria-label': t('set.theme'), title: t('set.theme'), onclick: () => {
      const next = document.documentElement.dataset.theme === 'night' ? 'daylight' : 'night'; SM.prefs.set('theme', next); SM.applyPrefs(); } }, icon(document.documentElement.dataset.theme === 'night' ? 'sun' : 'moon'));
    const langBtn = h('button', { class: 'icon-btn', 'aria-label': 'language', title: SM.state.lang === 'ar' ? 'English' : 'العربية', onclick: () => {
      SM.prefs.set('lang', SM.state.lang === 'ar' ? 'en' : 'ar'); SM.applyPrefs(); SM.renderShell(); } }, icon('lang'));
    const helpBtn = h('button', { class: 'btn ghost sm', onclick: () => { SM.state.helpFor = SM.state.view; SM.go('help'); } }, icon('help', 'sm'), t('help.this_screen'));
    const menuBtn = h('button', { class: 'icon-btn menu-btn', 'aria-label': t('nav.label'), onclick: () => document.querySelector('.app').classList.toggle('nav-open') }, icon('list'));
    const top = h('header', { class: 'topbar' }, menuBtn, title, h('div', { class: 'grow' }), helpBtn, langBtn, themeBtn);
    const main = h('main', { class: 'main' },
      SM.state.mode === 'training' ? h('div', { class: 'train-bar' }, icon('school'), h('span', { class: 'grow', text: t('mode.training_bar') })) : null,
      top, h('div', { class: 'content', id: 'view' }));
    SM.put(document.body, h('div', { class: 'app' }, side, main));
    pendingBanner(main);
    backupBanner(main);
    route();
  };

  /* Audit S03: an operation whose answer was lost stays visible until resolved. */
  function pendingBanner(main) {
    const ops = SM.pendingOps();
    if (!ops.length) return;
    const bar = h('div', { class: 'tip warn-tip pending-bar', role: 'alert' }, icon('warn'),
      h('div', { class: 'grow' }, h('b', { text: t('pending.title', { n: ops.length }) }), ' ', t('pending.help')),
      h('button', { class: 'btn primary sm', text: t('pending.check'), onclick: async () => {
        const results = await SM.resolvePending();
        const done = results.filter(r => r.result).length;
        SM.toast(t('pending.resolved', { n: done }), done === results.length ? 'ok' : 'bad');
        if (!SM.pendingOps().length) bar.remove();
      } }));
    main.insertBefore(bar, main.querySelector('.topbar').nextSibling);
  }

  /* Review H5/M7: a copy that only lives on the shop PC is lost with the PC; the owner/manager sees it on every screen. */
  async function backupBanner(main) {
    if (SM.state.mode === 'training' || !SM.can('settings')) return;
    const st = await SM.api('GET', '/api/backup/status').catch(() => null);
    if (!st || !st.has_data) return;
    const lines = [];
    if (st.external_error) lines.push(t('bk.error'));
    else if (!st.configured && st.external_days === null) lines.push(t('bk.none'));
    else if (st.external_days !== null && st.external_days >= 3) lines.push(t('bk.stale', { n: st.external_days }));
    if (st.low_disk) lines.push(t('bk.disk', { mb: st.free_mb }));
    const top = main.querySelector('.topbar');
    if (!lines.length || !top || !document.body.contains(main)) return;
    top.parentNode.insertBefore(h('div', { class: 'tip warn-tip backup-bar', id: 'backup-bar', role: 'alert' }, icon('warn'), h('div', { class: 'grow' }, lines.map(x => h('div', { text: x })))), top.nextSibling);
  }

  async function route() {
    if (!SM.state.user || !document.getElementById('view')) return;
    let v = (location.hash || '#today').slice(1);
    if (!SM.views[v] || !allowed(v)) v = SM.can('sell') && !SM.can('reports') ? 'sell' : 'today';
    if (current && current.unmount) { try { current.unmount(); } catch (e) { /* ignore */ } }
    current = SM.views[v];
    SM.state.view = v;
    document.querySelectorAll('.nav a').forEach(a => { if (a.dataset.view === v) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
    document.getElementById('view-title').textContent = t('nav.' + v);
    document.querySelector('.app').classList.remove('nav-open');
    const root = document.getElementById('view');
    SM.put(root, h('div', { class: 'skeleton skeleton-block' }));
    const page = h('div', { class: 'page' }, h('div', { class: 'page-head' }, h('div', { class: 'titles' }, h('h1', { text: t('nav.' + v) }), h('p', { text: t('lead.' + v) }))));
    const body = h('div', { class: 'stack' });
    page.append(body);
    try { await current.mount(body); SM.put(root, page); }
    catch (e) { SM.put(root, page); SM.put(body, h('p', { class: 'tip', text: SM.errorText(e) })); }
  }
  window.addEventListener('hashchange', () => { route().then(() => { if (SM.guide) SM.guide.routeChanged(); }); });
  window.addEventListener('DOMContentLoaded', boot);
})();
