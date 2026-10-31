/* Core helpers: DOM builder, API client, money/weight formatting, toasts,
   dialogs, drawers, icons. No inline HTML from data: every text goes through
   textContent, so scanned or typed values can never become markup. */
'use strict';

const SM = window.SM = { state: { user: null, mode: 'shop', settings: {}, lang: 'ar', session: 0 }, draftResets: [] };

/* ---------- DOM ---------- */
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k === 'style') Object.assign(el.style, v);
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k === 'value') el.value = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {   // nested [dt, dd] pairs stay elements (audit R16)
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
SM.h = h;

const ICONS = {
  home: 'M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  key: 'M14 10a4 4 0 1 0-1.2 2.8L20 20M17 17l2-2M15 15l1.5-1.5',
  cart: 'M3 4h2l2.4 11h11l2-8H6.3M9 20h.01M18 20h.01',
  truck: 'M3 6h11v10H3zM14 10h4l3 3v3h-7M7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM17 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  box: 'M21 8l-9-5-9 5 9 5 9-5zM3 8v8l9 5 9-5V8M12 13v8',
  knife: 'M3 21l10-10M14 4l6 6-7 7-3-3z',
  pot: 'M4 10h16v6a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4zM2 10h20M9 6c0-1 1-2 3-2s3 1 3 2',
  list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  cash: 'M3 7h18v10H3zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 10v4M18 10v4',
  undo: 'M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  tag: 'M20 12l-8 8-9-9V3h8zM7.5 7.5h.01',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3.6 15H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.1-2.7l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 9.7 4.4V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1.3z',
  help: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01',
  scan: 'M3 7V4h3M21 7V4h-3M3 17v3h3M21 17v3h-3M7 8v8M10 8v8M13 8v8M17 8v8',
  moon: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  sun: 'M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4',
  out: 'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  x: 'M18 6L6 18M6 6l12 12',
  check: 'M20 6L9 17l-5-5',
  warn: 'M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
  print: 'M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M6 14h12v8H6z',
  plus: 'M12 5v14M5 12h14',
  minus: 'M5 12h14',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6',
  lang: 'M5 8l6 6M4 14l6-6 2-3M2 5h12M7 2h1M22 22l-5-10-5 10M14 18h6',
  school: 'M22 10L12 5 2 10l10 5 10-5zM6 12v5c3 3 9 3 12 0v-5',
};
function icon(name, cls) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'ic ' + (cls || ''));
  svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path');
  p.setAttribute('d', ICONS[name] || ICONS.help);
  svg.append(p);
  return svg;
}
SM.icon = icon;

/* ---------- i18n ---------- */
function t(key, vars) {
  const dict = SM.i18n[SM.state.lang] || SM.i18n.ar;
  let s = dict[key];
  if (s === undefined) s = (SM.i18n.en[key] !== undefined ? SM.i18n.en[key] : key);
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split('{' + k + '}').join(String(v));
  return s;
}
SM.t = t;
SM.name = (row) => (SM.state.lang === 'ar' ? row.name_ar : row.name_en) || row.name_ar || row.name_en || '';

/* ---------- numbers: integers only ---------- */
const AR_DIGITS = { '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9',
  '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9', '٫': '.', '،': ',' };
function asciiDigits(s) { return String(s).replace(/[٠-٩۰-۹٫،]/g, c => AR_DIGITS[c]); }
/* "12.5", "12,5" or "١٢٫٥" -> 1250 piastres; null when not a clean amount. Never uses float.
   One separator (. , ، ٫) is the decimal point - in Egypt "2,5" means two and a half, never 25.
   Two separators or more are refused rather than guessed ("1,000.5" -> null). */
