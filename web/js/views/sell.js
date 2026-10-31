/* Sell screen: scan field, cart, fixed totals, payment dialog, receipt. */
'use strict';

(function () {
  const { h, t, icon } = SM;
  /* The cart belongs to one user, till and mode (audit R17); logout empties it. */
  const cart = { lines: [], invoiceDiscount: 0, customer: null, owner: null, key: null, unknown: false };
  function resetCart() { Object.assign(cart, { lines: [], invoiceDiscount: 0, customer: null, key: null, unknown: false }); }
  const ownerOf = () => [SM.state.user && SM.state.user.id, SM.workstation(), SM.state.mode].join('|');
  SM.draftResets.push(() => { resetCart(); cart.owner = null; });
  let shift = null;

  function lineAmount(l) {
    if (l.pack) return l.pack.price;
    if (l.product.price === null || l.product.price === undefined) return null;
    return l.product.measure === 'g' ? Math.floor((l.product.price * l.qty + 500) / 1000) : l.product.price * l.qty;
  }
  function totals() {
    let gross = 0, unknown = false, disc = 0;
    for (const l of cart.lines) { const a = lineAmount(l); if (a === null) unknown = true; else gross += a; disc += l.discount || 0; }
    return { gross, discount: disc + cart.invoiceDiscount, total: gross - disc - cart.invoiceDiscount, unknown };
  }

  async function addScan(raw, view) {
    if (cart.unknown) { SM.toast(t('pending.sale_unknown'), 'bad'); return; }
    const session = SM.state.session, owner = cart.owner;
    let r;
    try { r = await SM.api('POST', '/api/scan', { raw }); } catch (e) { SM.fail(e); SM.scanner.beep(false); return; }
    if (session !== SM.state.session || owner !== cart.owner) return;   // answer for a previous user/session
    cart.key = null;                                                     // the cart changed: a new sale
    view.lastScan(r, raw);
    if (!r.ok) { SM.scanner.beep(false); return; }
    if (r.pack) {
      if (cart.lines.some(l => l.pack && l.pack.code === r.pack.code)) {
        SM.toast(t('err.pack_in_cart'), 'bad'); SM.scanner.beep(false); return;
      }
      cart.lines.push({ product: r.product, qty: r.pack.net_g, pack: r.pack, scanned: r.scan.code });
    } else if (r.qty === null || r.qty === undefined) {
      const grams = await askWeight(r.product);
      if (!grams) return;
      cart.lines.push({ product: r.product, qty: grams, scanned: r.scan.code, manualWeight: true });
    } else {
      addProduct(r.product, r.qty, r.scan.code, r.scan.kind === 'label');
    }
    SM.scanner.beep(true);
    view.render();
  }

  function addProduct(product, qty, scanned, separate) {
    // Two scans of an ordinary barcode are two items: merge into one line, never drop.
    const same = !separate && cart.lines.find(l => !l.pack && !l.manualWeight && !l.label && l.product.id === product.id && product.measure !== 'g');
    if (same) same.qty += qty;
    else cart.lines.push({ product, qty, scanned, label: separate });
  }

  function askWeight(product) {
    return new Promise((resolve) => {
      const input = h('input', { class: 'input big', inputmode: 'decimal', autocomplete: 'off' });
      let done = false;
      const ok = () => {
        const g = SM.parseKg(input.value);
        if (!g || g <= 0) { SM.toast(t('err.bad_weight'), 'bad'); return; }
        done = true; d.close(); resolve(g);
      };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ok(); } });
      const d = SM.dialog({ title: t('sell.weight_title', { name: SM.name(product) }), onClose: () => { if (!done) resolve(null); },
        body: [SM.field(t('sell.weight_kg'), input, t('sell.weight_manual_note'))],
        actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }), h('button', { class: 'btn primary', text: t('add'), onclick: ok })] });
    });
  }

  async function search(view) {
    const q = h('input', { class: 'input', placeholder: t('sell.search_ph'), autocomplete: 'off' });
    const list = h('div', { class: 'pick-list' });
    let timer;
    const run = async () => {
      const rows = await SM.api('GET', '/api/products?q=' + encodeURIComponent(q.value)).catch(() => []);
      SM.put(list, ...rows.filter(r => ['grocery', 'raw_meat', 'prepared'].includes(r.kind)).map(r => h('button', { class: 'pick', onclick: async () => {
        d.close();
        if (r.measure === 'g') { const g = await askWeight(r); if (g) { cart.key = null; cart.lines.push({ product: r, qty: g, manualWeight: true }); view.render(); } }
        else { cart.key = null; addProduct(r, 1, null); view.render(); }
      } }, h('b', { text: SM.name(r) }), h('span', { class: 'muted', text: r.code }), SM.num(r.price === null ? '—' : SM.money(r.price)),
        h('span', { class: 'muted' }, t('sell.available'), ' ', SM.qty(r.available, r.measure)))));
    };
    q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(run, 180); });
    const d = SM.dialog({ title: t('sell.search'), body: [q, list], wide: true });
    run();
  }

  function cartPayload() {
    return {
      lines: cart.lines.map(l => l.pack ? { pack_code: l.pack.code, scanned: l.scanned } : { product_id: l.product.id, qty: l.qty, scanned: l.scanned || null, line_discount: l.discount || 0 }),
      invoice_discount: cart.invoiceDiscount, customer_id: cart.customer || undefined,
    };
  }

  /* The total shown at payment comes from the server (audit R14); a change
     after that shows old and new total and needs the cashier's OK. */
  async function payDialog(view) {
    if (!cart.lines.length) return;
    if (cart.unknown) { return verifyUnknown(view); }
    let quote;
    try { quote = await SM.api('POST', '/api/quote', { payload: cartPayload() }); } catch (e) { SM.fail(e); return; }
    const estimate = totals();
    const tot = { total: quote.total };
    // the key belongs to this cart until its answer is known (audit S03)
    cart.key = cart.key || SM.newKey();
    const key = cart.key;
    const cash = h('input', { class: 'input big', inputmode: 'decimal', autocomplete: 'off', maxlength: '12', 'aria-label': t('pay.cash_received') });
    const changeOut = h('div', { class: 'pay-change' });
    const others = [];
    const otherBox = h('div', { class: 'stack' });
    const customers = SM.api('GET', '/api/parties?kind=customer').catch(() => []);
    const addOther = (method) => {
      const amount = h('input', { class: 'input', inputmode: 'decimal', autocomplete: 'off', maxlength: '12' });
      const ref = h('input', { class: 'input', autocomplete: 'off', maxlength: '80', placeholder: t('pay.reference') });
      const row = { method, amount, ref };
      others.push(row);
      const remove = h('button', { class: 'icon-btn', 'aria-label': t('remove'), onclick: () => { others.splice(others.indexOf(row), 1); el.remove(); update(); } }, icon('trash'));
      const el = h('div', { class: 'pay-row' }, h('b', { text: t('pay.m.' + method) }), amount, method === 'credit' ? null : ref, remove);
      amount.addEventListener('input', update);
      otherBox.append(el);
      if (method === 'credit') {
        customers.then(list => {
          const sel = h('select', { class: 'input' }, h('option', { value: '', text: t('pay.pick_customer') }),
            list.map(c => h('option', { value: c.id, text: c.name + ' — ' + SM.money(c.balance) + ' / ' + SM.money(c.credit_limit) })));
          sel.onchange = () => { cart.customer = sel.value ? parseInt(sel.value, 10) : null; };
          el.append(sel);
        });
      }
      amount.focus();
    };
    const totalBox = h('div', { class: 'pay-total' });
    function showTotal(note) {
      SM.put(totalBox, t('sell.total'), ' ', SM.num(SM.moneyText(tot.total)), ' ', h('span', { class: 'badge ok', text: t('pay.server_price') }),
        note ? h('div', { class: 'badge warn', text: note }) : null);
    }
    showTotal(estimate.total !== quote.total ? t('pay.price_updated', { old: SM.money(estimate.total), now: SM.money(quote.total) }) : null);
    function update() {
      let nonCash = 0, bad = false;
      for (const o of others) { const v = SM.parseMoney(o.amount.value || '0'); if (v === null) bad = true; else nonCash += v; }
      const due = tot.total - nonCash;
      const got = cash.value ? SM.parseMoney(cash.value) : due;
      SM.put(changeOut);
      if (bad || got === null) { changeOut.append(h('span', { class: 'badge bad', text: t('err.bad_amount') })); return null; }
      if (due < 0) { changeOut.append(h('span', { class: 'badge bad', text: t('err.payment_exceeds_total') })); return null; }
      changeOut.append(h('div', {}, t('pay.cash_due'), ' ', SM.num(SM.moneyText(due))));
      if (got < due) { changeOut.append(h('span', { class: 'badge warn', text: t('pay.short', { amount: SM.money(due - got) }) })); return null; }
      changeOut.append(h('div', { class: 'change-big' }, t('pay.change'), ' ', SM.num(SM.moneyText(got - due))));
      return { nonCash, due, got };
    }
    cash.addEventListener('input', update);
    cash.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      // A barcode typed into the amount field: long digit run -> refuse, clear, never add items.
      if (/^[0-9٠-٩]{8,}$/.test(cash.value.trim())) { cash.value = ''; SM.toast(t('pay.looks_like_barcode'), 'bad'); }
      update();
    });
    const quick = h('div', { class: 'row wrap' }, h('button', { class: 'btn sm', text: t('pay.exact'), onclick: () => { cash.value = SM.money(tot.total).replace(/,/g, ''); update(); } }),
      [5000, 10000, 20000, 50000].map(v => h('button', { class: 'btn sm', onclick: () => { cash.value = String(v / 100); update(); } }, SM.money(v))));
    const methods = h('div', { class: 'row wrap' }, ['card', 'wallet', 'transfer', 'credit'].map(m => h('button', { class: 'btn sm ghost', onclick: () => addOther(m) }, icon('plus', 'sm'), t('pay.m.' + m))));
    const payBtn = h('button', { class: 'btn primary lg', text: t('pay.confirm') });
    let busy = false;
    payBtn.onclick = async () => {
      const v = update();
      if (!v || busy) return;
      busy = true; payBtn.disabled = true; payBtn.textContent = t('pay.saving');
      const payload = Object.assign(cartPayload(), {
        payments: others.map(o => ({ method: o.method, amount: SM.parseMoney(o.amount.value || '0'), reference: o.ref.value || undefined })).filter(p => p.amount > 0),
        cash_tendered: v.due > 0 ? v.got : 0, expected_total: tot.total,
      });
      try {
        const sale = await SM.commandWithApproval('/api/sales', key, payload);
        d.close();
        resetCart();
        view.render();
        receiptDialog(sale);
      } catch (e) {
        busy = false; payBtn.disabled = false; payBtn.textContent = t('pay.confirm');
        if (e.code === 'price_changed') {
          // nothing was saved; show the new total and let the cashier redo the cash
          const old = tot.total;
          tot.total = e.detail.total;
          showTotal(t('pay.price_changed', { old: SM.money(old), now: SM.money(tot.total) }));
          cash.value = ''; update();
          return;
        }
        if (e.unknownResult) {
          cart.unknown = true;               // the sale may be saved: same key until we know
          d.close(); view.render();
          SM.toast(t('pending.sale_unknown'), 'bad');
          return;
        }
        SM.fail(e);
      }
    };
    const d = SM.dialog({ title: t('pay.title'), wide: true,
      body: [totalBox, SM.field(t('pay.cash_received'), cash, t('pay.cash_help')), quick, h('h3', { text: t('pay.other') }), methods, otherBox, changeOut],
      actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }), payBtn] });
    update();
  }

  /* Ask the server with the SAME key whether the last sale was saved. */
  async function verifyUnknown(view) {
    const op = SM.pendingOps().find(p => p.key === cart.key);
    if (!op) { cart.unknown = false; view.render(); return; }
    try {
      const sale = await SM.command(op.path, op.key, op.payload, op.extra, 1);
      resetCart(); view.render(); receiptDialog(sale);
    } catch (e) {
      if (e.unknownResult) { SM.toast(t('pending.still_unknown'), 'bad'); return; }
      cart.unknown = false; cart.key = null; view.render(); SM.fail(e);   // definite answer: not saved
    }
  }

  function receiptDialog(sale) {
    const model = sale.receipt || sale;
    const paper = SM.receipt(model);
    const d = SM.dialog({ title: t('receipt.title', { no: model.no }),
      body: [h('div', { class: 'change-big' }, t('pay.change'), ' ', SM.num(SM.moneyText(model.change))), paper],
      actions: [h('button', { class: 'btn', onclick: () => SM.printReceipt(model) }, icon('print'), t('receipt.print')),
        h('button', { class: 'btn primary', text: t('sell.new'), onclick: () => d.close(), autofocus: true })] });
  }

  /* One receipt for first print and reprint, from the server model (audit R18):
     the SAVED sale time, effective payment states, discounts and training mark. */
  SM.receipt = function (r) {
    const lang = SM.state.lang;
    const L = (a, b) => h('div', { class: 'rc-line' }, h('span', { text: a }), SM.num(b));
    const at = r.at ? new Date(r.at).toLocaleString(lang === 'ar' ? 'ar-EG-u-nu-latn' : 'en-GB', { timeZone: 'Africa/Cairo' }) : '—';
    return h('div', { class: 'receipt' + (r.training ? ' training' : '') },
      r.training ? h('div', { class: 'rc-train', text: t('receipt.training') }) : null,
      r.reprint ? h('div', { class: 'rc-train', text: t('receipt.reprint') }) : null,
      h('div', { class: 'rc-shop', text: r.shop_name || t('app.name') }),
      h('div', { class: 'rc-kind', text: t('receipt.not_tax') }),
      L(t('receipt.no'), r.no), L(t('receipt.date'), at), r.cashier ? L(t('receipt.cashier'), r.cashier) : null,
      h('hr'),
      (r.lines || []).map(l => h('div', { class: 'rc-item' }, h('div', { text: lang === 'ar' ? l.name_ar : l.name_en }),
        L(SM.qty(l.qty, l.measure) + ' × ' + SM.money(l.unit_price), SM.money(l.gross)),
        l.discount ? L(t('receipt.discount'), '-' + SM.money(l.discount)) : null)),
      h('hr'),
      r.discount ? L(t('receipt.discount'), '-' + SM.money(r.discount)) : null,
      L(t('sell.total'), SM.money(r.total)),
      (r.payments || []).map(p => L(t('pay.m.' + p.method) + (p.state !== 'settled' ? ' (' + t('state.' + p.state) + ')' : ''), SM.money(p.amount))),
      r.cash_tendered ? L(t('pay.cash_received'), SM.money(r.cash_tendered)) : null,
      r.change ? L(t('pay.change'), SM.money(r.change)) : null,
      h('div', { class: 'rc-foot', text: r.footer || '' }));
  };
  SM.printReceipt = function (sale) {
    let area = document.getElementById('print');
    if (!area) { area = h('div', { id: 'print' }); document.body.append(area); }
    area.className = 'w' + (SM.state.settings.receipt_width_mm || 80);
    SM.put(area, SM.receipt(sale));
    window.print();
  };

  SM.views.sell = {
    icon: 'cart', perm: 'sell',
    async mount(root) {
      if (cart.owner !== ownerOf()) { resetCart(); cart.owner = ownerOf(); }
      try { shift = await SM.api('GET', '/api/shift'); } catch (e) { shift = null; }
      const scanInput = SM.scanner.field({});
      const status = h('div', { class: 'scan-status', role: 'status', 'aria-live': 'polite' });
      const cartBox = h('div', { class: 'cart' });
      const side = h('aside', { class: 'pay-panel card' });
      const view = {
        lastScan(r, raw) {
          SM.put(status);
          if (r.ok) status.append(h('span', { class: 'badge ok' }, icon('check', 'sm'), SM.name(r.product)));
          else status.append(h('span', { class: 'badge bad' }, icon('warn', 'sm'), t('err.' + r.error) === 'err.' + r.error ? r.error : t('err.' + r.error)),
            r.seen ? h('code', { class: 'ltr', text: r.seen }) : null);
          if (r.price_changed) status.append(h('span', { class: 'badge warn', text: t('sell.pack_price_changed', { packed: SM.money(r.price_changed.packed), current: SM.money(r.price_changed.current) }) }));
        },
        render() {
          const tot = totals();
          SM.put(cartBox, cart.lines.length ? SM.table([
            { label: t('sell.item'), render: l => h('div', {}, h('b', { text: SM.name(l.product) }),
              l.pack ? h('div', { class: 'muted ltr', text: t('sell.pack') + ' ' + l.pack.code }) : null,
              l.manualWeight ? h('div', { class: 'badge warn', text: t('sell.manual_weight') }) : null) },
            { label: t('sell.qty'), render: l => h('div', { class: 'row qty-cell' },
              !l.pack && l.product.measure !== 'g' ? h('button', { class: 'icon-btn', 'aria-label': t('less'), onclick: () => { cart.key = null; l.qty > 1 ? l.qty-- : cart.key = null; cart.lines.splice(cart.lines.indexOf(l), 1); view.render(); } }, icon('minus')) : null,
              SM.num(SM.qty(l.qty, l.product.measure)),
              !l.pack && l.product.measure !== 'g' ? h('button', { class: 'icon-btn', 'aria-label': t('more'), onclick: () => { cart.key = null; l.qty++; view.render(); } }, icon('plus')) : null) },
            { label: t('sell.price'), num: true, render: l => SM.num(l.pack ? SM.money(l.pack.price) : SM.money(l.product.price) + (l.product.measure === 'g' ? ' / ' + t('unit.kg') : '')) },
            { label: t('sell.amount'), num: true, render: l => SM.num(SM.money(lineAmount(l))) },
            { label: '', render: l => h('button', { class: 'icon-btn', 'aria-label': t('remove'), onclick: () => { cart.key = null; cart.lines.splice(cart.lines.indexOf(l), 1); view.render(); } }, icon('trash')) },
          ], cart.lines) : SM.empty(t('sell.empty'), t('sell.empty_hint')));
          SM.put(side,
            cart.unknown ? h('div', { class: 'tip warn-tip', role: 'alert' }, h('div', {}, h('b', { text: t('pending.sale_unknown') }),
              h('button', { class: 'btn primary sm block', text: t('pending.check'), onclick: () => verifyUnknown(view) }))) : null,
            h('div', { class: 'tot-row' }, h('span', { text: t('sell.items') }), SM.num(String(cart.lines.length))),
            cart.invoiceDiscount ? h('div', { class: 'tot-row' }, h('span', { text: t('sell.discount') }), SM.num('-' + SM.money(cart.invoiceDiscount))) : null,
            h('div', { class: 'tot-big' }, h('span', { text: t('sell.total') }), SM.num(SM.moneyText(tot.total))),
            h('button', { class: 'btn primary lg block', disabled: !cart.lines.length || !shift || cart.unknown, onclick: () => payDialog(view) }, t('sell.pay'), h('span', { class: 'kbd', text: 'F9' })),
            h('button', { class: 'btn ghost block', disabled: !cart.lines.length, onclick: () => discountDialog(view) }, t('sell.discount_btn')),
            h('button', { class: 'btn ghost block', disabled: !cart.lines.length, onclick: async () => { if (await SM.confirm(t('sell.clear'), t('sell.clear_q'))) { cart.key = null; cart.lines = []; cart.invoiceDiscount = 0; view.render(); } } }, t('sell.clear')));
          scanInput.focus();
        },
      };
      SM.scanner.handler = (raw) => addScan(raw, view);
      SM.put(root, 
        shift ? null : h('div', { class: 'tip warn-tip' }, icon('warn'), h('div', {}, h('b', { text: t('sell.no_shift') }), ' ',
          h('a', { href: '#cash', text: t('sell.open_shift') }))),
        h('div', { class: 'sell-grid' },
          h('section', { class: 'sell-main' },
            h('div', { class: 'scan-bar card' }, icon('scan', 'lg'), scanInput,
              h('button', { class: 'btn', onclick: () => search(view) }, icon('search'), t('sell.search'), h('span', { class: 'kbd', text: 'F4' }))),
            status, cartBox),
          side));
      view.render();
      this.keys = (e) => {
        if (SM.state.dialogOpen) return;
        if (e.key === 'F9') { e.preventDefault(); if (cart.lines.length && shift) payDialog(view); }
        else if (e.key === 'F4') { e.preventDefault(); search(view); }
        else if (e.key === 'F2') { e.preventDefault(); scanInput.focus(); }
      };
      document.addEventListener('keydown', this.keys);
    },
    unmount() { document.removeEventListener('keydown', this.keys); SM.scanner.handler = null; },
  };

  function discountDialog(view) {
    const input = h('input', { class: 'input', inputmode: 'decimal', value: cart.invoiceDiscount ? SM.money(cart.invoiceDiscount) : '' });
    const d = SM.dialog({ title: t('sell.discount_btn'), body: [SM.field(t('sell.discount_amount'), input, t('sell.discount_help'))],
      actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }),
        h('button', { class: 'btn primary', text: t('save'), onclick: () => {
          const v = input.value ? SM.parseMoney(input.value) : 0;
          if (v === null || v > totals().gross) { SM.toast(t('err.bad_amount'), 'bad'); return; }
          cart.key = null; cart.invoiceDiscount = v; d.close(); view.render();
        } })] });
  }
})();
