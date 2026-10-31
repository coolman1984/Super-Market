/* Audit R16/R17/R18 as CORRECT-behaviour checks on the real frontend code,
   in the same tiny DOM substitute the review used (docs/audit/reproduce_ui_review.js).
   Run: node tests/ui/audit_ui.js  (browser checks live in tests/ui/flows.js) */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class N {
  constructor(tag = 'text', text = '') { this.tag = tag; this._text = text; this.children = []; this.style = {}; this.value = ''; this.classList = { add() {}, remove() {} }; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  addEventListener() {} removeEventListener() {} focus() {} setAttribute() {}
}
const ctx = { Node: N, console, setTimeout, clearTimeout, localStorage: { getItem() { return null; }, setItem() {} },
  document: { createElement: tag => new N(tag), createElementNS: (ns, tag) => new N(tag), createTextNode: s => new N('text', s),
    addEventListener() {}, removeEventListener() {}, body: new N('body') }, crypto: require('node:crypto').webcrypto };
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync('web/js/core.js', 'utf8'), ctx);
const s = ctx.SM; s.i18n = { ar: {}, en: {} }; s.views = {};
const results = [];
// R16: nested description pairs stay elements
const dl = s.h('dl', {}, [[s.h('dt', { text: 'Field' }), s.h('dd', { text: 'Value' })]]);
assert.equal(dl.textContent, 'FieldValue');
results.push({ id: 'R16', ok: true });
s.scanner = { field: () => s.h('input'), beep() {}, handler: null };
s.toast = () => {}; s.state.user = { id: 1 }; s.state.session = 1; s.state.mode = 'shop';
s.workstation = () => 'till-1';
s.api = async (method, path) => path === '/api/shift' ? { shift: { id: 1 } } :
  { ok: true, product: { id: 1, name_ar: 'PRIVATE-CART', name_en: 'PRIVATE-CART', measure: 'unit', price: 1000 }, qty: 1, scan: { code: 'CODE', kind: 'code' } };
vm.runInContext(fs.readFileSync('web/js/views/sell.js', 'utf8'), ctx);
(async () => {
  const first = new N('root'); await s.views.sell.mount(first);
  await s.scanner.handler('CODE');
  assert.ok(first.textContent.includes('PRIVATE-CART'));
  s.views.sell.unmount();
  s.state.user = { id: 2 }; s.state.session = 2;                     // another user on the same till
  const second = new N('root'); await s.views.sell.mount(second);
  assert.ok(!second.textContent.includes('PRIVATE-CART'), 'previous user cart must not be visible');
  results.push({ id: 'R17', ok: true });
  const receipt = s.receipt({ no: 'S-OLD', at: '2000-01-01T10:00:00+02:00', total: 1000, lines: [], payments: [] }).textContent;
  assert.ok(receipt.includes('2000'), 'receipt must show the saved sale time');
  results.push({ id: 'R18', ok: true });
  console.log(JSON.stringify(results));
})().catch(e => { console.error(e); process.exitCode = 1; });