function parseScaled(text, decimals) {
  const s = asciiDigits(text).trim().replace(/\s+/g, '').replace(/,/g, '.');
  if ((s.match(/\./g) || []).length > 1) return null;
  const m = /^(\d{1,9})(?:\.(\d*))?$/.exec(s);
  if (!m) return null;
  const frac = (m[2] || '');
  if (frac.length > decimals) return null;
  return parseInt(m[1], 10) * Math.pow(10, decimals) + (frac ? parseInt(frac.padEnd(decimals, '0'), 10) : 0);
}
SM.parseMoney = (s) => parseScaled(s, 2);
SM.parseKg = (s) => parseScaled(s, 3);            // kilograms text -> grams
SM.parseInt = (s) => { const v = parseScaled(s, 0); return v === null ? null : v; };
function scaled(n, decimals) {
  if (n === null || n === undefined) return '—';
  const neg = n < 0; n = Math.abs(n);
  const base = Math.pow(10, decimals);
  const whole = Math.floor(n / base), frac = String(n % base).padStart(decimals, '0');
  return (neg ? '-' : '') + whole.toLocaleString('en-US') + (decimals ? '.' + frac : '');
}
SM.money = (p) => scaled(p, 2);
SM.moneyText = (p) => scaled(p, 2) + ' ' + t('currency');
SM.qty = (q, measure) => measure === 'g' ? scaled(q, 3) + ' ' + t('unit.kg') : scaled(q, 0) + ' ' + t('unit.pc');
SM.num = (text) => h('span', { class: 'num', text });

/* ---------- API ---------- */
SM.newKey = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '') :
  Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join(''));
class ApiError extends Error {
  constructor(status, body) { super(body && body.error || 'network'); this.status = status; this.code = body && body.error || 'network'; this.detail = body && body.detail || {}; }
}
SM.ApiError = ApiError;
SM.api = async function (method, path, body) {
  let resp;
  try {
    resp = await fetch(path, { method, credentials: 'same-origin', headers: method === 'POST' ? { 'Content-Type': 'application/json', 'X-SM': '1' } : {},
      body: method === 'POST' ? JSON.stringify(body || {}) : undefined });
  } catch (e) { throw new ApiError(0, { error: 'network' }); }
  const data = await resp.json().catch(() => ({ error: 'bad_reply' }));
  if (!resp.ok) {
    if (resp.status === 401 && data.error === 'login_required' && SM.onLoggedOut && SM.state.user) {
      SM.state.sessionEnded = true;     // report L1: the sign-in screen says why it is back
      SM.onLoggedOut();                  // one departure only; a second 401 must not rebuild the screen and hide the message
    }
    // the server refuses everything on a locked till or before a forced password change: show that screen
    if ((data.error === 'screen_locked' || data.error === 'must_change_password') && SM.onAccountGate) SM.onAccountGate(data.error);
    throw new ApiError(resp.status, data);
  }
  return data;
};
/* A command keeps its key across retries: a lost reply never sells twice.
   The caller keeps `key` for the whole attempt (including approval retries). */
/* Audit S03: an operation whose answer was lost keeps its key until the
   server's answer is known - across closed dialogs, double clicks and page
   reloads. It is stored per user + till + mode, without any password. */
SM.pendingSlot = () => 'pending.' + (SM.state.user ? SM.state.user.id : 0) + '.' + SM.workstation() + '.' + SM.state.mode;
SM.pendingOps = () => SM.prefs.get(SM.pendingSlot(), []);
function savePending(list) { SM.prefs.set(SM.pendingSlot(), list); }
SM.command = async function (path, key, payload, extra, tries = 3) {
  const body = Object.assign({ key, payload }, extra || {});
  const safeExtra = Object.assign({}, extra || {}); delete safeExtra.approval;
  savePending(SM.pendingOps().filter(p => p.key !== key).concat([{ path, key, payload, extra: safeExtra, at: Date.now() }]));
  for (let i = 0; ; i++) {
    try {
      const r = await SM.api('POST', path, body);
      savePending(SM.pendingOps().filter(p => p.key !== key));
      if (SM.guide) SM.guide.signal(path);   // the guide's coach moves on only after the server confirmed
      return r;
    } catch (e) {
      if (e.code === 'network' && i < tries - 1) { await new Promise(r => setTimeout(r, 600 * (i + 1))); continue; }
      if (e.code !== 'network' && e.status !== 0 && e.status < 500) savePending(SM.pendingOps().filter(p => p.key !== key));
      if (e.code === 'network') e.unknownResult = true;   // saved or not: unknown until resent with this key
      throw e;
    }
  }
};
/* Resend every unknown operation with its own key: the server answers with the
   stored result if it was saved, or performs it once if it was not. */
