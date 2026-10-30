/* Money screens: today, drawer/shift, returns, reports. */
'use strict';

(function () {
  const { h, t, icon } = SM;

  /* ---------------- today ---------------- */
  SM.views.today = {
    icon: 'home',
    async mount(root) {
      const shift = SM.can('shift') ? await SM.api('GET', '/api/shift').catch(() => null) : null;
      const steps = [];
      if (SM.can('shift') && !shift) steps.push(['cash', 'today.step_shift']);
      if (SM.can('receive')) steps.push(['receive', 'today.step_receive']);
      if (SM.can('cutting') || SM.can('produce')) steps.push(['prepare', 'today.step_prepare']);
      if (SM.can('sell') && shift) steps.push(['sell', 'today.step_sell']);
      if (SM.can('count')) steps.push(['count', 'today.step_count']);
      if (shift) steps.push(['cash', 'today.step_close']);
      const next = h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('today.next') })),
        h('ol', { class: 'steps' }, steps.map(([v, k]) => h('li', {}, h('a', { href: '#' + v, text: t(k) })))));
      const parts = [next];
      if (SM.can('reports')) {
        const r = await SM.api('GET', '/api/reports/day').catch(() => null);
        if (r) {
          const kpi = (label, value, hint, i) => h('div', { class: 'card kpi', style: { '--i': i } }, h('div', { class: 'label', text: label }), h('div', { class: 'value' }, SM.num(value)), hint ? h('div', { class: 'hint', text: hint }) : null);
          parts.unshift(h('div', { class: 'grid cols-4' },
            kpi(t('rep.net_sales'), SM.money(r.net_sales), t('rep.net_sales_hint'), 0),
            kpi(t('rep.sales_count'), String(r.sales_count), null, 1),
            kpi(t('rep.near_expiry'), String(r.near_expiry.length), t('rep.near_expiry_hint'), 2),
            kpi(t('rep.pending_payments'), String(r.pending_payments), t('rep.pending_hint'), 3)));
          if (r.held.length || r.near_expiry.length) parts.push(h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('today.attention') })),
            h('ul', {}, [...r.near_expiry, ...r.held].slice(0, 12).map(x => h('li', {}, SM.badge(x.state), ' ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); SM.lotDrawer(x.lot_id); } }, SM.name(x) + ' — ' + x.lot_no))))));
        }
      }
      if (SM.state.mode === 'training') parts.unshift(h('div', { class: 'tip' }, icon('school'), h('div', { text: t('today.training') })));
      SM.put(root, ...parts);
    },
  };

  /* ---------------- cash / shift ---------------- */
  SM.views.cash = {
    icon: 'cash', perm: 'shift',
    async mount(root) {
      const s = await SM.api('GET', '/api/shift').catch(() => null);
      if (!s) {
        const fl = h('input', { class: 'input big', inputmode: 'decimal', placeholder: '0' });
        const key = SM.newKey();
        SM.put(root, h('div', { class: 'card narrow' }, h('h3', { text: t('cash.open_title') }), h('p', { class: 'muted', text: t('cash.open_help') }),
          SM.field(t('cash.float'), fl), h('div', { class: 'form-actions' }, h('button', { class: 'btn primary lg', text: t('cash.open'), onclick: async () => {
            const v = fl.value ? SM.parseMoney(fl.value) : 0;
            if (v === null) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/shift/open', key, undefined, { opening_float: v }); SM.toast(t('cash.opened')); SM.go('sell'); } catch (e) { SM.fail(e); }
          } }))));
        return;
      }
      const moves = h('div', { class: 'card' }, h('h3', { text: t('cash.moves') }));
      if (SM.can('cash_moves')) {
        const kind = SM.select(['expense', 'drop', 'deposit', 'owner_draw'].map(k => ({ value: k, label: t('cash.k.' + k) })));
        const amount = h('input', { class: 'input', inputmode: 'decimal' });
        const note = h('input', { class: 'input', maxlength: '200' });
        moves.append(h('div', { class: 'grid cols-3' }, SM.field(t('cash.kind'), kind), SM.field(t('cash.amount'), amount), SM.field(t('cash.note'), note)),
          h('button', { class: 'btn', text: t('save'), onclick: async () => {
            const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/cash', SM.newKey(), undefined, { kind: kind.value, amount: v, note: note.value || undefined }); SM.toast(t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
          } }));
      } else moves.append(h('p', { class: 'muted', text: t('cash.moves_manager') }));
      const debt = h('div', { class: 'card' }, h('h3', { text: t('cash.debt') }));
      const customers = await SM.api('GET', '/api/parties?kind=customer').catch(() => []);
      if (customers.length) {
        const c = SM.select(customers.map(x => ({ value: x.id, label: x.name + ' — ' + SM.money(x.balance) })));
        const a = h('input', { class: 'input', inputmode: 'decimal' });
        debt.append(h('div', { class: 'grid cols-2' }, SM.field(t('cash.customer'), c), SM.field(t('cash.amount'), a)),
          h('button', { class: 'btn', text: t('cash.collect'), onclick: async () => {
            const v = SM.parseMoney(a.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/customers/pay', SM.newKey(), undefined, { customer_id: parseInt(c.value, 10), amount: v }); SM.toast(t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
          } }));
      }
      const counted = h('input', { class: 'input big', inputmode: 'decimal' });
      const note = h('input', { class: 'input', maxlength: '200' });
      const closeKey = SM.newKey();
      const close = h('div', { class: 'card' }, h('h3', { text: t('cash.close_title') }), h('p', { class: 'tip', text: t('cash.blind_tip') }),
        SM.field(t('cash.counted'), counted), SM.field(t('cash.close_note'), note),
        h('button', { class: 'btn primary lg', text: t('cash.close'), onclick: async () => {
          const v = SM.parseMoney(counted.value); if (v === null) return SM.toast(t('err.bad_amount'), 'bad');
          if (!(await SM.confirm(t('cash.close'), t('cash.close_q', { amount: SM.money(v) })))) return;
          try {
            const r = await SM.command('/api/shift/close', closeKey, undefined, { counted: v, note: note.value || undefined });
            SM.dialog({ title: t('cash.closed'), body: h('dl', { class: 'kv' }, h('dt', { text: t('cash.expected') }), h('dd', {}, SM.num(SM.moneyText(r.expected))),
              h('dt', { text: t('cash.counted') }), h('dd', {}, SM.num(SM.moneyText(r.counted))), h('dt', { text: t('cash.diff') }),
              h('dd', {}, h('span', { class: 'badge ' + (r.difference ? 'bad' : 'ok') }, SM.num(SM.money(r.difference))))) });
            SM.go('cash');
          } catch (e) { SM.fail(e); }
        } }));
      const info = h('div', { class: 'card' }, h('h3', { text: t('cash.current') }), h('p', {}, t('cash.since'), ' ', h('span', { class: 'ltr', text: s.shift.opened_at.slice(0, 16) }), ' — ', t('cash.sales_n', { n: s.sales })),
        s.noncash.length ? h('ul', {}, s.noncash.map(n => h('li', {}, t('pay.m.' + n.method), ' ', SM.badge(n.state), ' ', SM.num(SM.money(n.amount))))) : null,
        s.expected !== undefined ? h('p', {}, t('cash.expected'), ' ', SM.num(SM.moneyText(s.expected))) : h('p', { class: 'muted', text: t('cash.expected_hidden') }));
      const pending = SM.can('confirm_payments') ? await pendingCard() : null;
      SM.put(root, h('div', { class: 'grid cols-2' }, info, close, moves, debt), pending);
    },
  };

  async function pendingCard() {
    const rows = await SM.api('GET', '/api/payments/pending').catch(() => []);
    const card = h('div', { class: 'card' }, h('h3', { text: t('pay.pending_title') }), h('p', { class: 'muted', text: t('pay.pending_help') }));
    if (!rows.length) { card.append(h('p', { text: t('none') })); return card; }
    card.append(SM.table([{ label: t('receipt.no'), render: r => h('span', { class: 'ltr', text: r.no }) }, { label: t('pay.method'), render: r => t('pay.m.' + r.method) },
      { label: t('pay.reference'), render: r => r.reference || '—' }, { label: t('cash.amount'), num: true, render: r => SM.num(SM.money(r.amount)) },
      { label: '', render: r => h('div', { class: 'row' }, h('button', { class: 'btn sm', text: t('pay.confirmed'), onclick: () => decide(r, 'confirmed') }),
        h('button', { class: 'btn sm danger', text: t('pay.rejected'), onclick: () => decide(r, 'rejected') })) }], rows));
    async function decide(r, outcome) {
      try { await SM.command('/api/payments/confirm', SM.newKey(), undefined, { payment_id: r.id, outcome }); SM.toast(t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
    }
    return card;
  }

  /* ---------------- returns ---------------- */
  SM.views.returns = {
    icon: 'undo', perm: 'refund',
    mount(root) {
      const no = h('input', { class: 'input', placeholder: 'S-000001', autocomplete: 'off' });
      const box = h('div');
      const find = async () => {
        let sale;
        try { sale = await SM.api('GET', '/api/sales/' + encodeURIComponent(no.value.trim())); } catch (e) { return SM.fail(e); }
        const key = SM.newKey();
        const qtys = sale.lines.map(l => h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' }));
        const restock = sale.lines.map(() => h('input', { type: 'checkbox' }));
        const reason = SM.select(['quality', 'change_of_mind', 'wrong_item', 'other'].map(r => ({ value: r, label: t('ret.r.' + r) })));
        const method = SM.select(['cash', 'credit_note', 'original'].map(r => ({ value: r, label: t('ret.m.' + r) })));
        SM.put(box, h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('ret.sale', { no: sale.no }) }), h('span', { class: 'ltr muted', text: sale.at.slice(0, 16) })),
          SM.table([{ label: t('sell.item'), render: l => SM.name(l) }, { label: t('ret.sold'), render: l => SM.num(SM.qty(l.qty, l.measure)) },
            { label: t('ret.returned'), render: l => SM.num(SM.qty(l.returned_qty, l.measure)) }, { label: t('sell.amount'), num: true, render: l => SM.num(SM.money(l.net)) },
            { label: t('ret.qty'), render: l => qtys[sale.lines.indexOf(l)] },
            { label: t('ret.restock'), render: l => l.kind === 'grocery' ? h('label', { class: 'row' }, restock[sale.lines.indexOf(l)], t('ret.restock_ok')) : h('span', { class: 'badge warn', text: t('ret.quarantine_always') }) }], sale.lines),
          h('div', { class: 'grid cols-2' }, SM.field(t('ret.reason'), reason, t('ret.reason_help')), SM.field(t('ret.method'), method)),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn primary', text: t('ret.post'), onclick: async () => {
            const lines = [];
            for (const [i, l] of sale.lines.entries()) {
              if (!qtys[i].value) continue;
              const q = l.measure === 'g' ? SM.parseKg(qtys[i].value) : SM.parseInt(qtys[i].value);
              if (!q) return SM.toast(t('err.bad_number'), 'bad');
              lines.push({ sale_line_id: l.id, qty: q, restock: restock[i].checked });
            }
            if (!lines.length) return;
            try {
              const r = await SM.commandWithApproval('/api/returns', key, { sale_id: sale.id, lines, reason: reason.value, method: method.value });
              SM.dialog({ title: t('ret.done'), body: [h('p', {}, t('ret.amount'), ' ', SM.num(SM.moneyText(r.amount))), h('ul', {}, r.lines.map(x => h('li', {}, SM.badge(x.disposition), ' ', h('span', { class: 'ltr', text: x.lot }))))] });
              SM.put(box);
            } catch (e) { SM.fail(e); }
          } }))));
      };
      no.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); find(); } });
      SM.put(root, h('div', { class: 'card' }, h('p', { class: 'tip', text: t('ret.tip') }), h('div', { class: 'row wrap' }, SM.field(t('ret.find'), no), h('button', { class: 'btn', text: t('ret.search'), onclick: find }))), box);
      no.focus();
    },
  };

  /* ---------------- reports ---------------- */
  SM.views.reports = {
    icon: 'chart', perm: 'reports',
    async mount(root) {
      const day = h('input', { class: 'input', type: 'date', value: SM.today() });
      const box = h('div');
      const load = async () => {
        const r = await SM.api('GET', '/api/reports/day?day=' + day.value).catch(e => { SM.fail(e); return null; });
        if (!r) return;
        const kv = (k, v, why) => [h('dt', {}, k, why ? h('div', { class: 'help', text: why }) : null), h('dd', {}, SM.num(v))];
        const rec = await SM.api('GET', '/api/reconcile').catch(() => null);
        SM.put(box, h('div', { class: 'grid cols-2' },
          h('div', { class: 'card' }, h('h3', { text: t('rep.sales') }), h('dl', { class: 'kv' },
            kv(t('rep.sales_total'), SM.money(r.sales_total)), kv(t('rep.discounts'), SM.money(r.discounts)), kv(t('rep.returns'), SM.money(r.returns)),
            kv(t('rep.net_sales'), SM.money(r.net_sales), t('rep.net_sales_hint')),
            r.cost_of_sales !== undefined ? [kv(t('rep.cost'), SM.money(r.cost_of_sales)), kv(t('rep.margin'), SM.money(r.gross_margin), t('rep.margin_hint')),
              kv(t('rep.losses'), SM.money(r.losses), t('rep.losses_hint')), kv(t('rep.after_losses'), SM.money(r.margin_after_recorded_losses), t('rep.after_losses_hint'))] : null)),
          h('div', { class: 'card' }, h('h3', { text: t('rep.by_method') }), h('p', { class: 'muted', text: t('rep.sales_not_cash') }),
            h('dl', { class: 'kv' }, r.by_method.map(m => kv(t('pay.m.' + m.method) + (m.state === 'pending' ? ' (' + t('state.pending') + ')' : ''), SM.money(m.amount))))),
          h('div', { class: 'card' }, h('h3', { text: t('rep.shifts') }), r.shifts.length ? SM.table([{ label: '#', key: 'id' },
            { label: t('cash.expected'), num: true, render: s => s.expected === null ? t('state.open') : SM.num(SM.money(s.expected)) },
            { label: t('cash.counted'), num: true, render: s => s.counted === null ? '—' : SM.num(SM.money(s.counted)) },
            { label: t('cash.diff'), num: true, render: s => s.difference === null ? '—' : h('span', { class: 'badge ' + (s.difference ? 'bad' : 'ok') }, SM.num(SM.money(s.difference))) }], r.shifts) : h('p', { text: t('none') })),
          r.rejected_payments.length ? h('div', { class: 'card' }, h('h3', { text: t('rep.rejected') }), h('ul', {}, r.rejected_payments.map(x => h('li', { class: 'ltr', text: x.no + ' — ' + t('pay.m.' + x.method) + ' — ' + SM.money(x.amount) })))) : null,
          h('div', { class: 'card' }, h('h3', { text: t('rep.waste') }), r.waste.length ? h('dl', { class: 'kv' }, r.waste.map(w => kv(t('waste.' + w.reason), SM.money(w.cost)))) : h('p', { text: t('none') })),
          h('div', { class: 'card' }, h('h3', { text: t('rep.checks') }), h('p', {}, rec && rec.ok ? h('span', { class: 'badge ok', text: t('rep.reconcile_ok') }) : h('span', { class: 'badge bad', text: t('rep.reconcile_bad') })),
            h('p', { class: 'muted', text: t('rep.tax_unverified') }))));
      };
      day.onchange = load;
      SM.put(root, h('div', { class: 'toolbar' }, SM.field(t('rep.day'), day)), box);
      load();
    },
  };
})();
