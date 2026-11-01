/* Sell screen: scan field, cart, fixed totals, payment dialog, receipt. */
'use strict';

(function () {
  const { h, t, icon } = SM;
  /* The cart belongs to one user, till and mode (audit R17); logout empties it. */
  const cart = { lines: [], invoiceDiscount: 0, customer: null, owner: null, key: null, unknown: false };
  function resetCart() { Object.assign(cart, { lines: [], invoiceDiscount: 0, customer: null, key: null, unknown: false, restoring: false, restoreFailed: false }); }
  /* the shop's own identity first: a draft never crosses to another shop folder on the same browser */
  const ownerOf = () => [SM.state.instance || '-', SM.state.user && SM.state.user.id, SM.workstation(), SM.state.mode].join('|');
  const HELD_MAX = 10;

  /* An unpaid cart survives a page reload. Only references are kept (product, quantity, pack,
     scanned code); prices, stock and expiry are asked again from the server when it comes back,
     and again at payment. A sale whose answer was lost keeps its key, so it is checked, not re-sold. */
  const draftKey = () => 'draft.' + ownerOf();
  function saveDraft() {
    if (!cart.owner || cart.owner !== ownerOf() || cart.restoring) return;   // never overwrite a draft not yet read back
    if (!cart.lines.length && !cart.unknown) { SM.prefs.set(draftKey(), null); return; }
    SM.prefs.set(draftKey(), { at: new Date().toISOString(), invoiceDiscount: cart.invoiceDiscount, customer: cart.customer,
      key: cart.key || null,          // set before the payment request leaves (review: a reload mid-payment never sells twice)
      lines: cart.lines.map(l => ({ id: l.product.id, qty: l.qty, pack: l.pack ? l.pack.code : null, scanned: l.scanned || null,
        manualWeight: !!l.manualWeight, discount: l.discount || 0 })) });
  }
  async function restoreDraft(view) {
    if (cart.restoreRunning) return;          // one read-back at a time: a double click never adds the lines twice
    cart.restoreRunning = true;
    try { await restoreDraftOnce(view); } finally { cart.restoreRunning = false; if (cart.owner === ownerOf()) view.render(); }
  }
  async function restoreDraftOnce(view) {
    const d = SM.prefs.get(draftKey(), null);
    if (!d || (cart.lines.length && !cart.restoreFailed)) { cart.restoring = false; return; }   // after a failed read, new lines are kept beside it
    const session = SM.state.session, owner = cart.owner;
    let lines;
    try { lines = await restoreLines(d.lines, session, owner, true); }
    catch (e) {   // no connection: the saved draft stays untouched until it can be read back
      if (session === SM.state.session && owner === cart.owner) { cart.restoreFailed = true; view.render(); }
      return;
    }
    if (lines === null) return;
    cart.restoring = false; cart.restoreFailed = false;
    cart.lines.push(...lines);
    cart.invoiceDiscount = d.invoiceDiscount || 0; cart.customer = d.customer || null;
    if (d.key && SM.pendingOps().some(p => p.key === d.key)) { cart.key = d.key; cart.unknown = true; }   // the lost answer is checked first
    view.render();
    if (lines.length) SM.toast(t('sell.draft_restored', { n: lines.length }));
  }
  /* Re-read every saved line from the server; a pack that sold meanwhile or a missing product is reported, not added. */
  async function restoreLines(saved, session, owner, strict) {
    const out = [];
    // strict: a network or server failure throws (keep the draft); a clear answer (sold pack, gone product) is reported
    const read = (promise) => promise.catch(e => { if (strict && (e.code === 'network' || e.status === 0 || e.status >= 500)) throw e; return null; });
    for (const l of saved || []) {
      if (l.pack) {
        const r = await read(SM.api('POST', '/api/scan', { raw: l.pack }));
        if (session !== SM.state.session || owner !== cart.owner) return null;
        if (r && r.ok && r.pack) out.push({ product: r.product, qty: r.pack.net_g, pack: r.pack, scanned: l.scanned });
        else SM.toast(t('err.' + (r ? r.error : 'network')), 'bad');
      } else {
        const p = await read(SM.api('GET', '/api/products/' + l.id));
        if (session !== SM.state.session || owner !== cart.owner) return null;
        if (!p) SM.toast(t('sell.draft_line_gone'), 'bad');
        if (p) out.push({ product: p, qty: l.qty, scanned: l.scanned, manualWeight: l.manualWeight, discount: l.discount || 0 });
      }
    }
    return out;
  }
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
      const w = await askWeight(r.product);
      if (!w || blockOver(r.product, w.grams)) return;
      cart.lines.push({ product: r.product, qty: w.grams, scanned: r.scan.code, manualWeight: !w.fromScale });
    } else if (!addProduct(r.product, r.qty, r.scan.code, r.scan.kind === 'label')) {
      return;
    }
    SM.scanner.beep(true);
    view.render();
  }

  function looseQty(productId) {
    return cart.lines.reduce((n, l) => n + (!l.pack && l.product.id === productId ? l.qty : 0), 0);
  }
  /* Refuse the line at the moment it is added. Pay stays off later if the cart is still over (stock moved meanwhile). */
  function overStock(product, addQty) {
    if (!product || product.available === undefined || product.available === null) return false;
    return looseQty(product.id) + addQty > product.available;
  }
  function blockOver(product, addQty) {
    if (!overStock(product, addQty)) return false;
    SM.toast(t('sell.over_stock', { qty: SM.qty(product.available, product.measure) }), 'bad');
    SM.scanner.beep(false);
    return true;
  }
  function cartOver() { return !!(cart.short && Object.keys(cart.short).length); }

  function addProduct(product, qty, scanned, separate) {
    if (blockOver(product, qty)) return false;
    // Two scans of an ordinary barcode are two items: merge into one line, never drop.
    const same = !separate && cart.lines.find(l => !l.pack && !l.manualWeight && !l.label && l.product.id === product.id && product.measure !== 'g');
    if (same) same.qty += qty;
    else cart.lines.push({ product, qty, scanned, label: separate });
    return true;
  }

  /* Weighed item: with a scale cabled to this till its live weight shows and one tap takes it
     (owner, 31 Oct 2026); without one, or if it is off, the weight is typed as before. */
  /* report M8: after every change of the cart the server says, a moment later, if any item asks for
     more than can be sold; the line shows it at once instead of failing at payment. */
  let stockTimer = null, stockAsked = '';
  function checkStock(view) {
    clearTimeout(stockTimer);
    stockTimer = setTimeout(async () => {
      const payload = cart.lines.length ? cartPayload() : null;
      const sig = JSON.stringify(payload);
      if (!payload || sig === stockAsked) { if (!payload && cart.short) { cart.short = null; } return; }
      stockAsked = sig;
      const q = await SM.api('POST', '/api/quote', { payload }).catch(() => null);
      if (!q || sig !== JSON.stringify(cart.lines.length ? cartPayload() : null)) return;
      const next = {};
      (q.short || []).forEach(s => { next[s.product] = s.available; });
      if (JSON.stringify(next) !== JSON.stringify(cart.short || {})) { cart.short = Object.keys(next).length ? next : null; view.render(); }
    }, 350);
  }

  function askWeight(product) {
    return new Promise((resolve) => {
      const input = h('input', { class: 'input big', inputmode: 'decimal', autocomplete: 'off', 'data-guide': 'sell.weight' });
      let done = false, closed = false, live = null;
      const finish = (grams, fromScale) => { done = true; d.close(); resolve({ grams, fromScale }); };
      const ok = () => {
        const g = SM.parseKg(input.value);
        if (!g || g <= 0 || g > 200000) { SM.toast(t('err.bad_weight'), 'bad'); input.focus(); return; }     // M8: no 99,999 kg line
        finish(g, false);
      };
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ok(); } });
      const scaleBox = h('div', { class: 'scale-live', hidden: true });
      const take = h('button', { class: 'btn primary big', id: 'take-weight', disabled: true, onclick: () => { if (live && live.stable && live.grams > 0) finish(live.grams, true); } });
      const d = SM.dialog({ title: t('sell.weight_title', { name: SM.name(product) }), onClose: () => { closed = true; if (!done) resolve(null); },
        body: [scaleBox, SM.field(t('sell.weight_kg'), input, t('sell.weight_manual_note'))],
        actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }), h('button', { class: 'btn primary', text: t('add'), onclick: ok })] });
      (async () => {
        while (!closed) {
          const r = await SM.api('GET', '/api/scale/weight').catch(() => null);
          if (!r || !r.has_scale || closed) return;              // no scale on this till: typing only, nothing changes
          scaleBox.hidden = false;
          live = r.grams === null ? null : r;
          const shown = live ? SM.qty(live.grams, 'g') : '—';
          SM.put(scaleBox, h('div', { class: 'muted', text: t('scale.on_scale') }), h('div', { class: 'scale-weight ltr', text: shown }),
            h('div', { class: live && !live.stable ? 'badge warn' : 'muted', text: !r.connected ? t('scale.not_connected') : !live ? t('scale.put_item') : live.stable ? t('scale.ready') : t('scale.wait_still') }), take);
          take.textContent = t('scale.take');
          take.disabled = !(live && live.stable && live.grams > 0);
          await new Promise(res => setTimeout(res, 300));
        }
      })();
    });
  }

  /* Held sales: kept in this browser per user + till + mode. Holding reserves nothing:
     on resume every line is re-resolved by the server (price, expiry, pack status). */
  const heldKey = () => 'held.' + ownerOf();
  const held = () => {
    // held sales saved before the shop identity joined the key: moved once into this shop's slot
    const old = 'held.' + [SM.state.user && SM.state.user.id, SM.workstation(), SM.state.mode].join('|');
    const before = SM.prefs.get(old, null);
    if (before && before.length) {
      SM.prefs.set(heldKey(), SM.prefs.get(heldKey(), []).concat(before));
      try { localStorage.removeItem('sm.' + old); } catch (e) { /* private mode */ }
    }
    return SM.prefs.get(heldKey(), []);
  };
  function holdCart(view) {
    if (!cart.lines.length || cart.unknown) return;
    // never drop an older held sale to make room: the cashier decides which one goes
    if (held().length >= HELD_MAX) { SM.toast(t('sell.held_full', { n: HELD_MAX }), 'bad'); return; }
    const lines = cart.lines.map(l => ({ id: l.product.id, qty: l.qty, pack: l.pack ? l.pack.code : null, scanned: l.scanned || null, manualWeight: !!l.manualWeight }));
    SM.prefs.set(heldKey(), held().concat([{ at: new Date().toISOString(), lines, invoiceDiscount: cart.invoiceDiscount }]));
    resetCart(); view.render(); SM.toast(t('sell.held_saved'));
  }
  async function resumeDialog(view) {
    const list = held();
    const body = list.length ? list.map((h1, i) => h('div', { class: 'card row' }, h('div', { class: 'grow' }, h('b', { class: 'ltr', text: h1.at.slice(0, 16).replace('T', ' ') }),
      h('span', { class: 'muted', text: ' — ' + h1.lines.length })),
      h('button', { class: 'btn primary sm', text: t('sell.resume'), onclick: async () => {
        const session = SM.state.session, owner = cart.owner, key = heldKey();
        d.close(); cart.key = null;
        const restored = await restoreLines(h1.lines, session, owner);
        if (restored === null) return;
        // the answer belongs to the user who asked: a logout meanwhile leaves their held sale where it was
        SM.prefs.set(key, SM.prefs.get(key, []).filter(x => x.at !== h1.at));
        cart.lines.push(...restored); cart.invoiceDiscount = h1.invoiceDiscount || 0; view.render();
      } }),
      h('button', { class: 'icon-btn', 'aria-label': t('remove'), onclick: () => { SM.prefs.set(heldKey(), held().filter((_, j) => j !== i)); d.close(); resumeDialog(view); } }, icon('trash'))))
      : [h('p', { text: t('sell.held_none') })];
    const d = SM.dialog({ title: t('sell.held_title'), body: [h('p', { class: 'tip', text: t('sell.held_help') }), ...body] });
  }

  async function search(view) {
    const q = h('input', { class: 'input', placeholder: t('sell.search_ph'), autocomplete: 'off', 'data-guide': 'sell.search_box' });
    const list = h('div', { class: 'pick-list' });
    let timer;
    const run = async () => {
      const rows = await SM.api('GET', '/api/products?q=' + encodeURIComponent(q.value)).catch(() => []);
      SM.put(list, ...rows.filter(r => ['grocery', 'raw_meat', 'prepared'].includes(r.kind)).map(r => h('button', { class: 'pick', onclick: async () => {
        d.close();
        if (r.measure === 'g') { const w = await askWeight(r); if (w && !blockOver(r, w.grams)) { cart.key = null; cart.lines.push({ product: r, qty: w.grams, manualWeight: !w.fromScale }); view.render(); } }
        else if (!blockOver(r, 1)) { cart.key = null; addProduct(r, 1, null); view.render(); }
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
    if (!cart.lines.length || cartOver()) return;
    if (cart.unknown) { return verifyUnknown(view); }
    let quote;
    try { quote = await SM.api('POST', '/api/quote', { payload: cartPayload() }); } catch (e) { SM.fail(e); return; }
    const estimate = totals();
    const tot = { total: quote.total, hash: quote.quote_hash };
    // the key belongs to this cart until its answer is known (audit S03)
    cart.key = cart.key || SM.newKey();
    saveDraft();                       // the draft knows this key before any request is sent
    const key = cart.key;
    const cash = h('input', { class: 'input big', inputmode: 'decimal', autocomplete: 'off', maxlength: '12', 'aria-label': t('pay.cash_received'), 'data-guide': 'pay.cash' });
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
        quote.promo ? h('div', { class: 'badge ok', text: t('pay.promo', { amount: SM.money(quote.promo) }) }) : null,
        note ? h('div', { class: 'badge warn', text: note }) : null);
    }
    // a buy-X-get-Y offer is applied by the server: it is not a price change
    showTotal(estimate.total - (quote.promo || 0) !== quote.total ? t('pay.price_updated', { old: SM.money(estimate.total), now: SM.money(quote.total) }) : null);
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
    const payBtn = h('button', { class: 'btn primary lg', 'data-guide': 'pay.confirm', text: t('pay.confirm') });
    let busy = false;
    payBtn.onclick = async () => {
      const v = update();
      if (!v || busy) return;
      busy = true; payBtn.disabled = true; payBtn.textContent = t('pay.saving');
      const payload = Object.assign(cartPayload(), {
        payments: others.map(o => ({ method: o.method, amount: SM.parseMoney(o.amount.value || '0'), reference: o.ref.value || undefined })).filter(p => p.amount > 0),
        cash_tendered: v.due > 0 ? v.got : 0, expected_total: tot.total, quote_hash: tot.hash,
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
          tot.total = e.detail.total; tot.hash = e.detail.quote_hash;
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
    // digits only (31/10/2026 21:03): an Arabic "م" inside the left-to-right number cell printed scrambled on the receipt
    const at = r.at ? new Date(r.at).toLocaleString('en-GB', { timeZone: 'Africa/Cairo', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).replace(',', '') : '—';
    return h('div', { class: 'receipt' + (r.training ? ' training' : '') },
      r.training ? h('div', { class: 'rc-train', text: t('receipt.training') }) : null,
      r.reprint ? h('div', { class: 'rc-train', text: t('receipt.reprint') }) : null,
      h('div', { class: 'rc-shop', text: r.shop_name || t('app.name') }),
      h('div', { class: 'rc-kind', text: t('receipt.not_tax') }),
      L(t('receipt.no'), r.no), L(t('receipt.date'), at), r.cashier ? L(t('receipt.cashier'), r.cashier) : null,
      h('hr'),
      (r.lines || []).map(l => h('div', { class: 'rc-item' }, h('div', { text: lang === 'ar' ? l.name_ar : l.name_en }),
        L(SM.qty(l.qty, l.measure) + ' × ' + SM.money(l.unit_price), SM.money(l.gross)),
        l.promo ? L(t('receipt.promo'), '-' + SM.money(l.promo)) : null,
        l.discount - (l.promo || 0) ? L(t('receipt.discount'), '-' + SM.money(l.discount - (l.promo || 0))) : null)),
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
      const fresh = cart.owner !== ownerOf();
      if (fresh) { resetCart(); cart.owner = ownerOf(); cart.restoring = true; }
      try { shift = await SM.api('GET', '/api/shift'); } catch (e) { shift = null; }
      const scanInput = SM.scanner.field({});
      scanInput.setAttribute('data-guide', 'sell.scan');
      const status = h('div', { class: 'scan-status', role: 'status', 'aria-live': 'polite' });
      const cartBox = h('div', { class: 'cart' });
      const side = h('aside', { class: 'pay-panel card' });
      const view = {
        lastScan(r, raw) {
          SM.put(status);
          if (r.ok) status.append(h('span', { class: 'badge ok' }, icon('check', 'sm'), SM.name(r.product)));
          else status.append(...[h('span', { class: 'badge bad' }, icon('warn', 'sm'), t('err.' + r.error) === 'err.' + r.error ? r.error : t('err.' + r.error)),
            r.seen ? h('code', { class: 'ltr', text: r.seen }) : null].filter(Boolean));     // append(null) would print the word null (report M1)
          if (r.price_changed) status.append(h('span', { class: 'badge warn', text: t('sell.pack_price_changed', { packed: SM.money(r.price_changed.packed), current: SM.money(r.price_changed.current) }) }));
        },
        render() {
          saveDraft();
          checkStock(view);
          const tot = totals();
          SM.put(cartBox, cart.lines.length ? SM.table([
            { label: t('sell.item'), render: l => h('div', {}, h('b', { text: SM.name(l.product) }),
              cart.short && cart.short[l.product.id] !== undefined && !l.pack ? h('div', { class: 'badge bad', text: t('sell.only_available', { qty: SM.qty(cart.short[l.product.id], l.product.measure) }) }) : null,
              l.pack ? h('div', { class: 'muted ltr', text: t('sell.pack') + ' ' + l.pack.code }) : null,
              l.manualWeight ? h('div', { class: 'badge warn', text: t('sell.manual_weight') }) : null) },
            { label: t('sell.qty'), render: l => h('div', { class: 'row qty-cell' },
              !l.pack && l.product.measure !== 'g' ? h('button', { class: 'icon-btn', 'aria-label': t('less'), disabled: l.qty <= 1, onclick: () => { if (l.qty <= 1) return; cart.key = null; l.qty--; view.render(); } }, icon('minus')) : null,
              SM.num(SM.qty(l.qty, l.product.measure)),
              !l.pack && l.product.measure !== 'g' ? h('button', { class: 'icon-btn', 'data-guide': 'sell.more', 'aria-label': t('more'), onclick: () => { if (blockOver(l.product, 1)) return; cart.key = null; l.qty++; view.render(); } }, icon('plus')) : null) },
            { label: t('sell.price'), num: true, render: l => SM.num(l.pack ? SM.money(l.pack.price) : SM.money(l.product.price) + (l.product.measure === 'g' ? ' / ' + t('unit.kg') : '')) },
            { label: t('sell.amount'), num: true, render: l => SM.num(SM.money(lineAmount(l))) },
            { label: '', render: l => h('button', { class: 'icon-btn', 'aria-label': t('remove'), onclick: () => { cart.key = null; cart.lines.splice(cart.lines.indexOf(l), 1); view.render(); } }, icon('trash')) },
          ], cart.lines) : SM.empty(t('sell.empty'), t('sell.empty_hint')));
          SM.put(side,
            cart.restoreFailed ? h('div', { class: 'tip warn-tip', role: 'alert' }, h('div', {}, h('b', { text: t('sell.draft_offline') }),
              h('button', { class: 'btn primary sm block', text: t('sell.draft_retry'), disabled: !!cart.restoreRunning, onclick: () => restoreDraft(view) }))) : null,
            cart.unknown ? h('div', { class: 'tip warn-tip', role: 'alert' }, h('div', {}, h('b', { text: t('pending.sale_unknown') }),
              h('button', { class: 'btn primary sm block', text: t('pending.check'), onclick: () => verifyUnknown(view) }))) : null,
            h('div', { class: 'tot-row' }, h('span', { text: t('sell.items') }), SM.num(String(cart.lines.length))),
            cart.invoiceDiscount ? h('div', { class: 'tot-row' }, h('span', { text: t('sell.discount') }), SM.num('-' + SM.money(cart.invoiceDiscount))) : null,
            h('div', { class: 'tot-big' }, h('span', { text: t('sell.total') }), SM.num(SM.moneyText(tot.total))),
            cartOver() ? h('p', { class: 'badge bad', text: t('sell.pay_blocked') }) : null,
            h('button', { class: 'btn primary lg block', 'data-guide': 'sell.pay', disabled: !cart.lines.length || !shift || cart.unknown || cartOver(), onclick: () => payDialog(view) }, t('sell.pay'), h('span', { class: 'kbd', text: 'F9' })),
            h('button', { class: 'btn ghost block', disabled: !cart.lines.length, onclick: () => discountDialog(view) }, t('sell.discount_btn')),
            h('button', { class: 'btn ghost block', 'data-guide': 'sell.hold', disabled: !cart.lines.length || cart.unknown, onclick: () => holdCart(view) }, t('sell.hold')),
            held().length ? h('button', { class: 'btn ghost block', 'data-guide': 'sell.resume', disabled: cart.unknown, onclick: () => resumeDialog(view) }, t('sell.resume') + ' (' + held().length + ')') : null,
            h('button', { class: 'btn ghost block', disabled: !cart.lines.length, onclick: async () => { if (await SM.confirm(t('sell.clear'), t('sell.clear_q'))) { cart.key = null; cart.lines = []; cart.invoiceDiscount = 0; view.render(); } } }, t('sell.clear')));
          scanInput.focus();
        },
      };
      SM.scanner.handler = (raw) => addScan(raw, view);
      SM.put(root, 
        // the first thing a cashier must do is one big button, not a small link
        shift ? null : h('div', { class: 'tip warn-tip no-shift' }, icon('warn'), h('b', { text: t('sell.no_shift') }),
          h('button', { class: 'btn primary big', id: 'open-shift-now', text: t('sell.open_shift'), onclick: () => SM.go('cash') })),
        h('div', { class: 'sell-grid' },
          h('section', { class: 'sell-main' },
            h('div', { class: 'scan-bar card' }, icon('scan', 'lg'), scanInput,
              h('button', { class: 'btn', 'data-guide': 'sell.search', onclick: () => search(view) }, icon('search'), t('sell.search'), h('span', { class: 'kbd', text: 'F4' }))),
            status, cartBox),
          side));
      view.render();
      if (fresh || cart.restoring) restoreDraft(view);   // a read-back cut short by a second start-up is done again
      this.keys = (e) => {
        if (SM.state.dialogOpen) return;
        if (e.key === 'F9') { e.preventDefault(); if (cart.lines.length && shift && !cartOver()) payDialog(view); }
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