SM.resolvePending = async function () {
  const out = [];
  for (const p of SM.pendingOps()) {
    try { out.push({ op: p, result: await SM.command(p.path, p.key, p.payload, p.extra, 1) }); }
    catch (e) { out.push({ op: p, error: e }); }
  }
  return out;
};
/* Run a command; when the server asks for a manager, ask, then resend with the same key. */
SM.commandWithApproval = async function (path, key, payload) {
  let approval = null;
  for (;;) {
    try { return await SM.command(path, key, payload, approval ? { approval } : null); }
    catch (e) {
      if (e.status === 403 && (e.code === 'approval_required' || e.code === 'forbidden' || e.code === 'approver_lacks_permission')) {
        approval = await SM.askApproval(SM.t('perm.' + (e.detail.permission || 'generic')));
        if (approval) continue;
      }
      throw e;
    }
  }
};
/* Error text = reason + whether the operation was saved + (for support) the technical code.
   A refusal (4xx) did not save anything; a lost connection or a server error may have. */
SM.errorText = (e) => {
  const code = e && e.code || 'internal_error';
  const k = 'err.' + code;
  const msg = t(k);
  const reason = msg === k ? t('err.generic', { code }) : t(k, e.detail || {});
  if (code === 'network') return reason;                       // its own text already says "may be saved"
  if (code === 'login_required' || code === 'bad_login' || code === 'account_locked' || code === 'too_many_attempts') return reason;
  const saved = e && e.status && e.status < 500 ? t('err.suffix_not_saved') : t('err.suffix_unknown');
  return reason + ' ' + saved;
};
SM.errorCode = (e) => (e && e.code) || 'internal_error';
/* Replace {b:key} with the REAL label of that button/field, so a step can never name a button that does not exist. */
SM.fill = (text) => String(text).replace(/\{b:([a-z_0-9.]+)\}/g, (m, key) => t(key));

/* ---------- toasts, dialogs, drawers ---------- */
SM.toast = function (text, kind = 'ok', extra) {
  let box = document.getElementById('toasts');
  if (!box) { box = h('div', { id: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
  const el = h('div', { class: 'toast ' + kind }, icon(kind === 'bad' ? 'warn' : 'check'), h('span', { text }), extra || null);
  box.append(el);
  setTimeout(() => el.remove(), kind === 'bad' ? 7000 : 3500);
};
// an error with an explanation in the guide gets «ماذا أفعل؟» (same as the Yousef app)
SM.fail = (e) => SM.toast(SM.errorText(e), 'bad', SM.guide && SM.guide.errorButton(SM.errorCode ? SM.errorCode(e) : e && e.code));

SM.dialog = function ({ title, body, actions, wide, onClose }) {
  let overlay = document.getElementById('overlay');
  if (!overlay) { overlay = h('div', { id: 'overlay' }); document.body.append(overlay); }
  const previous = document.activeElement;
  const close = () => { overlay.classList.remove('on'); SM.put(overlay); SM.state.dialogOpen = false; if (onClose) onClose(); if (previous && previous.focus) previous.focus(); };
  const dlg = h('div', { class: 'dialog' + (wide ? ' wide' : ''), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('header', {}, h('h2', { text: title }), h('button', { class: 'icon-btn', 'aria-label': t('close'), onclick: close }, icon('x'))),
    h('div', { class: 'body' }, body),
    actions ? h('footer', {}, actions) : null);
  SM.put(overlay, dlg);
  overlay.classList.add('on', 'center');
  overlay.onclick = (ev) => { if (ev.target === overlay) close(); };
  dlg.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } });
  SM.state.dialogOpen = true;
  setTimeout(() => { const f = dlg.querySelector('[autofocus], input, select, button.primary'); if (f) f.focus(); }, 30);
  return { close, el: dlg };
};

SM.drawer = function ({ title, body, actions }) {
  let host = document.getElementById('panels');
  if (!host) { host = h('div', { id: 'panels' }); document.body.append(host); }
  const scrim = h('div', { class: 'drawer-scrim' });
  const close = () => { panel.classList.remove('on'); scrim.classList.remove('on'); setTimeout(() => { SM.put(host); }, 220); };
  const panel = h('aside', { class: 'drawer', role: 'dialog', 'aria-label': title },
    h('header', { class: 'drawer-head' }, h('h2', { text: title }), h('button', { class: 'icon-btn', 'aria-label': t('close'), onclick: close }, icon('x'))),
    h('div', { class: 'drawer-body' }, body),
    actions ? h('footer', { class: 'drawer-foot' }, actions) : null);
  scrim.onclick = close;
  panel.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); });
  SM.put(host, scrim, panel);
  requestAnimationFrame(() => { scrim.classList.add('on'); panel.classList.add('on'); });
  return { close };
};

