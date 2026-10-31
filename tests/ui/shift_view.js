/* Today and the shifts report, on the same tiny DOM the audit UI test uses.
   Money stays in piastres until the screen formats it. Run: node tests/ui/shift_view.js */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../..');

class N {
  constructor(tag = 'text', text = '') {
    this.tag = tag; this._text = text; this.children = []; this.style = {}; this.value = '';
    this.className = ''; this.attrs = {};
    this.classList = { add() {}, remove() {} };
  }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  addEventListener() {} removeEventListener() {} focus() {}
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
}
const ctx = {
  Node: N, console, setTimeout, clearTimeout,
  localStorage: { getItem() { return null; }, setItem() {} },
  document: {
    createElement: tag => new N(tag),
    createElementNS: (ns, tag) => new N(tag),
    createTextNode: s => new N('text', s),
    addEventListener() {}, removeEventListener() {},
    body: new N('body'),
  },
  crypto: require('node:crypto').webcrypto,
};
ctx.window = ctx;
vm.createContext(ctx);
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
vm.runInContext(read('web/js/core.js'), ctx);
vm.runInContext(read('web/js/i18n.js'), ctx);
vm.runInContext(read('web/js/i18n/ar.js'), ctx);
vm.runInContext(read('web/js/i18n/en.js'), ctx);
vm.runInContext(read('web/js/views.js'), ctx);
vm.runInContext(read('web/js/views/money.js'), ctx);
const s = ctx.SM;
s.state.lang = 'ar';
s.state.mode = 'shop';
s.state.businessDay = '2026-10-30';
s.toast = () => {};
s.fail = (e) => { throw e; };

const day = {
  net_sales: 0, sales_count: 0, sales_total: 0, discounts: 0, returns: 0,
  day_start_hour: 4, near_expiry: [], held: [], pending_payments: 0,
  by_method: [], refunds_by_method: [], rejected_payments: [], waste: [],
  shifts: [
    { id: 1, cashier: 'ميدو', closed_at: '2026-10-30T18:05:00', expected: 10000, counted: 5000, difference: -5000, close_note: 'نقص في العد' },
    { id: 2, cashier: 'سارة', closed_at: '2026-10-30T19:10:00', expected: 2000, counted: 1500, difference: -500, close_note: 'باقي ناقص' },
    { id: 3, cashier: 'علي', closed_at: null, expected: null, counted: null, difference: null, close_note: null },
  ],
};

function owner() {
  s.state.user = { id: 1, role: 'owner', permissions: ['shift', 'receive', 'cutting', 'produce', 'sell', 'count', 'reports', 'close_day'] };
}
function cashier() {
  s.state.user = { id: 2, role: 'cashier', permissions: ['sell', 'shift', 'discount', 'refund'] };
}

const calls = [];
s.api = async (method, path) => {
  calls.push(path);
  if (path === '/api/shift') return null;
  if (path.startsWith('/api/reports/day')) return day;
  if (path === '/api/state') return { business_day: '2026-10-30' };
  if (path === '/api/reconcile') return { ok: true };
  if (path.startsWith('/api/days/')) return { closed: false };
  if (path === '/api/aging') return { customers: [], suppliers: [] };
  throw new Error('unexpected ' + path);
};

(async () => {
  owner();
  const today = new N('root');
  await s.views.today.mount(today);
  const text = today.textContent;
  assert.ok(text.includes('ميدو: عجز 50.00 ج.م'), text);
  assert.ok(text.includes('سارة: عجز 5.00 ج.م'), text);
  assert.ok(!text.includes('افتح الوردية'), text);
  assert.equal(today.children.find(n => n.attrs && n.attrs.id === 'today-attention') ? 'yes' : 'no', 'yes');

  day.shifts = [
    { id: 4, cashier: 'ميدو', closed_at: '2026-10-30T20:00:00', expected: 10000, counted: 10000, difference: 0, close_note: null },
  ];
  const balanced = new N('root');
  await s.views.today.mount(balanced);
  assert.ok(balanced.textContent.includes('افتح الوردية'), 'a balanced day still asks the owner to open a shift');
  assert.ok(!balanced.textContent.includes('عجز'), balanced.textContent);

  day.shifts = [
    { id: 5, cashier: 'ميدو', closed_at: '2026-10-30T20:00:00', expected: 10000, counted: 11500, difference: 1500, close_note: 'زيادة في الدرج' },
  ];
  const over = new N('root');
  await s.views.today.mount(over);
  assert.ok(over.textContent.includes('ميدو: زيادة 15.00 ج.م'), over.textContent);
  assert.ok(!over.textContent.includes('افتح الوردية'), over.textContent);

  calls.length = 0;
  cashier();
  const till = new N('root');
  await s.views.today.mount(till);
  assert.ok(till.textContent.includes('افتح الوردية'), till.textContent);
  assert.ok(!calls.some(p => p.startsWith('/api/reports/day')), 'a cashier is not shown the day report');

  owner();
  day.shifts = [
    { id: 1, cashier: 'ميدو', closed_at: '2026-10-30T18:05:00', expected: 10000, counted: 5000, difference: -5000, close_note: 'نقص في العد' },
    { id: 2, cashier: 'سارة', closed_at: '2026-10-30T19:10:00', expected: 2000, counted: 1500, difference: -500, close_note: 'باقي ناقص' },
    { id: 3, cashier: 'علي', closed_at: null, expected: null, counted: null, difference: null, close_note: null },
  ];
  const reports = new N('root');
  await s.views.reports.mount(reports);
  await new Promise(r => setTimeout(r, 30));
  const card = reports.children.find(n => n.attrs && n.attrs.id === 'shifts-card')
    || (function find(n) {
      if (!n || !n.children) return null;
      if (n.attrs && n.attrs.id === 'shifts-card') return n;
      for (const c of n.children) { const hit = find(c); if (hit) return hit; }
      return null;
    })(reports);
  assert.ok(card, 'shifts card missing\n' + reports.textContent.slice(0, 400));
  const rows = card.textContent;
  assert.ok(rows.includes('ميدو') && rows.includes('سارة') && rows.includes('علي'), rows);
  assert.ok(rows.includes('نقص في العد') && rows.includes('باقي ناقص'), rows);
  assert.ok(rows.includes('-50.00') && rows.includes('-5.00'), rows);
  assert.ok(rows.includes('2026-10-30 18:05'), rows);
  assert.ok(rows.includes('مفتوح'), rows);
  console.log('shift view: shortage line, no false open-shift, cashier and reason on the report');
})().catch(e => { console.error(e); process.exit(1); });
