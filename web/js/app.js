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
    // quantities on hand: a pure counter counts blind (server: Shop.can_see_qty), so the page is not offered to him
    stock: ['reports', 'receive', 'transfer', 'waste', 'cutting', 'produce', 'sell'],
    reports: 'reports', scanner: 'scanner_setup', parties: ['receive', 'supplier_pay', 'sell'] };
  const allowed = (v) => { const p = PERM[v]; return !p || (Array.isArray(p) ? p.some(SM.can) : SM.can(p)); };
  let current = null;

  SM.go = (view) => { if (location.hash === '#' + view) route(); else location.hash = view; };

  async function boot() {
    SM.applyPrefs();
    let state;
    try { state = await SM.api('GET', '/api/state'); } catch (e) { SM.put(document.body, h('p', { class: 'empty', text: t('err.server_down') })); return; }
    SM.state.mode = state.mode; SM.state.instance = state.instance; SM.state.businessDay = state.business_day; SM.state.version = state.version; SM.state.trainingLogin = state.training_login; SM.state.lanAddresses = state.lan_addresses;
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

  function setAuthTitle(title, sub) {
    const h1 = document.getElementById('auth-title');
    const p = document.getElementById('auth-sub');
    if (h1) h1.textContent = title;
    if (p) p.textContent = sub || '';
  }

  function initials(name) {
    const parts = String(name || '').replace(/[()]/g, ' ').split(/\s+/).filter(Boolean);
    return parts[0] ? Array.from(parts[0])[0] : '؟';
  }

  function stayError() { return h('p', { class: 'field-error', role: 'alert' }); }

  let pinKeys = null;
  /* Large digit pad. The red note sits under the dots and stays until the next digit. It is not a toast. */
  function pinPad(digits, hint, onFull, onType) {
    let value = '';
    let locked = false;
    const dots = h('div', { class: 'pin-dots', 'aria-hidden': 'true' });
    const err = stayError();
    const onKey = (ev) => {
      if (!document.body.contains(dots)) { document.removeEventListener('keydown', onKey); if (pinKeys === onKey) pinKeys = null; return; }
      if (ev.target && ev.target.closest && ev.target.closest('input, textarea')) return;
      const typed = SM.asciiPinKeys(ev.key);
      if (typed) { ev.preventDefault(); for (const d of typed) push(d); }
      else if (ev.key === 'Backspace') { ev.preventDefault(); if (!locked) { value = value.slice(0, -1); paint(); } }
    };
    if (onFull) {
      if (pinKeys) document.removeEventListener('keydown', pinKeys);
      pinKeys = onKey;
      document.addEventListener('keydown', onKey);
    }
    function paint() {
      SM.put(dots, Array.from({ length: digits }, (_, i) => h('span', { class: 'pin-dot' + (i < value.length ? ' on' : '') })));
    }
    function push(d) {
      if (locked || value.length >= digits) return;
      err.textContent = '';
      if (onType) onType();
      value += d;
      paint();
      if (value.length === digits && onFull) onFull(value);
    }
    paint();
    const grid = h('div', { class: 'pin-pad', role: 'group', 'aria-label': t('set.pin') },
      ['1', '2', '3', '4', '5', '6', '7', '8', '9'].map(d => h('button', { type: 'button', class: 'pin-key', 'data-digit': d, text: d, onclick: () => push(d) })),
      h('button', { type: 'button', class: 'pin-key pin-ghost', text: t('login.pin_clear'), onclick: () => { value = ''; err.textContent = ''; paint(); } }),
      h('button', { type: 'button', class: 'pin-key', 'data-digit': '0', text: '0', onclick: () => push('0') }),
      h('button', { type: 'button', class: 'pin-key pin-ghost', 'aria-label': t('login.pin_backspace'), text: '⌫', onclick: () => { value = value.slice(0, -1); paint(); } }));
    return { el: h('div', { class: 'pin-block' }, hint ? h('p', { class: 'pin-hint', text: hint }) : null, dots, err, grid),
      value: () => value,
      setError(msg, freeze) {
        err.textContent = msg || '';
        value = '';
        paint();
        if (freeze) {
          locked = true;
          grid.querySelectorAll('button').forEach(b => { b.disabled = true; });
        }
      } };
  }

  function pinHint(digits) { return t(digits === 6 ? 'login.pin_hint_6' : 'login.pin_hint_4'); }

  /* Locked till: the same person's PIN or password opens it; "switch user" logs out. */

  /* Review M5: the PC date is behind a closed day: say so plainly instead of refusing every sale with a vague error. */
  function clockScreen(state) {
    const c = state.clock_behind;
    authFrame(t('clock.title'), t('clock.body', { today: '\u2066' + c.today + '\u2069', closed: '\u2066' + c.closed + '\u2069' }),
      h('button', { class: 'btn primary lg', id: 'clock-retry', text: t('clock.retry'), onclick: () => boot() }));
  }

  function lockScreen(state) {
    const u = state.user;
    const note = stayError();
    const leave = h('button', { class: 'btn ghost', type: 'button', text: t('lock.switch'), onclick: async () => { await SM.api('POST', '/api/logout', {}).catch(() => {}); SM.onLoggedOut(); } });
    SM.scanner.handler = null;
    const passwordForm = () => {
      const pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
      const form = h('form', { class: 'stack', onsubmit: async (e) => {
        e.preventDefault();
        note.textContent = '';
        try { await SM.api('POST', '/api/account/unlock', { password: pass.value }); boot(); }
        catch (err) { pass.value = ''; note.textContent = SM.errorText(err); }
      } }, SM.field(t('password'), pass), note, h('button', { class: 'btn primary lg', id: 'login-go', type: 'submit', text: t('lock.open') }), leave);
      return form;
    };
    let body;
    if (u.has_pin) {
      const pad = pinPad(u.pin_digits, '', async (pin) => {
        try { await SM.api('POST', '/api/account/unlock', { pin }); boot(); }
        catch (err) { pad.setError(SM.errorText(err), err.code === 'account_locked' || err.code === 'too_many_attempts'); }
      });
      const swap = h('button', { class: 'btn ghost', type: 'button', id: 'login-password', text: t('login.use_password'), onclick: () => {
        setAuthTitle(t('lock.title', { name: u.name }), t('lock.sub'));
        const card = document.querySelector('.auth-card');
        const form = passwordForm();
        const old = card.querySelector('.stack');
        if (old) old.replaceWith(form); else card.append(form);
        form.querySelector('input').focus();
      } });
      body = h('div', { class: 'stack' }, pad.el, (u.role === 'owner' || u.role === 'manager') ? swap : h('p', { class: 'muted', text: t('login.forgot_pin_hint') }), leave);
    } else body = passwordForm();
    authFrame(t('lock.title', { name: u.name }), u.has_pin ? pinHint(u.pin_digits) : t('lock.sub'), body);
    const input = document.querySelector('.auth-card input');
    if (input) input.focus();
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

  /* «نسيت كلمة المرور؟»: one plain answer for everyone, reachable from the login screen and from the help page. */
  SM.forgotDialog = () => { const d = SM.dialog({ title: t('forgot.title'), actions: [h('button', { class: 'btn primary', id: 'forgot-ok', text: t('forgot.ok'), onclick: () => d.close() })], body: [
    h('h4', { text: t('forgot.staff_title') }), h('p', { text: t('forgot.staff') }),
    h('h4', { text: t('forgot.owner_title') }), h('ol', { class: 'steps' }, SM.fill(t('forgot.owner_steps')).split('|').filter(Boolean).map(x => h('li', { text: x }))),
    h('h4', { text: t('forgot.wrong_title') }), h('p', { text: t('forgot.wrong') })] }); return d; };
  SM.lockNow = async () => { await SM.api('POST', '/api/account/lock', {}).catch(() => {}); boot(); };

  SM.scanner.installGlobalOnce = function () { if (!this._installed) { this._installed = true; this.installGlobal(); } };

  function authFrame(title, sub, form) {
    SM.put(document.body, h('main', { class: 'auth-wrap' },
      SM.state.mode === 'training' ? h('div', { class: 'train-bar' }, icon('school'), h('span', { text: t('mode.training_bar') })) : null,
      h('div', { class: 'card auth-card' }, h('div', { class: 'brand-mark' }, icon('cart', 'lg')), h('h1', { id: 'auth-title', text: title }), h('p', { class: 'muted', id: 'auth-sub', text: sub }), form,
        h('div', { class: 'row auth-foot' }, h('button', { class: 'btn ghost sm', onclick: () => { SM.prefs.set('lang', SM.state.lang === 'ar' ? 'en' : 'ar'); SM.applyPrefs(); boot(); } }, icon('lang', 'sm'), SM.state.lang === 'ar' ? 'English' : 'العربية')))));
  }

  function setupScreen() {
    const shop = h('input', { class: 'input' }), user = h('input', { class: 'input', autocomplete: 'username', value: 'owner' }), name = h('input', { class: 'input' });
    const pass = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' }), pass2 = h('input', { class: 'input', type: 'password', autocomplete: 'new-password' });
    authFrame(t('setup.title'), t('setup.sub'), h('form', { class: 'stack', onsubmit: async (e) => {
      e.preventDefault();
      // report L4: say which box is empty and put the cursor there
      const empty = [[shop, 'setup.need_shop'], [user, 'setup.need_user'], [pass, 'setup.need_pass']].find(([el]) => !el.value.trim());
      if (empty) { empty[0].focus(); return SM.toast(t(empty[1]), 'bad'); }
      if (pass.value !== pass2.value) return SM.toast(t('setup.mismatch'), 'bad');
      try { await SM.api('POST', '/api/setup', { shop_name: shop.value, username: user.value, name: name.value || user.value, password: pass.value }); SM.toast(t('setup.done')); boot(); } catch (err) { SM.fail(err); }
    } }, SM.field(t('set.shop_name'), shop), SM.field(t('setup.owner_name'), name), SM.field(t('username'), user), SM.field(t('password'), pass, t('set.pw_help')),
      SM.field(t('setup.repeat'), pass2), h('p', { class: 'tip', text: t('setup.tip') }), h('button', { class: 'btn primary lg', type: 'submit', text: t('setup.go') })));
  }

  function loginScreen(state) {
    const tl = state.training_login;
    const ended = SM.state.sessionEnded ? h('p', { class: 'tip', id: 'session-ended', role: 'status', text: t('login.session_ended') }) : null;
    SM.state.sessionEnded = false;
    const host = h('div', { class: 'stack', id: 'login-host' });
    const forgot = () => h('button', { class: 'btn ghost', type: 'button', id: 'login-forgot', text: t('login.forgot'), onclick: () => SM.forgotDialog() });
    const trainingTip = tl && tl.people ? h('div', { class: 'tip', id: 'training-pins' },
      h('div', { class: 'stack' }, h('b', { text: t('login.training_pins') }),
        tl.people.map(p => h('div', {}, p.name, ' ', h('b', { class: 'num', text: p.pin }))))) : null;
    let people = [];
    authFrame(t('login.who'), t('login.who_sub'), host);

    function personButton(p) {
      return h('button', {
        type: 'button', class: 'who-btn', 'data-username': p.username, 'data-role': p.role, 'data-user-id': String(p.id),
        onclick: () => (p.has_pin ? showPin(p) : showPassword(p)),
      }, h('span', { class: 'who-av', 'aria-hidden': 'true', text: initials(p.name) }),
        h('span', { class: 'who-name' }, h('b', { text: p.name }), h('small', { text: t('role.' + p.role) })));
    }
    function showWho() {
      setAuthTitle(t('login.who'), t('login.who_sub'));
      const list = h('div', { class: 'who-list' });
      const search = people.length > 12 ? h('input', { class: 'input', id: 'who-search', autocomplete: 'off', placeholder: t('login.search') }) : null;
      const draw = () => SM.put(list, SM.filterPeople(people, search ? search.value : '').map(personButton));
      if (search) search.addEventListener('input', draw);
      draw();
      SM.put(host, ended, trainingTip, search, list, forgot());
    }

    function showPassword(p) {
      setAuthTitle(p.name, p.has_pin ? t('login.password_for') : t('login.no_pin'));
      const pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', autofocus: true });
      const note = stayError();
      const form = h('form', { class: 'stack', onsubmit: async (e) => {
        e.preventDefault();
        note.textContent = '';
        try { await SM.api('POST', '/api/login', { user_id: p.id, password: pass.value, workstation: SM.workstation() }); boot(); }
        catch (err) { pass.value = ''; note.textContent = SM.errorText(err); }
      } }, SM.field(t('password'), pass), note,
        h('button', { class: 'btn primary lg', id: 'login-go', type: 'submit', text: t('login.go') }),
        p.has_pin ? h('button', { class: 'btn ghost', type: 'button', text: t('login.use_pin'), onclick: () => showPin(p) }) : null,
        h('button', { class: 'btn ghost', type: 'button', text: t('login.back'), onclick: () => showWho() }),
        forgot());
      SM.put(host, form);
      pass.focus();
    }

    function showPin(p) {
      setAuthTitle(p.name, pinHint(p.pin_digits));
      const pad = pinPad(p.pin_digits, '', async (pin) => {
        try { await SM.api('POST', '/api/login', { user_id: p.id, pin, workstation: SM.workstation() }); boot(); }
        catch (err) { pad.setError(SM.errorText(err), err.code === 'account_locked' || err.code === 'too_many_attempts'); }
      });
      const passLink = (p.role === 'owner' || p.role === 'manager')
        ? h('button', { class: 'btn ghost', type: 'button', id: 'login-password', text: t('login.use_password'), onclick: () => showPassword(p) })
        : h('p', { class: 'muted', text: t('login.forgot_pin_hint') });
      SM.put(host, pad.el, passLink,
        h('button', { class: 'btn ghost', type: 'button', text: t('login.back'), onclick: () => showWho() }));
    }

    function showClassic() {
      const user = h('input', { class: 'input', autocomplete: 'username' }), pass = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
      const note = stayError();
      setAuthTitle(state.shop_name || t('app.name'), t('login.sub'));
      SM.put(host, h('form', { class: 'stack', onsubmit: async (e) => {
        e.preventDefault();
        note.textContent = '';
        try { await SM.api('POST', '/api/login', { username: user.value, password: pass.value, workstation: SM.workstation() }); boot(); }
        catch (err) { note.textContent = SM.errorText(err); }
      } }, SM.field(t('username'), user), SM.field(t('password'), pass), note,
        h('button', { class: 'btn primary lg', id: 'login-go', type: 'submit', text: t('login.go') }), forgot(), trainingTip));
    }

    SM.api('GET', '/api/login/people').then(rows => { people = rows || []; if (!people.length) showClassic(); else showWho(); }).catch(() => showClassic());
  }

  SM.resetPinDialog = (user) => {
    const digits = user.role === 'owner' || user.role === 'manager' ? 6 : 4;
    const pad = pinPad(digits, t(digits === 6 ? 'set.pin_help_6' : 'set.pin_help_4'));
    pad.el.setAttribute('data-guide', 'set.pin_new');
    const d = SM.dialog({ title: t('set.pin_title', { name: user.display_name }), body: [pad.el],
      actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }),
        h('button', { class: 'btn primary', id: 'pin-save', text: t('set.pin_save'), onclick: async () => {
          try { await SM.api('POST', '/api/users/' + user.id + '/pin', { pin: pad.value() }); d.close(); SM.toast(t('set.pin_done')); SM.go('settings'); }
          catch (e) { pad.setError(SM.errorText(e)); }
        } })] });
    return d;
  };

  SM.ownPinDialog = () => {
    const u = SM.state.user;
    const digits = u.pin_digits || (u.role === 'owner' || u.role === 'manager' ? 6 : 4);
    const note = stayError();
    const clearNote = () => { note.textContent = ''; };
    const fresh = pinPad(digits, pinHint(digits), null, clearNote);
    let usePin = !!u.has_pin;
    let step = 1;
    const oldPw = h('input', { class: 'input', type: 'password', autocomplete: 'current-password' });
    oldPw.addEventListener('input', clearNote);
    const oldPad = pinPad(digits, t('account.pin_old_pin'), null, clearNote);
    const body = h('div', { id: 'own-pin-body' });
    const swap = h('button', { class: 'btn ghost', type: 'button', id: 'own-pin-swap', text: t('login.use_password'), onclick: () => {
      usePin = !usePin;
      swap.textContent = usePin ? t('login.use_password') : t('login.use_pin');
      paint();
    } });
    const primary = h('button', { class: 'btn primary', id: 'own-pin-save', type: 'button', text: t('login.next'), onclick: async () => {
      note.textContent = '';
      if (step === 1) { step = 2; paint(); return; }
      const payload = { pin: fresh.value() };
      if (usePin) payload.old_pin = oldPad.value(); else payload.old = oldPw.value;
      try { await SM.api('POST', '/api/account/pin', payload); d.close(); SM.toast(t('account.pin_saved')); boot(); }
      catch (e) { note.textContent = SM.errorText(e); }
    } });
    function paint() {
      primary.textContent = step === 1 ? t('login.next') : t('set.pin_save');
      swap.hidden = step !== 1;
      const cred = usePin ? oldPad.el : SM.field(t('account.pin_old'), oldPw);
      SM.put(body, step === 1 ? cred : fresh.el, note);
    }
    paint();
    const d = SM.dialog({ title: t('account.pin'), body: [body],
      actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }), u.has_pin ? swap : null, primary] });
    return d;
  };

  SM.renderShell = function () {
    const u = SM.state.user;
    SM.state.dialogOpen = false;     // a new shell has no dialog: a stale flag would block every scan
    const nav = h('nav', { class: 'nav', 'aria-label': t('nav.label') }, NAV.map(([group, views]) => h('div', { class: 'nav-group' }, h('span', { text: t('nav.g.' + group) }),
      views.filter(allowed).map(v => h('a', { href: '#' + v, 'data-view': v }, icon(SM.views[v].icon), h('span', { text: t('nav.' + v) }))))));
    const side = h('aside', { class: 'sidebar' }, h('div', { class: 'brand' }, h('div', { class: 'mark' }, icon('cart')), h('div', { class: 'name' }, SM.state.settings.shop_name || t('app.name'), h('small', { text: t('app.tagline') }))),
      nav, h('div', { class: 'side-foot' }, h('div', { class: 'who' }, h('b', { text: u.name }), h('small', { text: t('role.' + u.role) })),
        h('button', { class: 'icon-btn', 'data-guide': 'shell.lock', 'aria-label': t('lock.btn'), title: t('lock.btn'), onclick: () => SM.lockNow() }, icon('lock')),
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
    pinPrompt(main);
    route();
  };

  /* After an upgrade, old accounts have no PIN. The owner sees one bar until they set them. */
  async function pinPrompt(main) {
    if (SM.state.mode === 'training' || !SM.can('users')) return;
    const users = await SM.api('GET', '/api/users').catch(() => []);
    const missing = (users || []).filter(u => u.active && !u.has_pin);
    const top = main.querySelector('.topbar');
    if (!missing.length || !top || !document.body.contains(main)) return;
    top.parentNode.insertBefore(h('div', { class: 'tip warn-tip', id: 'pin-prompt', role: 'status' },
      h('div', { class: 'grow', text: t('login.pin_prompt', { n: missing.length }) }),
      h('button', { class: 'btn primary sm', id: 'pin-prompt-go', text: t('set.pin_btn'), onclick: () => SM.go('settings') })), top.nextSibling);
  }

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
    // a side panel of the page being left (an item's details...) must not stay over the next page
    const panels = document.getElementById('panels');
    if (panels) SM.put(panels);
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