/* One question in the program's own window, on its own layer above any open window (review report M9:
   no browser prompt()). Resolves the trimmed answer, or null on cancel. An empty answer is not accepted. */
SM.ask = (question, opts = {}) => new Promise((resolve) => {
  let layer = document.getElementById('ask-layer');
  if (!layer) { layer = h('div', { id: 'ask-layer' }); document.body.append(layer); }
  const input = h('input', { class: 'input big', autocomplete: 'off', id: 'ask-input', inputmode: opts.inputmode || 'text', value: opts.value || '' });
  const note = h('p', { class: 'field-error', role: 'alert' });
  let done = false;
  const onKey = (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); finish(null); } };
  const finish = (v) => { if (done) return; done = true; window.removeEventListener('keydown', onKey, true); layer.classList.remove('on'); SM.put(layer); resolve(v); };
  window.addEventListener('keydown', onKey, true);
  const ok = () => { const v = input.value.trim(); if (!v) { note.textContent = t(opts.empty || 'ask.empty'); input.focus(); return; } finish(v); };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ok(); } });
  const dlg = h('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title || question },
    h('header', {}, h('h2', { text: opts.title || t('ask.title') }), h('button', { class: 'icon-btn', 'aria-label': t('close'), onclick: () => finish(null) }, icon('x'))),
    h('div', { class: 'body' }, h('label', { class: 'field' }, h('span', { class: 'label', text: question }), input), opts.help ? h('p', { class: 'muted', text: opts.help }) : null, note),
    h('footer', {}, h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => finish(null) }), h('button', { class: 'btn primary', id: 'ask-ok', text: opts.ok || t('confirm'), onclick: ok })));
  SM.put(layer, dlg);
  layer.classList.add('on');
  layer.onclick = (ev) => { if (ev.target === layer) finish(null); };
  input.focus();
});

/* Yes/no on its own layer above any open window (review PR #18): cancelling never loses the form underneath. */
SM.confirm = (title, text) => new Promise((resolve) => {
  let layer = document.getElementById('ask-layer');
  if (!layer) { layer = h('div', { id: 'ask-layer' }); document.body.append(layer); }
  const previous = document.activeElement;
  let done = false;
  // Escape belongs to the top window while it is open, wherever the focus is (never to the window under it)
  const onKey = (ev) => { if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); finish(false); } };
  const finish = (v) => { if (done) return; done = true; window.removeEventListener('keydown', onKey, true); layer.classList.remove('on'); SM.put(layer); if (previous && previous.focus) previous.focus(); resolve(v); };
  window.addEventListener('keydown', onKey, true);
  const yes = h('button', { class: 'btn primary', id: 'confirm-yes', text: t('confirm'), onclick: () => finish(true) });
  const dlg = h('div', { class: 'dialog', role: 'alertdialog', 'aria-modal': 'true', 'aria-label': title },
    h('header', {}, h('h2', { text: title }), h('button', { class: 'icon-btn', 'aria-label': t('close'), onclick: () => finish(false) }, icon('x'))),
    h('div', { class: 'body' }, h('p', { text })),
    h('footer', {}, h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => finish(false) }), yes));
  SM.put(layer, dlg);
  layer.classList.add('on');
  layer.onclick = (ev) => { if (ev.target === layer) finish(false); };
  yes.focus();
});

/* A simple random temporary password (no look-alike letters), shown to the owner so he can read it out: one click, no thinking. */
SM.tempPassword = () => {
  const alphabet = 'abcdefghjkmnpqrstuvwxyz23456789', bytes = new Uint8Array(10);
  (window.crypto || window.msCrypto).getRandomValues(bytes);
  return Array.from(bytes, b => alphabet[b % alphabet.length]).join('');
};

