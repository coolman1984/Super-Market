/* Goods screens: receive, stock & lots, preparation (cutting / tagines), count. */
'use strict';

(function () {
  const { h, t, icon } = SM;
  const cache = {};
  async function locations() { return cache.loc || (cache.loc = await SM.api('GET', '/api/locations')); }
  const locName = (l) => SM.name(l);
  function select(options, value, placeholder) {
    const s = h('select', { class: 'input' }, placeholder ? h('option', { value: '', text: placeholder }) : null,
      options.map(o => h('option', { value: o.value, text: o.label })));
    if (value !== undefined && value !== null) s.value = String(value);
    return s;
  }
  SM.select = select;

  /* ---------------- receive ---------------- */
  SM.views.receive = {
    icon: 'truck', perm: 'receive',
    async mount(root) {
      const locs = await locations();
      const suppliers = await SM.api('GET', '/api/parties?kind=supplier').catch(() => []);
      const lines = [];
      const sup = select(suppliers.map(s => ({ value: s.id, label: s.name })), null, t('recv.no_supplier'));
      const inv = h('input', { class: 'input', maxlength: '60' });
      const invDate = h('input', { class: 'input', type: 'date', value: SM.today() });
      const invDisc = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
      const extra = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
      const paid = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
      const table = h('div');
      const key = { value: SM.newKey() };
      const scan = SM.scanner.field({ placeholder: t('recv.scan_ph') });
      SM.scanner.handler = async (raw) => {
        const r = await SM.api('POST', '/api/scan', { raw }).catch(e => { SM.fail(e); return null; });
        if (!r) return;
        if (!r.ok && !r.product) { SM.toast(t('err.' + r.error), 'bad'); return; }
        addLine(r.product, r);
      };
      function addLine(product, scan) {
        const l = { product, qty: h('input', { class: 'input', inputmode: 'decimal' }), packs: null, packaging: scan && scan.packaging,
          gross: h('input', { class: 'input', inputmode: 'decimal' }), tare: h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' }),
          free: h('input', { class: 'input', inputmode: 'numeric', placeholder: '0' }), rejected: h('input', { class: 'input', inputmode: 'numeric', placeholder: '0' }),
          cost: h('input', { class: 'input', inputmode: 'decimal' }), expires: h('input', { class: 'input', type: 'date' }),
          lot: h('input', { class: 'input', maxlength: '60' }), hold: h('input', { type: 'checkbox' }),
          loc: select(locs.map(x => ({ value: x.id, label: locName(x) })), (locs.find(x => x.kind === (product.measure === 'g' ? 'prep_fridge' : 'shelf')) || locs[0]).id) };
        if (l.packaging) { l.packs = h('input', { class: 'input', inputmode: 'numeric', value: '1' }); }
        if (scan && scan.expiry_hint) l.expires.value = scan.expiry_hint;
        if (scan && scan.lot_hint) l.lot.value = scan.lot_hint;
        lines.push(l); draw();
      }
      function draw() {
        SM.put(table, lines.length ? h('div', { class: 'stack' }, lines.map((l, i) => h('div', { class: 'card recv-line' },
          h('header', {}, h('h3', { text: SM.name(l.product) }), h('span', { class: 'muted ltr', text: l.product.code }),
            h('button', { class: 'icon-btn', 'aria-label': t('remove'), onclick: () => { lines.splice(i, 1); draw(); } }, icon('trash'))),
          h('div', { class: 'grid cols-4' },
            l.packs ? SM.field(t('recv.packs', { name: SM.name(l.packaging) }), l.packs) :
              l.product.measure === 'g' ? [SM.field(t('recv.gross_kg'), l.gross, t('recv.gross_help')), SM.field(t('recv.tare_kg'), l.tare)] :
                SM.field(t('recv.qty_units'), l.qty),
            SM.field(t('recv.free'), l.free), SM.field(t('recv.rejected'), l.rejected),
            SM.field(t('recv.line_cost'), l.cost, t('recv.line_cost_help')),
            SM.field(t('recv.expires'), l.expires, l.product.needs_expiry ? t('recv.expiry_required') : t('recv.expiry_optional')),
            SM.field(t('recv.supplier_lot'), l.lot), SM.field(t('recv.location'), l.loc),
            h('label', { class: 'row' }, l.hold, t('recv.hold'))))))
          : SM.empty(t('recv.empty'), t('recv.empty_hint')));
      }
      const pick = h('button', { class: 'btn', onclick: async () => {
        const rows = await SM.api('GET', '/api/products?limit=200');
        const s = select(rows.map(r => ({ value: r.id, label: SM.name(r) + ' (' + r.code + ')' })), null, t('choose'));
        const d = SM.dialog({ title: t('recv.add_product'), body: [s], actions: [h('button', { class: 'btn primary', text: t('add'), onclick: () => {
          const r = rows.find(x => String(x.id) === s.value); if (r) addLine(r); d.close(); } })] });
      } }, icon('plus'), t('recv.add_product'));
      const post = h('button', { class: 'btn primary lg', text: t('recv.post') });
      post.onclick = async () => {
        try {
          const money = (el) => el.value ? SM.parseMoney(el.value) : 0;
          const payload = { supplier_id: sup.value ? parseInt(sup.value, 10) : undefined, supplier_invoice: inv.value || undefined,
            invoice_date: invDate.value || undefined, invoice_discount: money(invDisc), extra_cost: money(extra), paid_now: money(paid),
            lines: lines.map(l => {
              const line = { product_id: l.product.id, line_cost: money(l.cost), free_qty: SM.parseInt(l.free.value || '0'),
                rejected_qty: SM.parseInt(l.rejected.value || '0'), expires: l.expires.value || undefined, supplier_lot: l.lot.value || undefined,
                location_id: parseInt(l.loc.value, 10), hold: l.hold.checked };
              if (l.packs) { line.packaging_id = l.packaging.id; line.packs = SM.parseInt(l.packs.value); }
              else if (l.product.measure === 'g') { line.gross_g = SM.parseKg(l.gross.value); line.tare_g = SM.parseKg(l.tare.value || '0'); }
              else line.qty = SM.parseInt(l.qty.value);
              return line;
            }) };
          let r;
          try { r = await SM.command('/api/receipts', key.value, payload); }
          catch (e) {
            if (e.code !== 'duplicate_supplier_invoice' || !(await SM.confirm(t('recv.dup_title'), t('recv.dup_text')))) throw e;
            payload.confirm_duplicate = true; key.value = SM.newKey();
            r = await SM.command('/api/receipts', key.value, payload);
          }
          SM.toast(t('recv.done', { n: r.lines.length }));
          lines.length = 0; key.value = SM.newKey(); draw();
        } catch (e) { SM.fail(e); }
      };
      SM.put(root, 
        h('div', { class: 'card' }, h('div', { class: 'grid cols-3' }, SM.field(t('recv.supplier'), sup), SM.field(t('recv.invoice'), inv), SM.field(t('recv.invoice_date'), invDate))),
        h('div', { class: 'scan-bar card' }, icon('scan', 'lg'), scan, pick),
        table,
        h('div', { class: 'card' }, h('div', { class: 'grid cols-3' }, SM.field(t('recv.invoice_discount'), invDisc), SM.field(t('recv.extra_cost'), extra, t('recv.extra_help')),
          SM.field(t('recv.paid_now'), paid, t('recv.paid_help'))), h('div', { class: 'form-actions' }, post)));
      draw();
    },
    unmount() { SM.scanner.handler = null; },
  };

  /* ---------------- stock & lots ---------------- */
  async function lotDrawer(lotId, after) {
    const tr = await SM.api('GET', '/api/trace/' + lotId).catch(e => { SM.fail(e); return null; });
    if (!tr) return;
    const lot = tr.lot;
    const kv = (k, v) => [h('dt', { text: k }), h('dd', {}, v)];
    const acts = [];
    const doStatus = async (status) => {
      const reason = prompt(t('lots.reason_q'));
      if (!reason) return;
      try { await SM.command('/api/lots/status', SM.newKey(), undefined, { lot_id: lot.id, status, reason }); SM.toast(t('saved')); d.close(); after && after(); } catch (e) { SM.fail(e); }
    };
    if (lot.status === 'released') acts.push(h('button', { class: 'btn', onclick: () => doStatus('held') }, t('lots.hold')));
    else acts.push(h('button', { class: 'btn', onclick: () => doStatus('released') }, t('lots.release')));
    if (SM.can('recall') && lot.status !== 'recalled') acts.push(h('button', { class: 'btn danger', onclick: async () => {
      const reason = prompt(t('lots.recall_q')); if (!reason) return;
      try { const r = await SM.command('/api/recall', SM.newKey(), undefined, { lot_id: lot.id, reason }); d.close(); recallResult(r); after && after(); } catch (e) { SM.fail(e); }
    } }, t('lots.recall')));
    const d = SM.drawer({ title: t('lots.title', { no: lot.lot_no }), actions: acts, body: [
      h('dl', { class: 'kv' }, kv(t('lots.product'), SM.name(lot)), kv(t('lots.status'), SM.badge(lot.status)),
        kv(t('lots.expires'), lot.expires || t('lots.no_expiry')), kv(t('lots.origin'), t('origin.' + lot.origin)),
        kv(t('lots.allergens'), lot.allergens || t('none'))),
      h('h3', { text: t('lots.parents') }), tr.parents.length ? h('ul', {}, tr.parents.map(p => h('li', {}, h('a', { href: '#', onclick: (e) => { e.preventDefault(); d.close(); lotDrawer(p.id, after); } }, p.lot_no), ' — ', SM.name(p)))) : h('p', { class: 'muted', text: t('none') }),
      h('h3', { text: t('lots.children') }), tr.children.length ? h('ul', {}, tr.children.map(p => h('li', {}, h('a', { href: '#', onclick: (e) => { e.preventDefault(); d.close(); lotDrawer(p.id, after); } }, p.lot_no), ' — ', SM.name(p), ' ', SM.badge(p.status)))) : h('p', { class: 'muted', text: t('none') }),
      h('h3', { text: t('lots.sales') }), tr.sales.length ? h('ul', {}, tr.sales.map(s => h('li', { class: 'ltr', text: s.no + ' — ' + s.at.slice(0, 16) }))) : h('p', { class: 'muted', text: t('none') }),
      h('h3', { text: t('lots.events') }), h('ul', {}, tr.events.map(e => h('li', {}, SM.badge(e.status), ' ', e.reason, ' ', h('span', { class: 'muted ltr', text: e.at.slice(0, 16) })))),
    ] });
  }
  SM.lotDrawer = lotDrawer;
  function recallResult(r) {
    SM.dialog({ title: t('recall.done'), wide: true, body: [
      h('p', { text: t('recall.summary', { lots: r.lots.length, sales: r.sales.length, packs: r.packs.length }) }),
      h('h3', { text: t('lots.sales') }), h('ul', {}, r.sales.map(s => h('li', { class: 'ltr', text: s.no + ' — ' + s.at.slice(0, 16) + (s.customer_id ? ' — customer #' + s.customer_id : '') }))),
      h('h3', { text: t('recall.stock') }), h('ul', {}, r.stock.map(s => h('li', { class: 'ltr', text: s.lot_no + ': ' + s.qty }))),
    ] });
  }

  SM.views.stock = {
    icon: 'box',
    async mount(root) {
      const filter = h('input', { class: 'input', placeholder: t('stock.filter') });
      const states = ['all', 'near_expiry', 'expired', 'held', 'recalled'];
      let state = 'all';
      const seg = h('div', { class: 'seg seg-scroll' }, states.map(s => h('button', { 'aria-pressed': s === state ? 'true' : 'false', text: t('stock.f.' + s), onclick: (e) => {
        state = s; seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', 'false')); e.target.setAttribute('aria-pressed', 'true'); draw(); } })));
      const box = h('div');
      let rows = [];
      const load = async () => { rows = await SM.api('GET', '/api/stock').catch(e => { SM.fail(e); return []; }); draw(); };
      function draw() {
        const q = filter.value.trim().toLowerCase();
        const shown = rows.filter(r => (state === 'all' || r.state === state) && (!q || (r.name_ar + r.name_en + r.code + r.lot_no).toLowerCase().includes(q)));
        SM.put(box, shown.length ? SM.table([
          { label: t('stock.product'), render: r => h('div', {}, h('b', { text: SM.name(r) }), h('div', { class: 'muted' }, h('span', { class: 'ltr', text: r.code }))) },
          { label: t('stock.lot'), render: r => h('span', { class: 'ltr', text: r.lot_no }) },
          { label: t('stock.location'), render: r => r.sellable ? (SM.state.lang === 'ar' ? r.loc_ar : r.loc_en) : h('span', {}, SM.state.lang === 'ar' ? r.loc_ar : r.loc_en, ' ', h('span', { class: 'badge', text: t('stock.not_sellable') })) },
          { label: t('stock.expires'), render: r => h('span', { class: 'ltr', text: r.expires || '—' }) },
          { label: t('stock.state'), render: r => SM.badge(r.state) },
          { label: t('stock.qty'), num: true, render: r => SM.num(SM.qty(r.qty, r.measure)) },
          SM.can('see_cost') ? { label: t('stock.cost'), num: true, render: r => SM.num(SM.money(r.cost)) } : null,
        ].filter(Boolean), shown, r => lotDrawer(r.lot_id, load)) : SM.empty(t('stock.empty')));
      }
      filter.addEventListener('input', draw);
      const actions = h('div', { class: 'toolbar' }, filter, seg,
        SM.can('waste') ? h('button', { class: 'btn', onclick: () => moveDialog('waste', rows, load) }, t('stock.waste')) : null,
        SM.can('transfer') ? h('button', { class: 'btn', onclick: () => moveDialog('transfer', rows, load) }, t('stock.transfer')) : null,
        SM.can('waste') ? h('button', { class: 'btn', onclick: () => wastePackDialog(load) }, t('stock.waste_pack')) : null,
        SM.can('receive') ? h('button', { class: 'btn', onclick: () => supplierReturnDialog(rows, load) }, t('stock.supplier_return')) : null,
        SM.can('cutting') || SM.can('produce') ? h('button', { class: 'btn ghost', onclick: () => reprintDialog() }, t('prep.reprint')) : null,
        SM.can('recall') ? h('button', { class: 'btn', onclick: () => recallsDialog(load) }, t('recall.list')) : null,
        SM.can('export') ? h('a', { class: 'btn ghost', href: '/api/export/stock.csv', download: 'stock.csv' }, t('stock.export')) : null);
      SM.put(root, actions, box);
      load();
    },
  };

  /* Audit R12: stock promised to a labelled pack leaves only with that pack. */
  function wastePackDialog(after) {
    const code = SM.scanner.field({ placeholder: t('stock.pack_scan'), onEnter: (raw) => { code.value = raw.trim(); } });
    const reason = SM.select(['expired', 'damaged', 'cold_chain', 'internal_use', 'sample', 'owner_draw', 'abnormal_loss', 'unknown'].map(v => ({ value: v, label: t('waste.' + v) })));
    const key = SM.newKey();
    const d = SM.dialog({ title: t('stock.waste_pack'), body: [SM.field(t('sell.pack'), code), SM.field(t('waste.reason'), reason)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        try { await SM.command('/api/packs/waste', key, undefined, { code: code.value.trim(), reason: reason.value }); d.close(); SM.toast(t('saved')); after(); }
        catch (e) { SM.fail(e); }
      }) })] });
  }

  /* Goods back to a supplier: stock leaves at cost; the debt changes only with the credit note. */
  async function supplierReturnDialog(rows, after) {
    const suppliers = await SM.api('GET', '/api/parties?kind=supplier').catch(() => []);
    const sup = select(suppliers.map(x => ({ value: x.id, label: x.name })), null, t('choose'));
    const lot = select(rows.map(r => ({ value: r.lot_id + ':' + r.location_id, label: SM.name(r) + ' — ' + r.lot_no + ' — ' + SM.qty(r.qty, r.measure) })), null, t('choose'));
    const qty = h('input', { class: 'input', inputmode: 'decimal' });
    const reason = select(['damaged', 'wrong_item', 'weight_dispute', 'expired', 'quality', 'other'].map(v => ({ value: v, label: t('reason.' + v) })));
    const key = SM.newKey();
    const d = SM.dialog({ title: t('stock.supplier_return'), body: [h('p', { class: 'tip', text: t('stock.supplier_return_help') }),
      SM.field(t('recv.supplier'), sup), SM.field(t('stock.lot'), lot), SM.field(t('stock.qty_help'), qty), SM.field(t('party.return_reason'), reason)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        const [lotId, locId] = lot.value.split(':').map(Number);
        const row = rows.find(r => r.lot_id === lotId && r.location_id === locId);
        if (!row || !sup.value) return;
        try {
          await SM.command('/api/supplier-returns', key, undefined, { supplier_id: parseInt(sup.value, 10), lot_id: lotId, location_id: locId,
            qty: row.measure === 'g' ? SM.parseKg(qty.value) : SM.parseInt(qty.value), reason: reason.value });
          d.close(); SM.toast(t('party.return_done')); after();
        } catch (e) { SM.fail(e); }
      }) })] });
  }

  /* Reprint a label: same code, no new pack, no stock movement. */
  function reprintDialog() {
    const code = SM.scanner.field({ placeholder: t('stock.pack_scan'), onEnter: (raw) => { code.value = raw.trim(); } });
    const out = h('div');
    const d = SM.dialog({ title: t('prep.reprint'), body: [h('p', { class: 'tip', text: t('prep.reprint_help') }), SM.field(t('sell.pack'), code), out],
      actions: [h('button', { class: 'btn primary', text: t('prep.reprint'), onclick: SM.guard(async () => {
        const l = await SM.api('GET', '/api/packs/' + encodeURIComponent(code.value.trim()) + '/label').catch(e => { SM.fail(e); return null; });
        if (!l) return;
        SM.put(out, SM.packLabel({ code: l.code, net_g: l.net_g, meat_g: l.meat_g, meat_basis: l.meat_basis, price: l.price }, { name_ar: l.name_ar, name_en: l.name_en, expires: l.expires, allergens: l.allergens }));
        const area = document.getElementById('print') || document.body.appendChild(h('div', { id: 'print' }));
        area.className = 'labels-print'; SM.put(area, SM.packLabel({ code: l.code, net_g: l.net_g, meat_g: l.meat_g, meat_basis: l.meat_basis, price: l.price }, { name_ar: l.name_ar, name_en: l.name_en, expires: l.expires, allergens: l.allergens }));
        window.print();
      }) })] });
  }

  /* Audit R03: a recall ends only by a separate, written decision; lots then
     still need inspection and release one by one. */
  async function recallsDialog(after) {
    const rows = await SM.api('GET', '/api/recalls').catch(e => { SM.fail(e); return []; });
    const d = SM.dialog({ title: t('recall.list'), wide: true, body: rows.length ? SM.table([
      { label: t('stock.lot'), render: r => h('span', { class: 'ltr', text: r.lot_no }) }, { label: t('stock.product'), render: r => SM.name(r) },
      { label: t('ret.reason'), key: 'reason' },
      { label: t('stock.state'), render: r => r.lifted_at ? h('span', { class: 'badge ok', text: t('recall.lifted') + ': ' + r.lift_reason }) :
        h('button', { class: 'btn sm', text: t('recall.lift'), onclick: SM.guard(async () => {
          const reason = prompt(t('recall.lift_q')); if (!reason) return;
          try { await SM.command('/api/recalls/lift', SM.newKey(), undefined, { recall_id: r.id, reason }); SM.toast(t('recall.lifted_toast')); d.close(); after(); }
          catch (e) { SM.fail(e); }
        }) }) }], rows) : h('p', { text: t('none') }) });
  }

  async function moveDialog(kind, rows, after) {
    const locs = await locations();
    const lot = select(rows.map(r => ({ value: r.lot_id + ':' + r.location_id, label: SM.name(r) + ' — ' + r.lot_no + ' — ' + (SM.state.lang === 'ar' ? r.loc_ar : r.loc_en) + ' — ' + SM.qty(r.qty, r.measure) })), null, t('choose'));
    const qty = h('input', { class: 'input', inputmode: 'decimal' });
    const reason = select(['expired', 'damaged', 'cold_chain', 'internal_use', 'sample', 'owner_draw', 'abnormal_loss', 'unknown'].map(v => ({ value: v, label: t('waste.' + v) })));
    const to = select(locs.map(l => ({ value: l.id, label: locName(l) })));
    const key = SM.newKey();
    const d = SM.dialog({ title: t('stock.' + kind), body: [SM.field(t('stock.lot'), lot), SM.field(t('stock.qty_help'), qty),
      kind === 'waste' ? SM.field(t('waste.reason'), reason) : SM.field(t('stock.to'), to)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
        const [lotId, locId] = lot.value.split(':').map(Number);
        const row = rows.find(r => r.lot_id === lotId && r.location_id === locId);
        if (!row) return;
        const q = row.measure === 'g' ? SM.parseKg(qty.value) : SM.parseInt(qty.value);
        try {
          if (kind === 'waste') await SM.command('/api/waste', key, undefined, { lot_id: lotId, location_id: locId, qty: q, reason: reason.value });
          else await SM.command('/api/transfer', key, undefined, { lot_id: lotId, from: locId, to: parseInt(to.value, 10), qty: q });
          d.close(); SM.toast(t('saved')); after();
        } catch (e) { SM.fail(e); }
      } })] });
  }

  /* ---------------- prepare: cutting & tagines ---------------- */
  SM.views.prepare = {
    icon: 'knife',
    async mount(root) {
      const stock = await SM.api('GET', '/api/stock').catch(() => []);
      const products = await SM.api('GET', '/api/products?limit=500').catch(() => []);
      const locs = await locations();
      let mode = SM.can('cutting') ? 'cutting' : 'recipe';
      const body = h('div');
      const seg = h('div', { class: 'seg' }, ['cutting', 'recipe'].map(m => h('button', { 'aria-pressed': m === mode ? 'true' : 'false', text: t('prep.' + m), onclick: (e) => {
        mode = m; seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', 'false')); e.target.setAttribute('aria-pressed', 'true'); draw(); } })));
      const usable = stock.filter(r => r.state === 'available' || r.state === 'near_expiry');
      const sellLoc = (locs.find(l => l.kind === 'display_fridge') || locs[0]).id;
      async function draw() {
        const key = SM.newKey();
        const inputs = [], outputs = [];
        const inBox = h('div', { class: 'stack' }), outBox = h('div', { class: 'stack' });
        const natural = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
        const abnormal = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
        const balance = h('div', { class: 'mass' });
        let version = null;
        const packsN = h('input', { class: 'input', inputmode: 'numeric', placeholder: '0' });
        const packG = h('input', { class: 'input', inputmode: 'decimal' });
        const packTare = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
        const addInput = (filter) => {
          const s = select(usable.filter(filter || (() => true)).map(r => ({ value: r.lot_id + ':' + r.location_id, label: SM.name(r) + ' — ' + r.lot_no + ' — ' + (r.expires || '') + ' — ' + SM.qty(r.qty, r.measure) })), null, t('choose'));
          const q = h('input', { class: 'input', inputmode: 'decimal' });
          const row = { s, q }; inputs.push(row);
          s.onchange = q.oninput = calc;
          inBox.append(h('div', { class: 'grid cols-2' }, SM.field(t('prep.input_lot'), s), SM.field(t('prep.input_qty'), q)));
        };
        const addOutput = (pid) => {
          const s = select(products.filter(p => p.measure === 'g' && ['raw_meat', 'prepared'].includes(p.kind)).map(p => ({ value: p.id, label: SM.name(p) })), pid, t('choose'));
          const q = h('input', { class: 'input', inputmode: 'decimal' });
          const loc = select(locs.map(l => ({ value: l.id, label: locName(l) })), sellLoc);
          const row = { s, q, loc }; outputs.push(row);
          q.oninput = calc;
          outBox.append(h('div', { class: 'grid cols-3' }, SM.field(t('prep.output'), s), SM.field(t('prep.output_kg'), q), SM.field(t('recv.location'), loc)));
        };
        function measureOf(row) { const [lot] = row.s.value.split(':').map(Number); const r = usable.find(x => x.lot_id === lot); return r ? r : null; }
        function calc() {
          let food = 0;
          for (const i of inputs) { const r = measureOf(i); if (r && r.measure === 'g' && r.kind !== 'packaging') food += SM.parseKg(i.q.value || '0') || 0; }
          const out = outputs.reduce((a, o) => a + (SM.parseKg(o.q.value || '0') || 0), 0);
          const loss = (SM.parseKg(natural.value || '0') || 0) + (SM.parseKg(abnormal.value || '0') || 0);
          const diff = food - out - loss;
          SM.put(balance, h('span', {}, t('prep.in'), ' ', SM.num(SM.qty(food, 'g'))), h('span', {}, t('prep.out'), ' ', SM.num(SM.qty(out, 'g'))),
            h('span', {}, t('prep.loss'), ' ', SM.num(SM.qty(loss, 'g'))),
            diff === 0 ? h('span', { class: 'badge ok', text: t('prep.balanced') }) : h('span', { class: 'badge bad', text: t('prep.unbalanced', { g: SM.qty(diff, 'g') }) }));
        }
        natural.oninput = abnormal.oninput = calc;
        let recipeBox = null;
        if (mode === 'recipe') {
          const prepared = products.filter(p => p.kind === 'prepared');
          const ps = select(prepared.map(p => ({ value: p.id, label: SM.name(p) })), null, t('choose'));
          recipeBox = h('div', { class: 'card' }, SM.field(t('prep.recipe_product'), ps), h('div', { class: 'recipe-info' }));
          ps.onchange = async () => {
            const versions = await SM.api('GET', '/api/recipes/' + ps.value).catch(() => []);
            version = versions[0] || null;
            const info = recipeBox.querySelector('.recipe-info');
            if (!version) { SM.put(info, h('p', { class: 'tip', text: t('prep.no_recipe') })); return; }
            SM.put(info, h('p', {}, t('prep.version', { v: version.version, days: version.shelf_life_days }), ' ', version.allergens ? h('span', { class: 'badge warn', text: t('lots.allergens') + ': ' + version.allergens }) : null),
              h('ul', {}, version.components.map(c => h('li', {}, SM.name(c), ' — ', SM.qty(c.qty, c.measure), ' (', t('role.' + c.role), ')'))));
            inputs.length = 0; outputs.length = 0; SM.put(inBox); SM.put(outBox);
            for (const c of version.components) addInput(r => r.product_id === c.product_id);
            addOutput(parseInt(ps.value, 10));
            if (version.target_pack_g) packG.value = SM.qty(version.target_pack_g, 'g').split(' ')[0];
            calc();
          };
        } else { addInput(r => r.kind === 'raw_meat'); addOutput(); }
        const unpackBox = h('div', { class: 'stack' });
        const unpackChecks = [];
        const refreshUnpack = async () => {
          unpackChecks.length = 0;
          const seenLots = new Set();
          const parts = [];
          for (const i of inputs) {
            if (!i.s.value || seenLots.has(i.s.value)) continue;
            seenLots.add(i.s.value);
            const [lot, loc] = i.s.value.split(':').map(Number);
            const packs = await SM.api('GET', '/api/packs?lot=' + lot + '&location=' + loc).catch(() => []);
            for (const pk of packs) {
              const c = h('input', { type: 'checkbox' }); unpackChecks.push({ code: pk.code, c });
              parts.push(h('label', { class: 'row' }, c, h('span', { class: 'ltr', text: pk.code }), ' ', SM.num(SM.qty(pk.net_g, 'g'))));
            }
          }
          SM.put(unpackBox, parts.length ? [h('h4', { text: t('prep.unpack') }), h('p', { class: 'muted', text: t('prep.unpack_help') }), ...parts] : null);
        };
        inBox.addEventListener('change', refreshUnpack);
        const post = h('button', { class: 'btn primary lg', text: t('prep.post') });
        post.onclick = async () => {
          const payload = { kind: mode, recipe_version_id: version ? version.id : undefined,
            inputs: inputs.filter(i => i.s.value).map(i => { const [lot, loc] = i.s.value.split(':').map(Number); const r = measureOf(i);
              return { lot_id: lot, location_id: loc, qty: r.measure === 'g' ? SM.parseKg(i.q.value) : SM.parseInt(i.q.value) }; }),
            outputs: outputs.filter(o => o.s.value).map(o => ({ product_id: parseInt(o.s.value, 10), qty: SM.parseKg(o.q.value), location_id: parseInt(o.loc.value, 10) })),
            natural_loss_g: SM.parseKg(natural.value || '0'), abnormal_loss_g: SM.parseKg(abnormal.value || '0'),
            unpack: unpackChecks.filter(x => x.c.checked).map(x => x.code) };
          const n = SM.parseInt(packsN.value || '0');
          if (mode === 'recipe' && n) {
            const each = SM.parseKg(packG.value), tare = SM.parseKg(packTare.value || '0');
            payload.packs = Array.from({ length: n }, () => ({ net_g: each, tare_g: tare }));
          }
          try {
            const r = await SM.commandWithApproval('/api/production', key, payload);
            SM.toast(t('prep.done'));
            const packs = (r.outputs[0] || {}).packs || [];
            SM.dialog({ title: t('prep.done'), wide: true, body: [
              h('ul', {}, r.outputs.map(o => h('li', {}, h('b', { class: 'ltr', text: o.lot_no }), ' — ', SM.qty(o.qty, 'g'), ' — ', t('lots.expires'), ' ', h('span', { class: 'ltr', text: o.expires }), o.allergens ? ' — ' + t('lots.allergens') + ': ' + o.allergens : ''))),
              packs.length ? h('div', { class: 'labels' }, packs.map(p => SM.packLabel(p, r.outputs[0]))) : null],
              actions: packs.length ? [h('button', { class: 'btn primary', onclick: () => { const area = document.getElementById('print') || document.body.appendChild(h('div', { id: 'print' })); area.className = 'labels-print'; SM.put(area, ...packs.map(p => SM.packLabel(p, r.outputs[0]))); window.print(); } }, icon('print'), t('prep.print_labels'))] : null });
            draw();
          } catch (e) { SM.fail(e); }
        };
        SM.put(body, recipeBox || h('p', { class: 'tip', text: t('prep.cutting_tip') }),
          h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('prep.inputs') }), h('button', { class: 'btn sm', onclick: () => { addInput(); } }, icon('plus', 'sm'), t('add'))), inBox, unpackBox),
          h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('prep.outputs') }), mode === 'cutting' ? h('button', { class: 'btn sm', onclick: () => addOutput() }, icon('plus', 'sm'), t('add')) : null), outBox,
            mode === 'recipe' ? h('div', { class: 'grid cols-3' }, SM.field(t('prep.packs_n'), packsN), SM.field(t('prep.pack_kg'), packG, t('prep.pack_help')), SM.field(t('prep.tare_kg'), packTare)) : null),
          h('div', { class: 'card' }, h('div', { class: 'grid cols-2' }, SM.field(t('prep.natural'), natural, t('prep.natural_help')), SM.field(t('prep.abnormal'), abnormal, t('prep.abnormal_help'))), balance,
            h('div', { class: 'form-actions' }, post)));
        calc();
      }
      SM.put(root, h('div', { class: 'toolbar' }, seg), body);
      draw();
    },
  };

  /* Pack label: the code is printed as digits and as an EAN-13 drawing. */
  SM.packLabel = function (p, out) {
    return h('div', { class: 'pack-label' }, h('b', { text: SM.name(out) || '' }),
      h('div', {}, t('label.net'), ' ', SM.num(SM.qty(p.net_g, 'g'))), p.meat_g ? h('div', {}, t(p.meat_basis === 'estimated' ? 'label.meat_estimated' : 'label.meat'), ' ', SM.num(SM.qty(p.meat_g, 'g'))) : null,
      h('div', {}, t('label.price'), ' ', SM.num(SM.moneyText(p.price))), h('div', {}, t('label.expires'), ' ', h('span', { class: 'ltr', text: out.expires })),
      out.allergens ? h('div', {}, t('lots.allergens'), ': ', out.allergens) : null, SM.ean13(p.code), h('div', { class: 'ltr mono', text: p.code }));
  };
  const EAN = { L: ['0001101', '0011001', '0010011', '0111101', '0100011', '0110001', '0101111', '0111011', '0110111', '0001011'],
    G: ['0100111', '0110011', '0011011', '0100001', '0011101', '0111001', '0000101', '0010001', '0001001', '0010111'],
    R: ['1110010', '1100110', '1101100', '1000010', '1011100', '1001110', '1010000', '1000100', '1001000', '1110100'],
    P: ['LLLLLL', 'LLGLGG', 'LLGGLG', 'LLGGGL', 'LGLLGG', 'LGGLLG', 'LGGGLL', 'LGLGLG', 'LGLGGL', 'LGGLGL'] };
  SM.ean13 = function (code) {
    if (!/^\d{13}$/.test(code)) return null;
    const d = code.split('').map(Number);
    let bits = '101';
    for (let i = 1; i <= 6; i++) bits += EAN[EAN.P[d[0]][i - 1]][d[i]];
    bits += '01010';
    for (let i = 7; i <= 12; i++) bits += EAN.R[d[i]];
    bits += '101';
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + (bits.length + 18) + ' 50'); svg.setAttribute('class', 'ean'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', code);
    for (let i = 0; i < bits.length; i++) if (bits[i] === '1') {
      const r = document.createElementNS(ns, 'rect'); r.setAttribute('x', 9 + i); r.setAttribute('y', 0); r.setAttribute('width', 1); r.setAttribute('height', 50); svg.append(r);
    }
    return svg;
  };

  /* ---------------- count ---------------- */
  SM.views.count = {
    icon: 'list', perm: 'count',
    async mount(root) {
      const locs = await locations();
      const list = await SM.api('GET', '/api/counts').catch(() => []);
      const loc = select(locs.map(l => ({ value: l.id, label: locName(l) })));
      const open = h('button', { class: 'btn primary', text: t('count.open'), onclick: async () => {
        try { const r = await SM.api('POST', '/api/counts', { location_id: parseInt(loc.value, 10) }); sheet(r.count); } catch (e) { SM.fail(e); }
      } });
      SM.put(root, h('div', { class: 'card' }, h('p', { class: 'tip', text: t('count.blind_tip') }), h('div', { class: 'row wrap' }, SM.field(t('count.location'), loc), open)),
        list.length ? SM.table([{ label: '#', key: 'id' }, { label: t('count.location'), render: r => SM.name(r) }, { label: t('stock.state'), render: r => SM.badge(r.status) },
          { label: t('count.opened'), render: r => h('span', { class: 'ltr', text: r.opened_at.slice(0, 16) }) }], list, r => r.status === 'open' && sheet(r.id)) : null);
      async function sheet(id) {
        const s = await SM.api('GET', '/api/counts/' + id);
        const filter = SM.scanner.field({ placeholder: t('count.scan_ph'), onEnter: async (raw) => {
          const r = await SM.api('POST', '/api/scan', { raw }).catch(() => null);
          const pid = r && r.product && r.product.id;
          rows.forEach(x => x.el.hidden = pid ? x.lot.code !== r.product.code : false);
          const first = rows.find(x => !x.el.hidden); if (first) first.input.focus();
        } });
        const rows = s.lots.map(l => {
          const input = h('input', { class: 'input', inputmode: 'decimal', value: l.counted === null ? '' : (l.measure === 'g' ? SM.qty(l.counted, 'g').split(' ')[0] : String(l.counted)) });
          const saved = h('span', {});
          input.addEventListener('change', async () => {
            const v = l.measure === 'g' ? SM.parseKg(input.value) : SM.parseInt(input.value);
            if (v === null) { SM.put(saved, h('span', { class: 'badge bad', text: t('err.bad_number') })); return; }
            try { await SM.api('POST', '/api/counts/' + id + '/line', { lot_id: l.lot_id, counted: v }); SM.put(saved, h('span', { class: 'badge ok', text: t('saved') })); } catch (e) { SM.fail(e); }
          });
          const el = h('div', { class: 'count-row' }, h('div', {}, h('b', { text: SM.name(l) }), h('div', { class: 'muted ltr', text: l.lot_no + ' — ' + (l.expires || '') })),
            h('div', { class: 'row' }, input, h('span', { class: 'muted', text: l.measure === 'g' ? t('unit.kg') : t('unit.pc') }), saved));
          return { lot: l, input, el };
        });
        const key = SM.newKey();
        const cancel = h('button', { class: 'btn ghost', text: t('count.cancel'), onclick: SM.guard(async () => {
          const reason = prompt(t('count.cancel_q')); if (!reason) return;
          try { await SM.api('POST', '/api/counts/' + id + '/cancel', { reason }); SM.toast(t('count.cancelled')); SM.go('count'); } catch (e) { SM.fail(e); }
        }) });
        const approve = SM.can('count_approve') ? h('button', { class: 'btn primary', text: t('count.approve'), onclick: async () => {
          if (!(await SM.confirm(t('count.approve'), t('count.approve_q')))) return;
          try {
            let extra;
            try { await SM.command('/api/counts/' + id + '/approve', key, undefined); }
            catch (e) {
              if (e.code !== 'approver_counted' || SM.state.user.role !== 'owner') throw e;
              const reason = prompt(t('count.owner_reason_q')); if (!reason) return;
              extra = { owner_reason: reason };
            }
            const r = await SM.command('/api/counts/' + id + '/approve', key, undefined, extra); SM.toast(t('count.approved', { n: r.differences.length })); SM.go('count'); } catch (e) { SM.fail(e); }
        } }) : null;
        SM.put(root, h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('count.sheet', { id }) }), cancel, approve), h('p', { class: 'tip', text: t('count.uncounted_tip') }), filter),
          h('div', { class: 'stack' }, rows.map(r => r.el)));
      }
    },
  };
})();
