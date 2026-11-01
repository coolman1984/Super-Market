/* ISSUE-003: a card or transfer row with an empty amount must not be saved as cash.
   ISSUE-002: the sale pay screen offers InstaPay on the transfer button.
   Run: node tests/ui/pay_empty.js */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class El {
  constructor(tag = 'div', text = '') {
    this.tag = tag;
    this.id = '';
    this.className = '';
    this._text = text;
    this.children = [];
    this.parent = null;
    this.style = {};
    this.value = '';
    this.disabled = false;
    this.tabIndex = 0;
    this.attributes = {};
    this._listeners = {};
    const el = this;
    this.classList = {
      add(...cs) {
        const set = new Set((el.className || '').split(/\s+/).filter(Boolean));
        for (const c of cs) set.add(c);
        el.className = [...set].join(' ');
      },
      remove(...cs) {
        const drop = new Set(cs);
        el.className = (el.className || '').split(/\s+/).filter(c => c && !drop.has(c)).join(' ');
      },
      contains(c) { return (el.className || '').split(/\s+/).includes(c); },
    };
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attributes[k] = String(v); if (k === 'id') this.id = String(v); }
  getAttribute(k) { return this.attributes[k]; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter(f => f !== fn); }
  append(...nodes) {
    for (let n of nodes) {
      if (n === null || n === undefined || n === false) continue;
      if (typeof n !== 'object') n = new El('text', String(n));
      n.parent = this;
      this.children.push(n);
    }
  }
  replaceChildren(...nodes) { this.children = []; this._text = ''; this.append(...nodes); }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter(c => c !== this);
    this.parent = null;
  }
  focus() {}
  querySelector(sel) {
    const parts = sel.split(',').map(s => s.trim());
    const walk = (node) => {
      for (const c of node.children || []) {
        if (parts.some(p => matches(c, p))) return c;
        const found = walk(c);
        if (found) return found;
      }
      return null;
    };
    return walk(this);
  }
  async click() {
    const ev = { preventDefault() {}, stopPropagation() {}, target: this, currentTarget: this };
    if (typeof this.onclick === 'function') await this.onclick(ev);
    for (const fn of (this._listeners.click || []).slice()) await fn(ev);
  }
}
function matches(el, sel) {
  if (sel.startsWith('[') && sel.endsWith(']')) {
    const body = sel.slice(1, -1);
    const eq = body.indexOf('=');
    if (eq < 0) return el.attributes[body] !== undefined;
    const k = body.slice(0, eq);
    const v = body.slice(eq + 1).replace(/^["']|["']$/g, '');
    return el.attributes[k] === v;
  }
  const m = /^(\w+)?(?:\.([\w-]+))?$/.exec(sel);
  if (!m) return false;
  if (m[1] && el.tag !== m[1]) return false;
  if (m[2] && !el.classList.contains(m[2])) return false;
  return !!(m[1] || m[2]);
}
function walk(node, out = []) {
  out.push(node);
  for (const c of node.children || []) walk(c, out);
  return out;
}

const mem = {};
const body = new El('body');
const document = {
  body,
  createElement: tag => new El(tag),
  createElementNS: () => new El('svg'),
  createTextNode: s => new El('text', s),
  addEventListener() {},
  removeEventListener() {},
  getElementById(id) {
    return walk(body).find(n => n.id === id) || null;
  },
  activeElement: null,
};
const ctx = {
  El, Node: El, console, setTimeout, clearTimeout,
  localStorage: { getItem: k => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); } },
  document, crypto: require('node:crypto').webcrypto,
};
ctx.window = ctx;
vm.createContext(ctx);
const rootDir = 'web/js/';
for (const f of ['core.js', 'i18n.js', 'i18n/en.js', 'i18n/ar.js']) {
  vm.runInContext(fs.readFileSync(rootDir + f, 'utf8'), ctx);
}
const s = ctx.SM;
s.views = {};
vm.runInContext(fs.readFileSync(rootDir + 'views/sell.js', 'utf8'), ctx);
s.state.user = { id: 1, permissions: ['sell'] };
s.state.session = 1;
s.state.mode = 'training';
s.state.instance = 'pay-test';
s.state.lang = 'ar';
s.scanner = { field: () => s.h('input', { class: 'scan-input' }), beep() {}, handler: null };
s.toast = () => {};
s.fail = (e) => { throw e; };
const sales = [];
s.command = async (path, key, payload) => {
  sales.push({ path, key, payload: { payments: payload.payments.map(p => ({ ...p })), cash_tendered: payload.cash_tendered } });
  return { no: 'T-1', at: '2026-10-31T12:00:00+02:00', total: payload.expected_total, change: 0,
    lines: [], payments: payload.payments || [], cash_tendered: payload.cash_tendered || 0, cashier: 'test' };
};
const product = { id: 7, name_ar: 'عصير', name_en: 'Juice', measure: 'unit', price: 3500, kind: 'grocery' };
s.api = async (method, path) => {
  if (path === '/api/shift') return { id: 1 };
  if (String(path).startsWith('/api/parties')) return [];
  if (path === '/api/quote') return { total: 3500, quote_hash: 'h', promo: 0, short: [] };
  if (path === '/api/scan') return { ok: true, product, qty: 1, scan: { code: 'X', kind: 'code' } };
  return {};
};

