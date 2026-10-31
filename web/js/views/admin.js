/* Catalog, scanner wizard, settings, help. */
'use strict';

(function () {
  const { h, t, icon } = SM;

  /* ---------------- catalog ---------------- */
  SM.views.catalog = {
    icon: 'tag',
    async mount(root) {
      const q = h('input', { class: 'input', placeholder: t('cat.search') });
      const box = h('div');
      const load = async () => {
        const rows = await SM.api('GET', '/api/products?limit=300&q=' + encodeURIComponent(q.value)).catch(() => []);
        SM.put(box, rows.length ? SM.table([
          { label: t('cat.code'), render: r => h('span', { class: 'ltr', text: r.code }) },
          { label: t('cat.name'), render: r => SM.name(r) },
          { label: t('cat.kind'), render: r => t('kind.' + r.kind) },
          { label: t('cat.price'), num: true, render: r => SM.num(r.price === null ? '—' : SM.money(r.price) + (r.measure === 'g' ? ' / ' + t('unit.kg') : '')) },
          { label: t('sell.available'), num: true, render: r => SM.num(SM.qty(r.available, r.measure)) },
        ], rows, productDrawer) : SM.empty(t('cat.empty'), t('cat.empty_hint')));
      };
      q.addEventListener('input', () => { clearTimeout(this.timer); this.timer = setTimeout(load, 200); });
      SM.put(root, h('div', { class: 'toolbar' }, q, SM.can('catalog') ? h('button', { class: 'btn primary', 'data-guide': 'cat.add', onclick: () => productDialog(load) }, icon('plus'), t('cat.add')) : null), box,
        SM.can('prices') ? await promotionsCard() : null);
      load();
    },
  };

  async function productDrawer(p) {
    const full = await SM.api('GET', '/api/products/' + p.id);
    const acts = [];
    if (SM.can('prices')) acts.push(h('button', { class: 'btn', text: t('cat.change_price'), onclick: async () => {
      const v = prompt(t('cat.new_price_q') + (full.measure === 'g' ? ' (' + t('cat.per_kg') + ')' : ''));
      if (v === null) return;
      const price = SM.parseMoney(v); if (price === null) return SM.toast(t('err.bad_amount'), 'bad');
      try { await SM.command('/api/prices', SM.newKey(), undefined, { product_id: full.id, price }); SM.toast(t('saved')); d.close(); } catch (e) { SM.fail(e); }
    } }));
    if (SM.can('catalog')) acts.push(h('button', { class: 'btn', text: t('cat.add_barcode'), onclick: () => barcodeDialog(full, () => d.close()) }));
    if (full.kind === 'prepared' && SM.can('recipes')) acts.push(h('button', { class: 'btn', text: t('cat.recipe'), onclick: () => recipeDialog(full) }));
    if (SM.can('prices') && full.measure === 'unit' && !['ingredient', 'packaging'].includes(full.kind))
      acts.push(h('button', { class: 'btn', text: t('promo.add'), onclick: () => promoDialog(full, () => d.close()) }));
    const d = SM.drawer({ title: SM.name(full), actions: acts, body: [
      h('dl', { class: 'kv' }, h('dt', { text: t('cat.code') }), h('dd', { class: 'ltr', text: full.code }), h('dt', { text: t('cat.kind') }), h('dd', { text: t('kind.' + full.kind) }),
        h('dt', { text: t('cat.price') }), h('dd', {}, SM.num(full.price === null ? '—' : SM.moneyText(full.price))), h('dt', { text: t('lots.allergens') }), h('dd', { text: full.allergens || t('none') }),
        h('dt', { text: t('cat.tax') }), h('dd', {}, h('span', { class: 'badge warn', text: t('cat.tax_unverified') }))),
      h('h3', { text: t('cat.barcodes') }), full.barcodes.length ? h('ul', {}, full.barcodes.map(b => h('li', { class: 'ltr', text: b.code + (b.packaging_id ? ' (' + t('cat.packaging') + ')' : '') }))) : h('p', { class: 'muted', text: t('none') }),
      h('h3', { text: t('cat.packagings') }), full.packagings.length ? h('ul', {}, full.packagings.map(x => h('li', {}, SM.name(x) + ' = ' + x.factor_num + '/' + x.factor_den,
        x.retired_op ? h('span', { class: 'badge', text: t('cat.retired') }) :
          SM.can('catalog') ? h('button', { class: 'btn sm', text: t('cat.carton_change'), onclick: () => cartonDialog(x, () => d.close()) }) : null))) : h('p', { class: 'muted', text: t('none') }),
      h('button', { class: 'btn ghost', text: t('cat.lots'), onclick: () => { d.close(); SM.go('stock'); } })] });
  }

  /* Row 9: a new carton size is a new packaging; the old one is retired, never edited. */
  function cartonDialog(pk, after) {
    const n = h('input', { class: 'input', inputmode: 'numeric' }), reason = h('input', { class: 'input', maxlength: '200' });
    const move = h('input', { type: 'checkbox', checked: true });
    const key = SM.newKey();
    const d = SM.dialog({ title: t('cat.carton_change'), body: [h('p', { class: 'tip', text: t('cat.carton_change_help', { old: pk.factor_num }) }),
      SM.field(t('cat.carton_units'), n), SM.field(t('cat.carton_reason'), reason), h('label', { class: 'row' }, move, t('cat.carton_move'))],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        const v = SM.parseInt(n.value); if (!v) return SM.toast(t('err.bad_quantity'), 'bad');
        try { await SM.command('/api/packagings/replace', key, undefined, { packaging_id: pk.id, factor_num: v, factor_den: 1, reason: reason.value, move_barcodes: move.checked });
          d.close(); SM.toast(t('saved')); after(); } catch (e) { SM.fail(e); }
      }) })] });
  }

  /* Row 26: buy X get Y of the same product, applied by the server on every sale. */
  function promoDialog(product, after) {
    const buy = h('input', { class: 'input', inputmode: 'numeric', value: '2' }), free = h('input', { class: 'input', inputmode: 'numeric', value: '1' });
    const starts = h('input', { class: 'input', type: 'date', value: SM.today() }), ends = h('input', { class: 'input', type: 'date' });
    const key = SM.newKey();
    const d = SM.dialog({ title: t('promo.add'), body: [h('p', { class: 'tip', text: t('promo.help') }),
      h('div', { class: 'grid cols-2' }, SM.field(t('promo.buy'), buy), SM.field(t('promo.free'), free), SM.field(t('promo.starts'), starts), SM.field(t('promo.ends'), ends, t('promo.ends_help')))],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        try { await SM.command('/api/promotions', key, undefined, { product_id: product.id, buy_qty: SM.parseInt(buy.value), free_qty: SM.parseInt(free.value),
          starts: starts.value, ends: ends.value || null }); d.close(); SM.toast(t('saved')); after(); } catch (e) { SM.fail(e); }
      }) })] });
  }

  async function promotionsCard() {
    const rows = await SM.api('GET', '/api/promotions').catch(() => []);
    return h('div', { class: 'card' }, h('h3', { text: t('promo.list') }), h('p', { class: 'muted', text: t('promo.list_help') }),
      rows.length ? SM.table([{ label: t('stock.product'), render: r => SM.name(r) },
        { label: t('promo.offer'), render: r => t('promo.offer_text', { buy: r.buy_qty, free: r.free_qty }) },
        { label: t('promo.starts'), render: r => h('span', { class: 'ltr', text: r.starts }) },
        { label: t('promo.ends'), render: r => h('span', { class: 'ltr', text: r.ends || '—' }) },
        { label: '', render: r => SM.can('prices') ? h('button', { class: 'btn sm danger', text: t('promo.end'), onclick: SM.guard(async () => {
          try { await SM.command('/api/promotions/end', SM.newKey(), undefined, { promotion_id: r.id }); SM.toast(t('saved')); SM.go('catalog'); } catch (e) { SM.fail(e); }
        }) }) : null }], rows) : h('p', { class: 'muted', text: t('none') }));
  }

  function barcodeDialog(product, after) {
    const code = SM.scanner.field({ placeholder: t('cat.scan_barcode'), onEnter: (raw) => { code.value = raw.trim(); } });
    const pk = SM.select(product.packagings.map(x => ({ value: x.id, label: SM.name(x) })), null, t('cat.single_unit'));
    const d = SM.dialog({ title: t('cat.add_barcode'), body: [SM.field(t('cat.barcode'), code, t('cat.barcode_help')), SM.field(t('cat.packaging'), pk)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
        try { await SM.api('POST', '/api/barcodes', { code: code.value.trim(), product_id: product.id, packaging_id: pk.value ? parseInt(pk.value, 10) : null }); SM.toast(t('saved')); d.close(); after && after(); } catch (e) { SM.fail(e); }
      } })] });
  }

  function productDialog(after) {
    const f = { code: h('input', { class: 'input', 'data-guide': 'cat.code' }), name_ar: h('input', { class: 'input', 'data-guide': 'cat.name_ar' }), name_en: h('input', { class: 'input', dir: 'ltr' }),
      kind: SM.select(['grocery', 'raw_meat', 'prepared', 'ingredient', 'packaging'].map(k => ({ value: k, label: t('kind.' + k) }))),
      price: h('input', { class: 'input', inputmode: 'decimal', 'data-guide': 'cat.price' }), barcode: h('input', { class: 'input', dir: 'ltr', 'data-guide': 'cat.barcode' }),
      needs_expiry: h('input', { type: 'checkbox', checked: true }), allergens: h('input', { class: 'input' }),
      meat_share: h('input', { class: 'input', inputmode: 'numeric', placeholder: '700' }),
      carton: h('input', { class: 'input', inputmode: 'numeric', placeholder: '12' }), carton_barcode: h('input', { class: 'input', dir: 'ltr' }) };
    const d = SM.dialog({ title: t('cat.add'), wide: true, body: [h('div', { class: 'grid cols-2' },
      SM.field(t('cat.code'), f.code), SM.field(t('cat.kind'), f.kind, t('cat.kind_help')), SM.field(t('cat.name_ar'), f.name_ar), SM.field(t('cat.name_en'), f.name_en),
      SM.field(t('cat.price'), f.price, t('cat.price_help')), SM.field(t('cat.barcode'), f.barcode, t('cat.barcode_help')),
      SM.field(t('cat.carton_units'), f.carton), SM.field(t('cat.carton_barcode'), f.carton_barcode),
      SM.field(t('lots.allergens'), f.allergens, t('cat.allergens_help')), SM.field(t('cat.meat_share'), f.meat_share, t('cat.meat_share_help')),
      h('label', { class: 'row' }, f.needs_expiry, t('cat.needs_expiry')))],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
        const weighed = ['raw_meat', 'prepared', 'ingredient'].includes(f.kind.value);
        const body = { code: f.code.value, name_ar: f.name_ar.value, name_en: f.name_en.value || f.name_ar.value, kind: f.kind.value,
          measure: weighed ? 'g' : 'unit', needs_expiry: f.needs_expiry.checked, allergens: f.allergens.value,
          price: f.price.value ? SM.parseMoney(f.price.value) : undefined, barcodes: f.barcode.value ? [f.barcode.value.trim()] : [] };
        if (f.meat_share.value) body.meat_permille = SM.parseInt(f.meat_share.value);
        if (f.carton.value) body.packagings = [{ name_ar: 'كرتونة', name_en: 'Carton', factor_num: SM.parseInt(f.carton.value), barcodes: f.carton_barcode.value ? [f.carton_barcode.value.trim()] : [] }];
        try { await SM.api('POST', '/api/products', body); SM.toast(t('saved')); d.close(); after(); } catch (e) { SM.fail(e); }
      } })] });
  }

  async function recipeDialog(product) {
    const products = await SM.api('GET', '/api/products?limit=500');
    const versions = await SM.api('GET', '/api/recipes/' + product.id);
    const rows = [];
    const box = h('div', { class: 'stack' });
    const add = (c) => {
      const p = SM.select(products.filter(x => x.id !== product.id && x.kind !== 'grocery').map(x => ({ value: x.id, label: SM.name(x) })), c && c.product_id, t('choose'));
      const qty = h('input', { class: 'input', inputmode: 'decimal', value: c ? (c.measure === 'g' ? SM.qty(c.qty, 'g').split(' ')[0] : String(c.qty)) : '' });
      const role = SM.select(['meat', 'food', 'packaging'].map(r => ({ value: r, label: t('role.' + r) })), c && c.role);
      rows.push({ p, qty, role }); box.append(h('div', { class: 'grid cols-3' }, SM.field(t('prep.input_lot'), p), SM.field(t('recipe.qty'), qty, t('recipe.qty_help')), SM.field(t('recipe.role'), role)));
    };
    const last = versions[0];
    (last ? last.components : [null]).forEach(add);
    const days = h('input', { class: 'input', inputmode: 'numeric', value: last ? String(last.shelf_life_days) : '' });
    const tol = h('input', { class: 'input', inputmode: 'numeric', value: last ? String(last.loss_tolerance_permille) : '' });
    const allergens = h('input', { class: 'input', value: last ? last.allergens : '' });
    const target = h('input', { class: 'input', inputmode: 'decimal', value: last && last.target_pack_g ? SM.qty(last.target_pack_g, 'g').split(' ')[0] : '' });
    const d = SM.dialog({ title: t('recipe.title', { name: SM.name(product), v: (last ? last.version : 0) + 1 }), wide: true, body: [
      h('p', { class: 'tip', text: t('recipe.tip') }), box, h('button', { class: 'btn sm', onclick: () => add() }, icon('plus', 'sm'), t('add')),
      h('div', { class: 'grid cols-2' }, SM.field(t('recipe.days'), days, t('recipe.days_help')), SM.field(t('recipe.tolerance'), tol, t('recipe.tolerance_help')),
        SM.field(t('lots.allergens'), allergens), SM.field(t('prep.pack_kg'), target))],
      actions: [h('button', { class: 'btn primary', text: t('recipe.save'), onclick: async () => {
        const comps = rows.filter(r => r.p.value).map(r => { const p = products.find(x => String(x.id) === r.p.value);
          return { product_id: p.id, qty: p.measure === 'g' ? SM.parseKg(r.qty.value) : SM.parseInt(r.qty.value), role: r.role.value }; });
        try {
          await SM.api('POST', '/api/recipes', { product_id: product.id, components: comps, shelf_life_days: SM.parseInt(days.value), allergens: allergens.value,
            loss_tolerance_permille: SM.parseInt(tol.value || '0'), target_pack_g: target.value ? SM.parseKg(target.value) : null });
          SM.toast(t('saved')); d.close();
        } catch (e) { SM.fail(e); }
      } })] });
  }

  /* ---------------- scanner wizard ---------------- */
  SM.views.scanner = {
    icon: 'scan', perm: 'scanner_setup',
    async mount(root) {
      const profile = Object.assign({ connection: 'keyboard', suffix: 'CR', prefix: '', layout_fix: '', aim_ids: false, beep: false }, SM.scanner.profile);
      let conn = profile.connection || 'keyboard';
      const body = h('div');
      const seg = h('div', { class: 'seg seg-scroll' }, ['keyboard', 'serial', 'tcp'].map(c => h('button', { 'aria-pressed': c === conn ? 'true' : 'false', text: t('scn.c.' + c), onclick: (e) => {
        conn = c; seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', 'false')); e.target.setAttribute('aria-pressed', 'true'); draw(); } })));
      const saveProfile = async (extra) => {
        const cfg = Object.assign({}, profile, extra, { connection: conn });
        try { SM.scanner.profile = await SM.api('POST', '/api/scanner/profile', { config: cfg }); Object.assign(profile, SM.scanner.profile); SM.toast(t('scn.saved')); SM.scanner.stop(); SM.scanner.startEvents(); } catch (e) { SM.fail(e); }
      };
      const resultBox = h('div', { class: 'scan-result' });
      const showInspect = async (raw, keys) => {
        const r = await SM.api('POST', '/api/scanner/inspect', { raw, profile }).catch(e => { SM.fail(e); return null; });
        if (!r) return;
        const rows = [[t('scn.seen'), h('code', { class: 'ltr', text: r.seen })], [t('scn.length'), String(r.length)]];
        if (keys) rows.push([t('scn.ending'), h('code', { class: 'ltr', text: keys })]);
        if (r.arabic_letters) rows.push([t('scn.layout'), h('span', {}, h('span', { class: 'badge warn', text: t('scn.arabic_detected') }), ' ',
          h('button', { class: 'btn sm', text: t('scn.use_fix'), onclick: () => saveProfile({ layout_fix: 'arabic101' }) }))]);
        if (r.result) rows.push([t('scn.meaning'), r.result.ok ? h('span', { class: 'badge ok' }, t('scan.k.' + r.result.scan.kind), r.result.product ? ' — ' + SM.name(r.result.product) : '')
          : h('span', { class: 'badge bad', text: t('err.' + r.result.error) })]);
        if (r.result && r.result.scan) rows.push([t('scn.kind'), t('scan.k.' + r.result.scan.kind)]);
        SM.put(resultBox, h('dl', { class: 'kv' }, rows.map(([k, v]) => [h('dt', { text: k }), h('dd', {}, v)])));
      };
      function keyboard() {
        const test = h('textarea', { class: 'input mono', rows: '2', autocomplete: 'off', spellcheck: 'false', placeholder: t('scn.test_ph') });
        let keys = [];
        test.addEventListener('keydown', (e) => {
          keys.push(e.key.length === 1 ? e.key : '<' + e.key + '>');
          if (e.key === 'Enter' || e.key === 'Tab') {
            e.preventDefault();
            const raw = test.value; test.value = '';
            showInspect(raw, keys.slice(-1).join(''));
            keys = [];
          }
        });
        const prefix = h('input', { class: 'input mono', maxlength: '1', value: profile.prefix || '' });
        const suffix = SM.select([{ value: 'CR', label: 'Enter' }, { value: 'TAB', label: 'Tab' }], profile.suffix);
        const fix = h('input', { type: 'checkbox', checked: profile.layout_fix === 'arabic101' });
        const aim = h('input', { type: 'checkbox', checked: !!profile.aim_ids });
        const beep = h('input', { type: 'checkbox', checked: !!profile.beep });
        return [h('ol', { class: 'steps' }, h('li', { text: t('scn.kb1') }), h('li', { text: t('scn.kb2') }), h('li', { text: t('scn.kb3') })),
          SM.field(t('scn.test'), test), resultBox,
          h('div', { class: 'grid cols-2' }, SM.field(t('scn.prefix'), prefix, t('scn.prefix_help')), SM.field(t('scn.suffix'), suffix),
            h('label', { class: 'row' }, fix, t('scn.layout_fix')), h('label', { class: 'row' }, aim, t('scn.aim')), h('label', { class: 'row' }, beep, t('scn.beep'))),
          h('button', { class: 'btn primary', text: t('scn.save'), onclick: () => saveProfile({ prefix: prefix.value, suffix: suffix.value, layout_fix: fix.checked ? 'arabic101' : '', aim_ids: aim.checked, beep: beep.checked }) })];
      }
      async function device(type) {
        const ports = await SM.api('GET', '/api/scanner/ports').catch(() => ({ serial: [], devices: [] }));
        const settings = SM.can('settings') ? await SM.api('GET', '/api/settings').catch(() => ({})) : {};
        const existing = (settings.scanner_devices || []).find(d => d.id === profile.device_id && d.type === type) || {};
        const id = h('input', { class: 'input', value: existing.id || (type + '1') });
        const port = type === 'serial' ? h('input', { class: 'input ltr', list: 'ports', value: existing.port || ports.serial[0] || '' }) : h('input', { class: 'input', inputmode: 'numeric', value: existing.port || '' });
        const host = h('input', { class: 'input ltr', value: existing.host || '' });
        const baud = SM.select([1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200].map(b => ({ value: b, label: String(b) })), existing.baud || 9600);
        const parity = SM.select(['none', 'even', 'odd'].map(p => ({ value: p, label: t('scn.parity.' + p) })), existing.parity || 'none');
        const bits = SM.select([{ value: 8, label: '8' }, { value: 7, label: '7' }], existing.bits || 8);
        const stop = SM.select([{ value: 1, label: '1' }, { value: 2, label: '2' }], existing.stop || 1);
        const suffix = SM.select(['CR', 'LF', 'CRLF', 'TAB', 'ETX', 'EOT'].map(s => ({ value: s, label: s })), existing.suffix || 'CR');
        const till = h('input', { class: 'input ltr', value: existing.workstation || SM.workstation() });
        const live = h('div', { class: 'scan-result' });
        const statusBox = h('div', {}, ports.devices.map(d => h('div', {}, h('b', { class: 'ltr', text: d.id }), ' ', SM.badge(d.status === 'connected' ? 'available' : 'held'), ' ', t('scn.st.' + d.status),
          d.workstation ? h('span', { class: 'muted' }, ' — ', t('scn.bound_to'), ' ', h('code', { class: 'ltr', text: d.workstation })) : null, d.error ? h('div', { class: 'muted ltr', text: d.error }) : null)));
        const save = h('button', { class: 'btn primary', text: t('scn.save_device'), disabled: !SM.can('settings'), onclick: async () => {
          const dev = { id: id.value, type, suffix: suffix.value, enabled: true, workstation: till.value.trim() };
          if (type === 'serial') Object.assign(dev, { port: port.value, baud: parseInt(baud.value, 10), parity: parity.value, bits: parseInt(bits.value, 10), stop: parseInt(stop.value, 10) });
          else Object.assign(dev, { host: host.value, port: parseInt(port.value, 10) });
          const others = (settings.scanner_devices || []).filter(d => d.id !== dev.id);
          try { await SM.api('POST', '/api/scanner/devices', { devices: [...others, dev] }); await saveProfile({ device_id: dev.id }); SM.go('scanner'); } catch (e) { SM.fail(e); }
        } });
        let stopLive = false;
        (async () => {   // diagnostics: read-only view (admin), never consumes the till's scans
          if (!SM.can('settings')) return;
          let after = (await SM.api('GET', '/api/scanner/events?diag=1&wait=0').catch(() => ({ last: 0 }))).last;
          while (!stopLive && SM.state.view === 'scanner') {
            const r = await SM.api('GET', '/api/scanner/events?diag=1&wait=10&after=' + after).catch(() => null);
            if (!r) break;
            after = r.last;
            for (const e of r.events) { live.prepend(h('div', {}, h('b', { class: 'ltr', text: e.device }), ' ', h('code', { class: 'ltr', text: e.seen }))); showInspect(e.text); }
          }
        })();
        SM.views.scanner.unmount = () => { stopLive = true; };
        return [h('ol', { class: 'steps' }, h('li', { text: t('scn.' + type + '1') }), h('li', { text: t('scn.' + type + '2') }), h('li', { text: t('scn.' + type + '3') })),
          h('datalist', { id: 'ports' }, ports.serial.map(p => h('option', { value: p }))),
          h('div', { class: 'grid cols-3' }, SM.field(t('scn.device_id'), id), type === 'tcp' ? SM.field(t('scn.host'), host, t('scn.host_help')) : null,
            SM.field(type === 'serial' ? t('scn.port') : t('scn.tcp_port'), port, type === 'serial' ? t('scn.port_help', { list: ports.serial.join(', ') || t('none') }) : null),
            type === 'serial' ? [SM.field(t('scn.baud'), baud), SM.field(t('scn.parity'), parity), SM.field(t('scn.bits'), bits), SM.field(t('scn.stop'), stop)] : null,
            SM.field(t('scn.suffix'), suffix), SM.field(t('scn.till'), till, t('scn.till_help'))),
          SM.can('settings') ? save : h('p', { class: 'muted', text: t('scn.need_manager') }),
          h('h3', { text: t('scn.status') }), statusBox, h('h3', { text: t('scn.live') }), live, resultBox];
      }
      async function draw() {
        if (SM.views.scanner.unmount) SM.views.scanner.unmount();
        SM.put(body, h('div', { class: 'card' }, ...(conn === 'keyboard' ? keyboard() : await device(conn))));
      }
      const sheet = h('div', { class: 'card' }, h('h3', { text: t('scn.sheet') }), h('p', { class: 'muted', text: t('scn.sheet_help') }),
        h('div', { class: 'labels' }, ['6220000000013', '0012345678905', '2112345012506'].map(c => h('div', { class: 'pack-label' }, SM.ean13(c), h('div', { class: 'ltr mono', text: c })))));
      /* H3: a device test the owner can run on the real shop PC; the answer is kept on this PC, never claimed for the hardware */
      const deviceTest = () => {
        const last = SM.prefs.get('device_test', {});
        const lastBox = h('p', { class: 'muted' });
        const showLast = () => SM.put(lastBox, ...(last.print ? [t('dev.last_print', { mm: last.print.mm, result: t(last.print.ok ? 'dev.ok' : 'dev.bad'), at: last.print.at })] : [t('dev.none_yet')]));
        const remember = (mm, ok) => { last.print = { mm, ok, at: new Date().toLocaleString('en-GB', { timeZone: 'Africa/Cairo' }) }; SM.prefs.set('device_test', last); showLast(); };
        const test = {
          no: 'TEST-0001', at: new Date().toISOString(), cashier: t('dev.cashier'), shop_name: SM.state.settings.shop_name || t('app.name'),
          lines: [{ name_ar: 'لبن كامل الدسم ١ لتر — جرّب الحروف: أ إ آ ؤ ئ ة ى', name_en: 'Full-cream milk 1 L', qty: 2, measure: 'piece', unit_price: 4550, gross: 9100, discount: 0 },
            { name_ar: 'جبنة رومي — ٢٥٠ جم', name_en: 'Roumy cheese 250 g', qty: 1, measure: 'piece', unit_price: 12075, gross: 12075, discount: 0 }],
          discount: 0, total: 21175, payments: [{ method: 'cash', amount: 21175, state: 'settled' }], cash_tendered: 25000, change: 3825, footer: t('dev.footer') };
        const run = async (mm) => {
          let area = document.getElementById('print');
          if (!area) { area = h('div', { id: 'print' }); document.body.append(area); }
          area.className = 'w' + mm;
          SM.put(area, SM.receipt(test));
          window.print();
          const ok = await SM.confirm(t('dev.print_q_title'), t('dev.print_q', { mm }));
          remember(mm, ok);
        };
        showLast();
        return h('div', { class: 'card', 'data-guide': 'dev.card' }, h('h3', { text: t('dev.title') }), h('p', { class: 'muted', text: t('dev.help') }),
          h('div', { class: 'toolbar' }, [58, 80].map(mm => h('button', { class: 'btn', text: t('dev.print', { mm }), onclick: SM.guard(() => run(mm)) }))), lastBox);
      };
      SM.put(root, h('p', { class: 'tip', text: t('scn.intro') }), h('div', { class: 'toolbar' }, seg), body, sheet, deviceTest());
      draw();
    },
  };

  /* ---------------- settings ---------------- */
  SM.views.settings = {
    icon: 'gear',
    async mount(root) {
      const parts = [];
      // appearance: everyone
      const theme = SM.select(['daylight', 'night', 'contrast'].map(x => ({ value: x, label: t('set.theme.' + x) })), document.documentElement.dataset.theme);
      const lang = SM.select([{ value: 'ar', label: 'العربية' }, { value: 'en', label: 'English' }], SM.state.lang);
      const size = SM.select([90, 100, 115, 130].map(x => ({ value: x, label: x + '%' })), SM.prefs.get('size', 100));
      const apply = () => { SM.prefs.set('theme', theme.value); SM.prefs.set('lang', lang.value); SM.prefs.set('size', parseInt(size.value, 10)); SM.applyPrefs(); SM.renderShell(); };
      [theme, lang, size].forEach(s => s.onchange = apply);
      parts.push(h('div', { class: 'card' }, h('h3', { text: t('set.appearance') }), h('div', { class: 'grid cols-3' }, SM.field(t('set.theme'), theme), SM.field(t('set.lang'), lang), SM.field(t('set.size'), size)),
        h('p', { class: 'muted' }, t('set.workstation'), ' ', h('code', { class: 'ltr', text: SM.workstation() }))));
      if (SM.can('settings')) {
        const s = await SM.api('GET', '/api/settings');
        const name = h('input', { class: 'input', value: s.shop_name || '' });
        const footer = h('input', { class: 'input', value: s.receipt_footer || '' });
        const width = SM.select([{ value: 80, label: '80 mm' }, { value: 58, label: '58 mm' }], s.receipt_width_mm);
        const near = h('input', { class: 'input', inputmode: 'numeric', value: String(s.near_expiry_days) });
        const disc = h('input', { class: 'input', inputmode: 'numeric', value: String(s.cashier_discount_permille) });
        const tol = h('input', { class: 'input', inputmode: 'numeric', value: s.cutting_loss_tolerance_permille === null ? '' : String(s.cutting_loss_tolerance_permille) });
        const dayStart = h('input', { class: 'input', inputmode: 'numeric', value: String(s.day_start_hour === undefined ? 4 : s.day_start_hour) });
        const thawDays = h('input', { class: 'input', inputmode: 'numeric', value: s.thaw_shelf_life_days ? String(s.thaw_shelf_life_days) : '' });
        const refundOver = h('input', { class: 'input', inputmode: 'decimal', value: SM.money(s.refund_approval_over === undefined ? 20000 : s.refund_approval_over) });
        const refundDays = h('input', { class: 'input', inputmode: 'numeric', value: String(s.refund_days_without_approval === undefined ? 7 : s.refund_days_without_approval) });
        const below = h('input', { type: 'checkbox', checked: s.block_below_cost });
        const gtin = h('input', { type: 'checkbox', checked: s.gtin_equivalence });
        parts.push(h('div', { class: 'card' }, h('h3', { text: t('set.shop') }), h('div', { class: 'grid cols-3' },
          SM.field(t('set.shop_name'), name), SM.field(t('set.footer'), footer), SM.field(t('set.width'), width), SM.field(t('set.near'), near),
          SM.field(t('set.discount'), disc, t('set.discount_help')), SM.field(t('set.tolerance'), tol, t('set.tolerance_help')),
          SM.field(t('set.thaw_days'), thawDays, t('set.thaw_days_help')), SM.field(t('set.day_start'), dayStart, t('set.day_start_help')),
          SM.field(t('set.refund_over'), refundOver, t('set.refund_help')), SM.field(t('set.refund_days'), refundDays),
          h('label', { class: 'row' }, below, t('set.below_cost')), h('label', { class: 'row' }, gtin, t('set.gtin'))),
          h('p', { class: 'tip', text: t('set.tax_note') }),
          h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
            try {
              await SM.api('POST', '/api/settings', { shop_name: name.value, receipt_footer: footer.value, receipt_width_mm: parseInt(width.value, 10),
                near_expiry_days: SM.parseInt(near.value), cashier_discount_permille: SM.parseInt(disc.value), cutting_loss_tolerance_permille: tol.value === '' ? null : SM.parseInt(tol.value),
                block_below_cost: below.checked, gtin_equivalence: gtin.checked, thaw_shelf_life_days: thawDays.value === '' ? null : SM.parseInt(thawDays.value), day_start_hour: SM.parseInt(dayStart.value || '0'),
                refund_approval_over: SM.parseMoney(refundOver.value), refund_days_without_approval: SM.parseInt(refundDays.value) });
              SM.state.settings = await SM.api('GET', '/api/settings'); SM.toast(t('saved'));
            } catch (e) { SM.fail(e); }
          } })));
        parts.push(await labelCard());
      }
      if (SM.can('users')) parts.push(await usersCard());
      if (SM.state.lanAddresses) parts.push(lanCard());
      if (SM.can('backup')) parts.push(await backupCard(), await cloudCard());
      SM.put(root, ...parts);
    },
  };

  /* Review M1/M2: how another till reaches this PC, and the honest warning about plain HTTP. */
  function lanCard() {
    return h('div', { class: 'card', id: 'lan-card' }, h('h3', { text: t('set.lan') }), h('p', { class: 'muted', text: t('set.lan_help') }),
      h('ul', {}, SM.state.lanAddresses.map(a => h('li', { class: 'ltr', text: a }))), h('p', { class: 'warn', text: t('set.lan_warn') }));
  }

  async function labelCard() {
    const list = await SM.api('GET', '/api/label-templates').catch(() => []);
    const f = { name: h('input', { class: 'input' }), prefix: h('input', { class: 'input', inputmode: 'numeric', value: '21' }), item_start: h('input', { class: 'input', value: '2' }),
      item_len: h('input', { class: 'input', value: '5' }), value_start: h('input', { class: 'input', value: '7' }), value_len: h('input', { class: 'input', value: '5' }),
      kind: SM.select([{ value: 'weight', label: t('lbl.weight') }, { value: 'price', label: t('lbl.price') }]), decimals: h('input', { class: 'input', value: '3' }) };
    return h('div', { class: 'card' }, h('h3', { text: t('lbl.title') }), h('p', { class: 'tip', text: t('lbl.tip') }),
      list.length ? h('ul', {}, list.map(x => h('li', { class: 'ltr', text: x.name + ': ' + x.prefix + ' item[' + x.item_start + ',' + x.item_len + '] ' + x.value_kind + '[' + x.value_start + ',' + x.value_len + '] /10^' + x.decimals }))) : null,
      h('div', { class: 'grid cols-4' }, SM.field(t('lbl.name'), f.name), SM.field(t('lbl.prefix'), f.prefix), SM.field(t('lbl.item_start'), f.item_start), SM.field(t('lbl.item_len'), f.item_len),
        SM.field(t('lbl.value_start'), f.value_start), SM.field(t('lbl.value_len'), f.value_len), SM.field(t('lbl.kind'), f.kind), SM.field(t('lbl.decimals'), f.decimals)),
      h('button', { class: 'btn', text: t('save'), onclick: async () => {
        const n = (el) => SM.parseInt(el.value);
        try { await SM.api('POST', '/api/label-templates', { name: f.name.value || 'scale', prefix: f.prefix.value, length: 13, item_start: n(f.item_start), item_len: n(f.item_len),
          value_start: n(f.value_start), value_len: n(f.value_len), value_kind: f.kind.value, decimals: n(f.decimals) }); SM.toast(t('saved')); SM.go('settings'); } catch (e) { SM.fail(e); }
      } }));
  }

  async function usersCard() {
    const users = await SM.api('GET', '/api/users').catch(() => []);
    const f = { username: h('input', { class: 'input', autocomplete: 'off', 'data-guide': 'set.username' }), name: h('input', { class: 'input' }), password: h('input', { class: 'input', type: 'password', autocomplete: 'new-password', 'data-guide': 'set.password' }),
      role: SM.select(['manager', 'cashier', 'receiver', 'butcher', 'counter', 'accountant', 'viewer', 'owner'].map(r => ({ value: r, label: t('role.' + r) }))) };
    f.role.setAttribute('data-guide', 'set.role');
    f.password.type = 'text'; f.password.classList.add('ltr'); f.password.value = SM.tempPassword();     // one less thing to think of: a ready temporary password, shown so it can be read out
    return h('div', { class: 'card', 'data-guide': 'set.users' }, h('h3', { text: t('set.users') }), h('p', { class: 'muted', 'data-guide': 'set.roles_help', text: t('set.roles_help') }),
      SM.table([{ label: t('username'), render: u => h('span', { class: 'ltr', text: u.username }) }, { label: t('set.display'), key: 'display_name' }, { label: t('set.role'), render: u => t('role.' + u.role) },
        { label: t('set.state'), render: u => h('span', { class: 'badge ' + (u.active ? (u.must_change ? 'warn' : 'ok') : 'bad'),
          text: u.active ? (u.must_change ? t('set.user_must_change') : t('set.user_active')) : t('set.user_stopped') }) },
        { label: '', render: u => SM.state.user && u.id === SM.state.user.id ? null : h('div', { class: 'row' },
          h('button', { class: 'btn sm', 'data-guide': 'set.reset_password', text: t('set.reset_password'), onclick: SM.guard(async () => {
            const temp = h('input', { class: 'input ltr', value: SM.tempPassword(), 'data-guide': 'set.temp_pw', autocomplete: 'off' });
            const d = SM.dialog({ title: t('set.reset_title', { name: u.display_name }), body: [h('p', { class: 'tip', text: t('set.reset_password_q', { name: u.display_name }) }), SM.field(t('set.temp_pw'), temp, t('set.temp_pw_help'))],
              actions: [h('button', { class: 'btn ghost', text: t('cancel'), onclick: () => d.close() }),
                h('button', { class: 'btn primary', id: 'reset-save', text: t('set.reset_save'), onclick: async () => {
                  try { await SM.api('POST', '/api/users/' + u.id + '/password', { password: temp.value }); d.close(); SM.toast(t('set.reset_done')); SM.go('settings'); } catch (e) { SM.fail(e); }
                } })] });
          }) }),
          h('button', { class: 'btn sm ' + (u.active ? 'danger' : ''), text: u.active ? t('set.stop_user') : t('set.start_user'), onclick: SM.guard(async () => {
            const reason = prompt(u.active ? t('set.stop_user_q', { name: u.display_name }) : t('set.start_user_q')); if (!reason) return;
            try { await SM.api('POST', '/api/users/' + u.id + '/active', { active: !u.active, reason }); SM.toast(t('saved')); SM.go('settings'); } catch (e) { SM.fail(e); }
          }) })) }], users),
      h('div', { class: 'grid cols-4' }, SM.field(t('username'), f.username), SM.field(t('set.display'), f.name), SM.field(t('password'), f.password, t('set.pw_help')), SM.field(t('set.role'), f.role)),
      h('button', { class: 'btn', 'data-guide': 'set.add_user', text: t('set.add_user'), onclick: async () => {
        try { await SM.api('POST', '/api/users', { username: f.username.value, name: f.name.value || f.username.value, password: f.password.value, role: f.role.value }); SM.toast(t('saved')); SM.go('settings'); } catch (e) { SM.fail(e); }
      } }));
  }

  async function backupCard() {
    const list = await SM.api('GET', '/api/backups').catch(() => []);
    const st = await SM.api('GET', '/api/backup/status').catch(() => ({}));
    const second = h('input', { class: 'input ltr', id: 'backup2', value: (SM.state.settings || {}).backup_folder_2 || '', placeholder: 'E:\\' });
    return h('div', { class: 'card' }, h('h3', { text: t('set.backup') }), h('p', { class: 'muted', text: t('set.backup_help') }),
      h('button', { class: 'btn primary', 'data-guide': 'set.backup_now', text: t('set.backup_now'), onclick: async () => {
        try { const r = await SM.api('POST', '/api/backup', {}); SM.toast(t('set.backup_done')); SM.dialog({ title: t('set.backup_done'), body: h('code', { class: 'ltr', text: r.folder + '\nSHA-256 ' + r.sha256 }) }); } catch (e) { SM.fail(e); }
      } }),
      list.length ? h('ul', {}, list.slice(0, 10).map(b => h('li', { class: 'ltr', text: b.created_at + ' — schema ' + b.schema + ' — ' + b.integrity }))) : null,
      h('h4', { text: t('set.backup2') }), h('p', { class: 'muted', text: t('set.backup2_help') }),
      h('p', { class: st.external_error ? 'warn' : 'muted', text: st.external_error ? t('bk.error') : st.external_ok_at ? t('set.backup2_state_ok', { at: '\u2066' + st.external_ok_at.replace('T', ' ').slice(0, 16) + '\u2069' }) : t('set.backup2_state_none') }),
      h('div', { class: 'row' }, second, h('button', { class: 'btn', id: 'backup2-save', text: t('set.backup2_save'), onclick: async () => {
        try { SM.state.settings = await SM.api('POST', '/api/settings', { backup_folder_2: second.value.trim() }); SM.toast(t('set.backup2_saved')); SM.go('settings'); } catch (e) { SM.fail(e); }
      } })));
  }

  // Live copy of the shop in the cloud (server/cloudsync.py). The program connects itself; the owner only sees the state
  // and, if he wants, his recovery code for a replacement PC. Never shows the secret or the address.
  async function cloudCard() {
    const st = await SM.api('GET', '/api/cloud').catch(() => ({ unknown: true }));
    const known = ['offline', 'certificate', 'clock', 'busy', 'not_ready', 'pc_full', 'copy_failed', 'rejected_device'];
    const problem = st.error ? t('set.cloud_problem', { why: t('cloud.' + (known.includes(st.error) ? st.error : 'other')) }) : null;
    const lines = st.unknown ? [t('set.cloud_unknown')] : !st.enabled ? (st.enrolling ? [t('set.cloud_enrolling'), problem] : [t('set.cloud_off')]) : [
      st.last_ok_at ? t('set.cloud_ok', { at: '\u2066' + st.last_ok_at.replace('T', ' ').slice(0, 16) + '\u2069' }) : t('set.cloud_never'),
      st.last_copy ? t('set.cloud_copy', { name: '\u2066' + st.last_copy + '\u2069' }) : null,
      st.queued ? t('set.cloud_wait', { n: st.queued }) : null,
      problem].filter(Boolean);
    const shown = h('p', { class: 'recovery-code', id: 'cloud-recovery-code', dir: 'ltr' });
    return h('div', { class: 'card', id: 'cloud-card' }, h('h3', { text: t('set.cloud') }), h('p', { class: 'muted', text: t('set.cloud_help') }),
      lines.filter(Boolean).map(x => h('p', { class: st.error ? 'warn' : '', text: x })),
      st.enabled ? h('button', { class: 'btn', id: 'cloud-now', text: t('set.cloud_now'), onclick: async () => {
        try { await SM.api('POST', '/api/cloud/now', {}); SM.toast(t('set.cloud_started')); setTimeout(() => SM.go('settings'), 4000); } catch (e) { SM.fail(e); }
      } }) : null,
      st.recovery_available && SM.can('settings') ? [h('p', { class: 'muted', text: t('set.cloud_recovery_help') }),
        h('button', { class: 'btn', id: 'cloud-recovery', text: t('set.cloud_recovery_show'), onclick: async () => {
          try { const r = await SM.api('GET', '/api/cloud/recovery'); shown.textContent = '\u2066' + r.code + '\u2069'; } catch (e) { SM.fail(e); }
        } }), shown] : null);
  }

})();