/* Manager approval typed by a person: username + password. A scan can never fill it. */
SM.askApproval = (reason) => new Promise((resolve) => {
  const user = h('input', { class: 'input', autocomplete: 'off', 'aria-label': t('username') });
  const pass = h('input', { class: 'input', type: 'password', autocomplete: 'off', 'aria-label': t('password') });
  let done = false;
  const ok = () => { if (!user.value || !pass.value) return; done = true; d.close(); resolve({ username: user.value, password: pass.value }); };
  pass.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); } });
  const d = SM.dialog({ title: t('approval.title'), onClose: () => { if (!done) resolve(null); },
    body: [h('p', { class: 'tip', text: t('approval.why', { reason }) }),
      field(t('username'), user), field(t('password'), pass), h('p', { class: 'muted', text: t('approval.hint') })],
    actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }),
      h('button', { class: 'btn primary', text: t('approval.ok'), onclick: ok })] });
});

function field(label, input, help) {
  const id = 'f' + Math.random().toString(36).slice(2, 8);
  input.id = input.id || id;
  return h('div', { class: 'field' }, h('label', { for: input.id, text: label }), input, help ? h('div', { class: 'help', text: help }) : null);
}
SM.field = field;

SM.badge = (state) => {
  const map = { available: 'ok', near_expiry: 'warn', expired: 'bad', held: 'warn', recalled: 'bad', expiry_missing: 'bad', released: 'ok',
    pending: 'warn', settled: 'ok', open: 'info', approved: 'ok', quarantine: 'warn', restock: 'ok' };
  const ic = { ok: 'check', warn: 'warn', bad: 'x', info: 'help' }[map[state] || 'info'];
  return h('span', { class: 'badge ' + (map[state] || 'info') }, icon(ic, 'sm'), t('state.' + state));
};

SM.table = function (cols, rows, onRow) {
  const thead = h('thead', {}, h('tr', {}, cols.map(c => h('th', { text: c.label, class: c.num ? 'n' : '' }))));
  const tbody = h('tbody', {}, rows.map((r, i) => {
    const tr = h('tr', { style: { '--i': i } }, cols.map(c => h('td', { class: c.num ? 'n' : '' }, c.render ? c.render(r) : (r[c.key] ?? ''))));
    if (onRow) { tr.tabIndex = 0; tr.classList.add('clickable'); tr.onclick = () => onRow(r); tr.onkeydown = (e) => { if (e.key === 'Enter') onRow(r); }; }
    return tr;
  }));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'tbl' }, thead, tbody));
};

SM.empty = (title, text) => h('div', { class: 'empty' }, h('div', { class: 'art' }, icon('box', 'lg')), h('h3', { text: title }), text ? h('p', { text }) : null);
SM.can = (perm) => !!(SM.state.user && SM.state.user.permissions.includes(perm));
SM.today = () => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };

/* ---------- preferences (per browser; not business data) ---------- */
SM.prefs = {
  get(k, d) { try { const v = localStorage.getItem('sm.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('sm.' + k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};
SM.workstation = () => {
  let w = SM.prefs.get('workstation', null);
  if (!w) { w = 'till-' + SM.newKey().slice(0, 6); SM.prefs.set('workstation', w); }
  return w;
};
SM.applyPrefs = () => {
  const root = document.documentElement;
  SM.state.lang = SM.prefs.get('lang', 'ar');
  root.lang = SM.state.lang; root.dir = SM.state.lang === 'ar' ? 'rtl' : 'ltr';
  const theme = SM.prefs.get('theme', window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'night' : 'daylight');
  root.dataset.theme = theme;
  root.style.fontSize = SM.prefs.get('size', 100) + '%';
};
/* replaceChildren that skips null/false placeholders used for optional parts. */
SM.put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false));
/* Audit S03: a button that runs a command owns ONE key for its form; double
   clicks are ignored while a request is in flight, and a retry after a lost
   answer re-sends the same key (the server returns the saved result). */
SM.guard = function (fn) {
  let busy = false;
  return async function (ev) {
    if (busy) return;
    busy = true;
    const btn = ev && ev.currentTarget;
    if (btn) btn.disabled = true;
    try { await fn(ev); } finally { busy = false; if (btn && btn.isConnected) btn.disabled = false; }
  };
};
