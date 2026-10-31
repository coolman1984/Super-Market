/* Money screens: today, drawer/shift, returns, reports. */
'use strict';

(function () {
  const { h, t, icon } = SM;

  /* ---------------- today ---------------- */
  SM.views.today = {
    icon: 'home',
    async mount(root) {
      const [shift, report, gapsDoc] = await Promise.all([
        SM.can('shift') ? SM.api('GET', '/api/shift').catch(() => null) : null,
        SM.can('reports') ? SM.api('GET', '/api/reports/day').catch(() => null) : null,
        SM.can('reports') ? SM.api('GET', '/api/reports/gaps').catch(() => null) : null,
      ]);
      const gaps = (gapsDoc && gapsDoc.gaps) || [];
      const steps = [];
      // the owner is not sent to open a shift while a gap is waiting; a manager without one still is
      const ownerWaiting = SM.state.user && SM.state.user.role === 'owner' && gaps.length;
      if (SM.can('shift') && !shift && !ownerWaiting) steps.push(['cash', 'today.step_shift']);
      if (SM.can('receive')) steps.push(['receive', 'today.step_receive']);
      if (SM.can('cutting') || SM.can('produce')) steps.push(['prepare', 'today.step_prepare']);
      if (SM.can('sell') && shift) steps.push(['sell', 'today.step_sell']);
      if (SM.can('count')) steps.push(['count', 'today.step_count']);
      if (shift) steps.push(['cash', 'today.step_close']);
      const next = h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('today.next') })),
        h('ol', { class: 'steps' }, steps.map(([v, k]) => h('li', {}, h('a', { href: '#' + v, text: t(k) })))));
      const parts = [next];
      if (report) {
        const r = report;
        const kpi = (label, value, hint, i) => h('div', { class: 'card kpi', style: { '--i': i } }, h('div', { class: 'label', text: label }), h('div', { class: 'value' }, SM.num(value)), hint ? h('div', { class: 'hint', text: hint }) : null);
        parts.unshift(h('div', { class: 'grid cols-4' },
          kpi(t('rep.net_sales'), SM.money(r.net_sales), t('rep.net_sales_hint'), 0),
          kpi(t('rep.sales_count'), String(r.sales_count), null, 1),
          kpi(t('rep.near_expiry'), String(r.near_expiry.length), t('rep.near_expiry_hint'), 2),
          kpi(t('rep.pending_payments'), String(r.pending_payments), t('rep.pending_hint'), 3)));
        const attention = gaps.map(s => h('li', { class: 'gap-row' },
          h('a', { href: '#reports', onclick: (e) => openGap(s, e) }, h('span', { class: 'badge bad', text: gapText(s, report) })),
          h('button', { class: 'btn sm', text: t('today.gap_done'), onclick: SM.guard(() => settleGap(s)) })));
        attention.push(...[...r.near_expiry, ...r.held].slice(0, 12).map(x => h('li', {}, SM.badge(x.state), ' ', h('a', { href: '#', onclick: (e) => { e.preventDefault(); SM.lotDrawer(x.lot_id); } }, SM.name(x) + ' — ' + x.lot_no))));
        if (attention.length) parts.push(h('div', { class: 'card', id: 'today-attention' }, h('header', {}, h('h3', { text: t('today.attention') })), h('ul', {}, attention)));
      }
      if (SM.state.mode === 'training') parts.unshift(h('div', { class: 'tip' }, icon('school'), h('div', { text: t('today.training') })));
      SM.put(root, ...parts);
    },
  };

  function gapText(s, report) {
    const line = t(s.difference < 0 ? 'today.short' : 'today.over', { name: s.cashier || '—', amount: SM.moneyText(Math.abs(s.difference)) });
    return s.business_day && report && s.business_day !== report.day ? line + ' — ' + s.business_day : line;
  }
  function openGap(s, e) {
    e.preventDefault();
    SM.state.reportDay = s.business_day || null;
    SM.state.scrollShift = true;
    SM.go('reports');
  }
  async function settleGap(s) {
    let note;
    if (!s.close_note) {
      note = await SM.ask(t('today.gap_ask'));
      if (!note) return;
    }
    try {
      await SM.command('/api/shifts/gap', SM.newKey(), undefined, { shift_id: s.id, note: note || undefined });
      SM.toast(t('saved'));
      SM.go('today');
    } catch (e) { SM.fail(e); }
  }

  function shiftTable(rows) {
    return SM.table([{ label: '#', key: 'id' },
      { label: t('rep.cashier'), render: s => s.cashier || '—' },
      { label: t('rep.closed'), render: s => s.closed_at ? h('span', { class: 'ltr nowrap', text: s.closed_at.slice(0, 16).replace('T', ' ') }) : '—' },
      { label: t('cash.expected'), num: true, render: s => s.expected === null ? t('state.open') : SM.num(SM.money(s.expected)) },
      { label: t('cash.counted'), num: true, render: s => s.counted === null ? '—' : SM.num(SM.money(s.counted)) },
      { label: t('cash.diff'), num: true, render: s => s.difference === null ? '—' : h('span', { class: 'badge ' + (s.difference ? 'bad' : 'ok') }, SM.num(SM.money(s.difference))) },
      { label: t('rep.reason'), render: s => s.close_note || '—' }], rows);
  }
  function shiftCards(rows) {
    return h('div', { class: 'shift-cards' }, rows.map(s => h('article', { class: 'shift-card' },
      h('div', { class: 'shift-line' }, h('b', { text: s.cashier || '—' }),
        h('span', { class: 'muted ltr nowrap', text: s.closed_at ? s.closed_at.slice(0, 16).replace('T', ' ') : t('state.open') })),
      h('div', { class: 'shift-line' }, h('span', { class: 'muted', text: t('cash.diff') }),
        s.difference === null ? '—' : h('span', { class: 'badge ' + (s.difference ? 'bad' : 'ok') }, SM.num(SM.money(s.difference)))),
      h('div', { class: 'shift-line' }, h('span', { class: 'muted', text: t('rep.reason') }), h('span', { class: 'reason', text: s.close_note || '—' })),
      h('div', { class: 'muted' }, t('cash.expected'), ' ', s.expected === null ? t('state.open') : SM.money(s.expected),
        ' · ', t('cash.counted'), ' ', s.counted === null ? '—' : SM.money(s.counted)))));
  }

  /* ---------------- cash / shift ---------------- */
  SM.views.cash = {
    icon: 'cash', perm: 'shift',
    async mount(root) {
      const s = await SM.api('GET', '/api/shift').catch(() => null);
      if (!s) {
        const fl = h('input', { class: 'input big', inputmode: 'decimal', placeholder: '0', 'data-guide': 'cash.float' });
        const key = SM.newKey();
        SM.put(root, h('div', { class: 'card narrow' }, h('h3', { text: t('cash.open_title') }), h('p', { class: 'muted', text: t('cash.open_help') }),
          SM.field(t('cash.float'), fl), h('div', { class: 'form-actions' }, h('button', { class: 'btn primary lg', 'data-guide': 'cash.open', text: t('cash.open'), onclick: async () => {
            const v = fl.value ? SM.parseMoney(fl.value) : 0;
            if (v === null) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/shift/open', key, undefined, { opening_float: v }); SM.toast(t('cash.opened')); SM.go('sell'); } catch (e) { SM.fail(e); }
          } }))));
        return;
      }
      const moves = h('div', { class: 'card' }, h('h3', { text: t('cash.moves') }));
      if (SM.can('cash_moves')) {
        const moveKey = SM.newKey();
        const kind = SM.select(['expense', 'drop', 'deposit', 'owner_draw'].map(k => ({ value: k, label: t('cash.k.' + k) })));
        const amount = h('input', { class: 'input', inputmode: 'decimal' });
        const note = h('input', { class: 'input', maxlength: '200' });
        moves.append(h('div', { class: 'grid cols-3' }, SM.field(t('cash.kind'), kind), SM.field(t('cash.amount'), amount), SM.field(t('cash.note'), note)),
          h('button', { class: 'btn', text: t('save'), onclick: SM.guard(async () => {
            const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/cash', moveKey, undefined, { kind: kind.value, amount: v, note: note.value || undefined }); SM.toast(t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
          }) }));
      } else moves.append(h('p', { class: 'muted', text: t('cash.moves_manager') }));
      const debt = h('div', { class: 'card' }, h('h3', { text: t('cash.debt') }));
      const customers = await SM.api('GET', '/api/parties?kind=customer').catch(() => []);
      if (customers.length) {
        const c = SM.select(customers.map(x => ({ value: x.id, label: x.name + ' — ' + SM.money(x.balance) })));
        const a = h('input', { class: 'input', inputmode: 'decimal' });
        const payKey = SM.newKey();
        debt.append(h('div', { class: 'grid cols-2' }, SM.field(t('cash.customer'), c), SM.field(t('cash.amount'), a)),
          h('button', { class: 'btn', text: t('cash.collect'), onclick: SM.guard(async () => {
            const v = SM.parseMoney(a.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
            try { await SM.command('/api/customers/pay', payKey, undefined, { customer_id: parseInt(c.value, 10), amount: v }); SM.toast(t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
          }) }));
      }
      const counted = h('input', { class: 'input big', inputmode: 'decimal', 'data-guide': 'cash.counted' });
      const note = h('input', { class: 'input', maxlength: '200' });
      const closeKey = SM.newKey();
      const close = h('div', { class: 'card' }, h('h3', { text: t('cash.close_title') }), h('p', { class: 'tip', text: t('cash.blind_tip') }),
        SM.field(t('cash.counted'), counted), SM.field(t('cash.close_note'), note),
        h('button', { class: 'btn primary lg', 'data-guide': 'cash.close', text: t('cash.close'), onclick: async () => {
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
      const safe = SM.can('safe') ? safeCard() : null;
      SM.put(root, h('div', { class: 'grid cols-2' }, info, close, moves, debt), pending, safe);
    },
  };

  /* The safe: drops from drawers come in, supplier payments go out (audit R11). */
  function safeCard() {
    const card = h('div', { class: 'card' }, h('h3', { text: t('safe.title') }), h('p', { class: 'muted', text: t('safe.help') }));
    const amount = h('input', { class: 'input', inputmode: 'decimal' }), note = h('input', { class: 'input', maxlength: '200' });
    const kind = SM.select([{ value: 'deposit', label: t('safe.deposit') }, { value: 'withdrawal', label: t('safe.withdrawal') }]);
    const balance = h('p', {});
    const key = SM.newKey();
    SM.api('GET', '/api/reports/day').then(r => { if (r.safe !== null && r.safe !== undefined) SM.put(balance, t('safe.balance'), ' ', SM.num(SM.moneyText(r.safe))); }).catch(() => {});
    card.append(balance, h('div', { class: 'grid cols-3' }, SM.field(t('cash.kind'), kind), SM.field(t('cash.amount'), amount), SM.field(t('cash.note'), note)),
      h('button', { class: 'btn', text: t('save'), onclick: SM.guard(async () => {
        const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
        try { await SM.command('/api/safe', key, undefined, { kind: kind.value, amount: v, note: note.value || undefined }); SM.toast(t('saved')); SM.go('cash'); }
        catch (e) { SM.fail(e); }
      }) }));
    return card;
  }

  async function pendingCard() {
    const rows = await SM.api('GET', '/api/payments/pending').catch(() => []);
    const card = h('div', { class: 'card' }, h('h3', { text: t('pay.pending_title') }), h('p', { class: 'muted', text: t('pay.pending_help') }));
    if (!rows.length) { card.append(h('p', { text: t('none') })); return card; }
    card.append(SM.table([{ label: t('receipt.no'), render: r => h('span', { class: 'ltr', text: r.no }) }, { label: t('pay.method'), render: r => t('pay.m.' + r.method) },
      { label: t('pay.reference'), render: r => r.reference || '—' }, { label: t('cash.amount'), num: true, render: r => SM.num(SM.money(r.amount)) },
      { label: '', render: r => { r.key = SM.newKey(); return h('div', { class: 'row' }, h('button', { class: 'btn sm', text: t('pay.confirmed'), onclick: SM.guard(() => decide(r, 'confirmed')) }),
        h('button', { class: 'btn sm danger', text: t('pay.rejected'), onclick: SM.guard(() => decide(r, 'rejected')) })); } }], rows));
    async function decide(r, outcome) {   // one key per payment row: a second click cannot decide twice
      try { const res = await SM.command('/api/payments/confirm', r.key, undefined, { payment_id: r.id, outcome });
        SM.toast(res.follow_up ? t('pay.follow_' + res.follow_up) : t('saved')); SM.go('cash'); } catch (e) { SM.fail(e); }
    }
    return card;
  }

  /* ---------------- returns ---------------- */
  SM.views.returns = {
    icon: 'undo', perm: 'refund',
    mount(root) {
      const no = h('input', { class: 'input', placeholder: 'S-000001', autocomplete: 'off', 'data-guide': 'ret.find' });
      const box = h('div');
      const find = async () => {
        let sale;
        try { sale = await SM.api('GET', '/api/sales/' + encodeURIComponent(no.value.trim())); } catch (e) { return SM.fail(e); }
        const key = SM.newKey();
        const qtys = sale.lines.map(l => h('input', { class: 'input', inputmode: 'decimal', placeholder: '0', 'data-guide': 'ret.qty' }));
        const restock = sale.lines.map(() => h('input', { type: 'checkbox' }));
        const reason = SM.select(['quality', 'change_of_mind', 'wrong_item', 'other'].map(r => ({ value: r, label: t('ret.r.' + r) })));
        reason.setAttribute('data-guide', 'ret.reason');
        const method = SM.select(['original', 'cash', 'credit_note'].map(r => ({ value: r, label: t('ret.m.' + r) })));
        SM.put(box, h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('ret.sale', { no: sale.no }) }), h('span', { class: 'ltr muted', text: sale.at.slice(0, 16) })),
          SM.table([{ label: t('sell.item'), render: l => h('span', {}, SM.name(l), l.promo_discount ? h('span', { class: 'badge', title: t('ret.promo_note'), text: t('receipt.promo') }) : null) }, { label: t('ret.sold'), render: l => SM.num(SM.qty(l.qty, l.measure)) },
            { label: t('ret.returned'), render: l => SM.num(SM.qty(l.returned_qty, l.measure)) }, { label: t('sell.amount'), num: true, render: l => SM.num(SM.money(l.net)) },
            { label: t('ret.qty'), render: l => qtys[sale.lines.indexOf(l)] },
            { label: t('ret.restock'), render: l => l.kind === 'grocery' ? h('label', { class: 'row' }, restock[sale.lines.indexOf(l)], t('ret.restock_ok')) : h('span', { class: 'badge warn', text: t('ret.quarantine_always') }) }], sale.lines),
          sale.lines.some(l => l.promo_discount) ? h('p', { class: 'tip', text: t('ret.promo_note') }) : null,
          h('div', { class: 'grid cols-2' }, SM.field(t('ret.reason'), reason, t('ret.reason_help')), SM.field(t('ret.method'), method)),
          h('div', { class: 'form-actions' }, h('button', { class: 'btn primary', 'data-guide': 'ret.post', text: t('ret.post'), onclick: async () => {
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
              SM.dialog({ title: t('ret.done'), body: [h('p', {}, t('ret.amount'), ' ', SM.num(SM.moneyText(r.amount))),
                h('p', { class: 'muted' }, t('ret.reference', { no: r.reference_receipt })),
                r.cancelled_uncollected ? h('p', { class: 'badge warn', text: t('ret.cancelled_uncollected', { amount: SM.money(r.cancelled_uncollected) }) }) : null,
                h('ul', {}, (r.refunds || []).map(x => h('li', {}, t('refund.' + x.method), ' ', SM.num(SM.money(x.amount))))), h('ul', {}, r.lines.map(x => h('li', {}, SM.badge(x.disposition), ' ', h('span', { class: 'ltr', text: x.lot }))))] });
              SM.put(box);
            } catch (e) { SM.fail(e); }
          } }))));
      };
      no.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); find(); } });
      SM.put(root, h('div', { class: 'card' }, h('p', { class: 'tip', text: t('ret.tip') }), h('div', { class: 'row wrap' }, SM.field(t('ret.find'), no), h('button', { class: 'btn', 'data-guide': 'ret.search', text: t('ret.search'), onclick: find }))), box);
      no.focus();
    },
  };

  /* ---------------- reports ---------------- */
  SM.views.reports = {
    icon: 'chart', perm: 'reports',
    async mount(root) {
      // the business day moves while the program stays open (day-start hour, a day close): ask the server each time
      const st = await SM.api('GET', '/api/state').catch(() => null);
      if (st && st.business_day) SM.state.businessDay = st.business_day;
      const focusDay = SM.state.reportDay;
      SM.state.reportDay = null;
      const scrollShift = SM.state.scrollShift;
      SM.state.scrollShift = false;
      const day = h('input', { class: 'input', type: 'date', value: focusDay || SM.state.businessDay || SM.today(), 'data-guide': 'rep.day' });
      const box = h('div');
      const load = async () => {
        const r = await SM.api('GET', '/api/reports/day?day=' + day.value).catch(e => { SM.fail(e); return null; });
        if (!r) return;
        const kv = (k, v, why) => [h('dt', {}, k, why ? h('div', { class: 'help', text: why }) : null), h('dd', {}, SM.num(v))];
        const rec = await SM.api('GET', '/api/reconcile').catch(() => null);
        const closed = await SM.api('GET', '/api/days/' + day.value).catch(() => null);
        /* row 38: a closed day shows its kept figures and every later correction under it */
        const closeCard = h('div', { class: 'card' }, h('h3', { text: t('close.title') }),
          closed && closed.closed ? [h('p', {}, h('span', { class: 'badge ' + (closed.snapshot_ok ? 'ok' : 'bad'), text: closed.snapshot_ok ? t('close.closed') : t('close.tampered') }),
              ' ', h('span', { class: 'muted ltr', text: closed.at.slice(0, 16) })),
            h('p', { class: 'muted', text: t('close.kept') + ' ' + SM.money(closed.snapshot.net_sales) }),
            h('h4', { text: t('close.corrections') }),
            closed.corrections.length ? h('ul', { class: 'help-list' }, closed.corrections.map(c => h('li', {}, t('close.kind.' + c.kind), ' — ', h('span', { class: 'ltr', text: c.ref + ' — ' + c.at.slice(0, 16) }), ' — ', c.reason)))
              : h('p', { class: 'muted', text: t('none') })]
            : [h('p', { class: 'muted', text: t('close.help') }),
              SM.can('close_day') ? h('button', { class: 'btn', 'data-guide': 'close.btn', text: t('close.btn'), onclick: SM.guard(async () => {
                if (!(await SM.confirm(t('close.btn'), t('close.confirm', { day: day.value })))) return;
                try { await SM.command('/api/days/close', SM.newKey(), undefined, { day: day.value }); SM.toast(t('close.done')); load(); } catch (e) { SM.fail(e); }
              }) }) : null]);
        const dayNote = h('p', { class: 'muted', text: t('rep.business_day_note', { hour: r.day_start_hour }) });
        SM.put(box, dayNote, closeCard, h('div', { class: 'grid cols-2', 'data-guide': 'rep.figures' },
          h('div', { class: 'card' }, h('h3', { text: t('rep.sales') }), h('dl', { class: 'kv' },
            kv(t('rep.sales_total'), SM.money(r.sales_total)), kv(t('rep.discounts'), SM.money(r.discounts)), kv(t('rep.returns'), SM.money(r.returns)),
            kv(t('rep.net_sales'), SM.money(r.net_sales), t('rep.net_sales_hint')),
            r.cost_of_sales !== undefined ? [kv(t('rep.cost'), SM.money(r.cost_of_sales)), kv(t('rep.margin'), SM.money(r.gross_margin), t('rep.margin_hint')),
              kv(t('rep.losses'), SM.money(r.losses), t('rep.losses_hint')), kv(t('rep.after_losses'), SM.money(r.margin_after_recorded_losses), t('rep.after_losses_hint')),
              r.cost_adjustments ? kv(t('rep.cost_adjustments'), SM.money(r.cost_adjustments), t('rep.cost_adjustments_hint')) : null,
              r.purchase_variance ? kv(t('rep.purchase_variance'), SM.money(r.purchase_variance), t('rep.purchase_variance_hint')) : null,
              r.supplier_return_adjustments ? kv(t('rep.supplier_return_adjustments'), SM.money(r.supplier_return_adjustments)) : null,
              r.supplier_rebates ? kv(t('rep.supplier_rebates'), SM.money(r.supplier_rebates), t('rep.supplier_rebates_hint')) : null] : null,
            r.gift_lines ? kv(t('rep.gift_lines'), String(r.gift_lines), t('rep.gift_lines_hint')) : null)),
          h('div', { class: 'card' }, h('h3', { text: t('rep.by_method') }), h('p', { class: 'muted', text: t('rep.sales_not_cash') }),
            h('dl', { class: 'kv' }, r.by_method.map(m => kv(t('pay.m.' + m.method) + (m.state !== 'settled' ? ' (' + t('state.' + m.state) + ')' : ''), SM.money(m.amount)))),
            r.refunds_by_method && r.refunds_by_method.length ? [h('h4', { text: t('rep.refunds') }), h('dl', { class: 'kv' }, r.refunds_by_method.map(m => kv(t('refund.' + m.method), SM.money(m.amount))))] : null),
          h('div', { class: 'card', id: 'shifts-card', style: { gridColumn: '1 / -1' } }, h('h3', { text: t('rep.shifts') }), r.shifts.length ? [shiftTable(r.shifts), shiftCards(r.shifts)] : h('p', { text: t('none') })),
          r.rejected_payments.length ? h('div', { class: 'card' }, h('h3', { text: t('rep.rejected') }), h('ul', {}, r.rejected_payments.map(x => h('li', { class: 'ltr', text: x.no + ' — ' + t('pay.m.' + x.method) + ' — ' + SM.money(x.amount) })))) : null,
          h('div', { class: 'card' }, h('h3', { text: t('rep.waste') }), r.waste.length ? h('dl', { class: 'kv' }, r.waste.map(w => kv(t('waste.' + w.reason), SM.money(w.cost)))) : h('p', { text: t('none') })),
          h('div', { class: 'card' }, h('h3', { text: t('rep.checks') }), h('p', {}, rec && rec.ok ? h('span', { class: 'badge ok', text: t('rep.reconcile_ok') }) : h('span', { class: 'badge bad', text: t('rep.reconcile_bad') })),
            h('p', { class: 'muted', text: t('rep.tax_unverified') }))));
        if (scrollShift) {
          const el = document.getElementById && document.getElementById('shifts-card');
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
        }
      };
      day.onchange = load;
      const from = h('input', { class: 'input', type: 'date', value: SM.today().slice(0, 8) + '01' });
      const to = h('input', { class: 'input', type: 'date', value: SM.today() });
      const periodBox = h('div');
      const showPeriod = async () => {
        const r = await SM.api('GET', '/api/reports/period?start=' + from.value + '&end=' + to.value).catch(e => { SM.fail(e); return null; });
        if (!r) return;
        SM.put(periodBox, SM.table([{ label: t('rep.day'), render: x => h('span', { class: 'ltr', text: x.day }) }, { label: t('rep.sales_count'), num: true, render: x => SM.num(String(x.sales)) },
          { label: t('rep.sales_total'), num: true, render: x => SM.num(SM.money(x.sales_total)) }, { label: t('rep.returns'), num: true, render: x => SM.num(SM.money(x.returns)) },
          { label: t('rep.net_sales'), num: true, render: x => SM.num(SM.money(x.net_sales)) }], r.series),
          h('p', { class: 'muted' }, t('rep.net_sales'), ': ', SM.num(SM.moneyText(r.totals.net_sales)), ' — ', t('rep.net_sales_hint')));
      };
      const agingBox = h('div');
      const showAging = async () => {
        const r = await SM.api('GET', '/api/aging').catch(() => null);
        if (!r) return;
        const table = (title, rows) => h('div', {}, h('h4', { text: title }), rows.length ? SM.table([{ label: t('cat.name'), key: 'name' },
          { label: t('rep.age_0_30'), num: true, render: x => SM.num(SM.money(x.d0_30)) }, { label: t('rep.age_31_60'), num: true, render: x => SM.num(SM.money(x.d31_60)) },
          { label: t('rep.age_61'), num: true, render: x => SM.num(SM.money(x.d61_plus)) }], rows) : h('p', { class: 'muted', text: t('none') }));
        SM.put(agingBox, table(t('rep.aging_customers'), r.customers), table(t('rep.aging_suppliers'), r.suppliers));
      };
      SM.put(root, h('div', { class: 'toolbar' }, SM.field(t('rep.day'), day)), box,
        h('div', { class: 'card' }, h('h3', { text: t('rep.period') }), h('div', { class: 'row wrap' }, SM.field(t('rep.from'), from), SM.field(t('rep.to'), to),
          h('button', { class: 'btn', text: t('rep.show'), onclick: SM.guard(showPeriod) })), periodBox),
        h('div', { class: 'card' }, h('h3', { text: t('rep.aging') }), agingBox));
      load(); showAging();
    },
  };
})();
