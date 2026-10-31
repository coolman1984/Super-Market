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
      SM.put(root, h('div', { class: 'toolbar' }, q, SM.can('catalog') ? h('button', { class: 'btn primary', onclick: () => productDialog(load) }, icon('plus'), t('cat.add')) : null), box);
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
    const d = SM.drawer({ title: SM.name(full), actions: acts, body: [
      h('dl', { class: 'kv' }, h('dt', { text: t('cat.code') }), h('dd', { class: 'ltr', text: full.code }), h('dt', { text: t('cat.kind') }), h('dd', { text: t('kind.' + full.kind) }),
        h('dt', { text: t('cat.price') }), h('dd', {}, SM.num(full.price === null ? '—' : SM.moneyText(full.price))), h('dt', { text: t('lots.allergens') }), h('dd', { text: full.allergens || t('none') }),
        h('dt', { text: t('cat.tax') }), h('dd', {}, h('span', { class: 'badge warn', text: t('cat.tax_unverified') }))),
      h('h3', { text: t('cat.barcodes') }), full.barcodes.length ? h('ul', {}, full.barcodes.map(b => h('li', { class: 'ltr', text: b.code + (b.packaging_id ? ' (' + t('cat.packaging') + ')' : '') }))) : h('p', { class: 'muted', text: t('none') }),
      h('h3', { text: t('cat.packagings') }), full.packagings.length ? h('ul', {}, full.packagings.map(x => h('li', { text: SM.name(x) + ' = ' + x.factor_num + '/' + x.factor_den }))) : h('p', { class: 'muted', text: t('none') }),
      h('button', { class: 'btn ghost', text: t('cat.lots'), onclick: () => { d.close(); SM.go('stock'); } })] });
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
    const f = { code: h('input', { class: 'input' }), name_ar: h('input', { class: 'input' }), name_en: h('input', { class: 'input', dir: 'ltr' }),
      kind: SM.select(['grocery', 'raw_meat', 'prepared', 'ingredient', 'packaging'].map(k => ({ value: k, label: t('kind.' + k) }))),
      price: h('input', { class: 'input', inputmode: 'decimal' }), barcode: h('input', { class: 'input', dir: 'ltr' }),
      needs_expiry: h('input', { type: 'checkbox', checked: true }), allergens: h('input', { class: 'input' }),
      carton: h('input', { class: 'input', inputmode: 'numeric', placeholder: '12' }), carton_barcode: h('input', { class: 'input', dir: 'ltr' }) };
    const d = SM.dialog({ title: t('cat.add'), wide: true, body: [h('div', { class: 'grid cols-2' },
      SM.field(t('cat.code'), f.code), SM.field(t('cat.kind'), f.kind, t('cat.kind_help')), SM.field(t('cat.name_ar'), f.name_ar), SM.field(t('cat.name_en'), f.name_en),
      SM.field(t('cat.price'), f.price, t('cat.price_help')), SM.field(t('cat.barcode'), f.barcode, t('cat.barcode_help')),
      SM.field(t('cat.carton_units'), f.carton), SM.field(t('cat.carton_barcode'), f.carton_barcode),
      SM.field(t('lots.allergens'), f.allergens, t('cat.allergens_help')), h('label', { class: 'row' }, f.needs_expiry, t('cat.needs_expiry')))],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
        const weighed = ['raw_meat', 'prepared', 'ingredient'].includes(f.kind.value);
        const body = { code: f.code.value, name_ar: f.name_ar.value, name_en: f.name_en.value || f.name_ar.value, kind: f.kind.value,
          measure: weighed ? 'g' : 'unit', needs_expiry: f.needs_expiry.checked, allergens: f.allergens.value,
          price: f.price.value ? SM.parseMoney(f.price.value) : undefined, barcodes: f.barcode.value ? [f.barcode.value.trim()] : [] };
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
      SM.put(root, h('p', { class: 'tip', text: t('scn.intro') }), h('div', { class: 'toolbar' }, seg), body, sheet);
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
        const below = h('input', { type: 'checkbox', checked: s.block_below_cost });
        const gtin = h('input', { type: 'checkbox', checked: s.gtin_equivalence });
        parts.push(h('div', { class: 'card' }, h('h3', { text: t('set.shop') }), h('div', { class: 'grid cols-3' },
          SM.field(t('set.shop_name'), name), SM.field(t('set.footer'), footer), SM.field(t('set.width'), width), SM.field(t('set.near'), near),
          SM.field(t('set.discount'), disc, t('set.discount_help')), SM.field(t('set.tolerance'), tol, t('set.tolerance_help')),
          h('label', { class: 'row' }, below, t('set.below_cost')), h('label', { class: 'row' }, gtin, t('set.gtin'))),
          h('p', { class: 'tip', text: t('set.tax_note') }),
          h('button', { class: 'btn primary', text: t('save'), onclick: async () => {
            try {
              await SM.api('POST', '/api/settings', { shop_name: name.value, receipt_footer: footer.value, receipt_width_mm: parseInt(width.value, 10),
                near_expiry_days: SM.parseInt(near.value), cashier_discount_permille: SM.parseInt(disc.value), cutting_loss_tolerance_permille: tol.value === '' ? null : SM.parseInt(tol.value),
                block_below_cost: below.checked, gtin_equivalence: gtin.checked });
              SM.state.settings = await SM.api('GET', '/api/settings'); SM.toast(t('saved'));
            } catch (e) { SM.fail(e); }
          } })));
        parts.push(await labelCard());
      }
      if (SM.can('users')) parts.push(await usersCard());
      if (SM.can('backup')) parts.push(await backupCard());
      SM.put(root, ...parts);
    },
  };

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
    const f = { username: h('input', { class: 'input', autocomplete: 'off' }), name: h('input', { class: 'input' }), password: h('input', { class: 'input', type: 'password', autocomplete: 'new-password' }),
      role: SM.select(['manager', 'cashier', 'receiver', 'butcher', 'counter', 'accountant', 'viewer', 'owner'].map(r => ({ value: r, label: t('role.' + r) }))) };
    return h('div', { class: 'card' }, h('h3', { text: t('set.users') }),
      SM.table([{ label: t('username'), render: u => h('span', { class: 'ltr', text: u.username }) }, { label: t('set.display'), key: 'display_name' }, { label: t('set.role'), render: u => t('role.' + u.role) }], users),
      h('div', { class: 'grid cols-4' }, SM.field(t('username'), f.username), SM.field(t('set.display'), f.name), SM.field(t('password'), f.password, t('set.pw_help')), SM.field(t('set.role'), f.role)),
      h('button', { class: 'btn', text: t('set.add_user'), onclick: async () => {
        try { await SM.api('POST', '/api/users', { username: f.username.value, name: f.name.value, password: f.password.value, role: f.role.value }); SM.toast(t('saved')); SM.go('settings'); } catch (e) { SM.fail(e); }
      } }));
  }

  async function backupCard() {
    const list = await SM.api('GET', '/api/backups').catch(() => []);
    return h('div', { class: 'card' }, h('h3', { text: t('set.backup') }), h('p', { class: 'muted', text: t('set.backup_help') }),
      h('button', { class: 'btn primary', text: t('set.backup_now'), onclick: async () => {
        try { const r = await SM.api('POST', '/api/backup', {}); SM.toast(t('set.backup_done')); SM.dialog({ title: t('set.backup_done'), body: h('code', { class: 'ltr', text: r.folder + '\nSHA-256 ' + r.sha256 }) }); } catch (e) { SM.fail(e); }
      } }),
      list.length ? h('ul', {}, list.slice(0, 10).map(b => h('li', { class: 'ltr', text: b.created_at + ' — schema ' + b.schema + ' — ' + b.integrity }))) : null);
  }

  /* ---------------- help ---------------- */
  SM.views.help = {
    icon: 'help',
    mount(root) {
      const topics = ['today', 'sell', 'receive', 'stock', 'prepare', 'count', 'cash', 'returns', 'reports', 'catalog', 'scanner', 'settings'];
      SM.put(root, 
        h('div', { class: 'tip' }, icon('school'), h('div', { text: SM.state.mode === 'training' ? t('help.in_training') : t('help.training') })),
        ...topics.map(v => h('details', { class: 'card faq', open: v === (SM.state.helpFor || 'today') }, h('summary', {}, h('b', { text: t('nav.' + v) })),
          h('p', { text: t('help.' + v) }), h('ol', { class: 'steps' }, (t('help.' + v + '.steps') || '').split('|').filter(Boolean).map(s => h('li', { text: s }))),
          h('p', { class: 'muted' }, h('b', { text: t('help.next') + ' ' }), t('help.' + v + '.next')))));
    },
  };
})();