function paymentsOf(call) {
  return Array.from(call.payload.payments, p => ({ method: '' + p.method, amount: +p.amount, reference: p.reference ? '' + p.reference : null }));
}
function buttons(text) {
  return walk(body).filter(n => n.tag === 'button' && n.textContent.includes(text));
}
async function openPay() {
  sales.length = 0;
  await s.scanner.handler('X');
  const pay = walk(body).find(n => n.getAttribute && n.getAttribute('data-guide') === 'sell.pay');
  assert.ok(pay, 'pay button');
  await pay.click();
  assert.ok(document.getElementById('overlay'), 'pay dialog');
}
function rowInput(label) {
  const row = walk(body).find(n => n.classList && n.classList.contains('pay-row') && n.textContent.includes(label));
  assert.ok(row, 'row ' + label);
  return row.children.find(n => n.tag === 'input');
}
async function confirm() {
  const btn = walk(body).find(n => n.getAttribute && n.getAttribute('data-guide') === 'pay.confirm');
  assert.ok(btn, 'confirm');
  await btn.click();
}

(async () => {
  const root = new El('root');
  body.append(root);
  await s.views.sell.mount(root);
  await openPay();
  const labels = buttons('').filter(b => b.classList.contains('ghost')).map(b => b.textContent);
  assert.ok(labels.some(t => t.includes('إنستاباي')), 'InstaPay choice on the sale pay screen: ' + labels.join(' | '));
  assert.ok(labels.some(t => t.includes('بطاقة')));
  assert.ok(document.body.textContent.includes('إنستاباي والتحويل البنكي'), 'help text');

  await buttons('بطاقة')[0].click();
  const card = rowInput('بطاقة');
  assert.equal(card.value, '35.00', 'empty tap fills the remaining total');
  card.value = '';
  await confirm();
  assert.equal(sales.length, 0, 'empty card row must not save a sale');
  assert.ok(document.body.textContent.includes('اكتب المبلغ'), 'clear Arabic message');
  assert.ok(document.body.textContent.includes('بطاقة'));
  assert.equal(document.getElementById('overlay').classList.contains('on'), true, 'dialog stays open');

  card.value = '0';
  await confirm();
  assert.equal(sales.length, 0, 'zero card amount must not save as cash');

  card.value = '10.00';
  await confirm();
  assert.equal(sales.length, 1);
  assert.deepEqual(paymentsOf(sales[0]), [{ method: 'card', amount: 1000, reference: null }]);
  assert.equal(sales[0].payload.cash_tendered, 2500, 'the rest is cash, the card part is kept');
  assert.ok(!sales[0].payload.payments.some(p => p.method === 'cash'));

  await openPay();
  await buttons('إنستاباي')[0].click();
  const transfer = rowInput('تحويل');
  assert.equal(transfer.value, '35.00');
  transfer.value = '';
  await confirm();
  assert.equal(sales.length, 0, 'empty transfer row must not save as cash');
  assert.ok(document.body.textContent.includes('اكتب المبلغ'));
  assert.ok(document.body.textContent.includes('تحويل'));
  transfer.value = '35.00';
  await confirm();
  assert.equal(sales.length, 1);
  assert.deepEqual(paymentsOf(sales[0]), [{ method: 'transfer', amount: 3500, reference: null }]);
  assert.equal(sales[0].payload.cash_tendered, 0, 'InstaPay is a transfer, not cash');

  console.log(JSON.stringify({ ok: true, cases: ['empty-card', 'zero-card', 'split-card', 'empty-transfer', 'instapay'] }));
})().catch(e => { console.error(e); process.exit(1); });
