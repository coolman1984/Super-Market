/* Suppliers and customers: add, statement, pay a supplier (drawer or safe),
   collect from a customer and reverse a collection - all from the screen
   (audit delivery gap 1 and R11). */
'use strict';

(function () {
  const { h, t, icon } = SM;

  function statementTable(lines) {
    return SM.table([
      { label: t('party.date'), render: l => h('span', { class: 'ltr', text: (l.at || '').slice(0, 16) }) },
      { label: t('party.entry'), render: l => h('span', {}, t('entry.' + l.kind), l.sale_no ? ' ' : '', l.sale_no ? h('span', { class: 'ltr', text: l.sale_no }) : null,
        l.supplier_invoice ? h('span', { class: 'ltr muted', text: ' ' + l.supplier_invoice }) : null,
        l.reversed ? h('span', { class: 'badge warn', text: t('party.reversed') }) : null) },
      { label: t('cash.amount'), num: true, render: l => SM.num(SM.money(l.amount)) },
      { label: t('party.balance'), num: true, render: l => SM.num(SM.money(l.balance)) },
      { label: '', render: l => l.actions || null },
    ], lines);
  }

  async function customerDrawer(c, after) {
    const st = await SM.api('GET', '/api/customers/' + c.id + '/statement').catch(e => { SM.fail(e); return null; });
    if (!st) return;
    if (SM.can('cash_moves')) {
      for (const l of st.lines) {
        if (l.kind !== 'payment' || l.reversed) continue;
        const key = SM.newKey();
        l.actions = h('button', { class: 'btn sm danger', text: t('party.reverse'), onclick: SM.guard(async () => {
          const reason = prompt(t('party.reverse_q'));
          if (!reason) return;
          try { await SM.command('/api/customers/reverse', key, undefined, { entry_id: l.id, reason }); SM.toast(t('saved')); d.close(); after(); }
          catch (e) { SM.fail(e); }
        }) });
      }
    }
    const review = st.account && !st.account.ok ? h('div', { class: 'tip warn-tip', role: 'alert' }, icon('warn'), h('div', {}, h('b', { text: t('party.account_review') }), ' ',
      SM.can('users') && st.unallocated_collections.length && st.open_sales.length ? h('button', { class: 'btn sm', text: t('party.allocate'), onclick: () => allocateDialog(st, () => { d.close(); after(); }) }) : null)) : null;
    const d = SM.drawer({ title: st.customer.name, body: [review,
      h('p', {}, t('party.balance'), ' ', SM.num(SM.moneyText(st.balance)), ' — ', t('party.limit'), ' ', SM.num(SM.money(st.customer.credit_limit))),
      h('p', { class: 'muted', text: t('party.customer_help') }), statementTable(st.lines)] });
  }

  async function supplierDrawer(s, after) {
    const st = await SM.api('GET', '/api/suppliers/' + s.id + '/statement').catch(e => { SM.fail(e); return null; });
    if (!st) return;
    const parts = [h('p', {}, t('party.we_owe'), ' ', SM.num(SM.moneyText(st.balance))), statementTable(st.lines)];
    const open = st.open_returns || [];     // every return stays here: a part-credit does not complete it
    if (open.length) {
      parts.push(h('div', { class: 'card' }, h('h3', { text: t('party.returns') }), SM.table([
        { label: t('stock.lot'), render: r => h('span', { class: 'ltr', text: r.lot_no }) }, { label: t('stock.product'), render: r => SM.name(r) },
        { label: t('party.return_reason'), render: r => t('reason.' + r.reason) },
        { label: t('party.cost'), num: true, render: r => SM.can('see_cost') && r.cost !== undefined ? SM.num(SM.money(r.cost)) : '—' },
        { label: t('party.credited'), num: true, render: r => SM.num(SM.money(r.credited)) },
        { label: '', render: r => SM.can('supplier_pay') ? h('button', { class: 'btn sm', text: t('party.credit_note'), onclick: () => creditNoteDialog(r, () => { d.close(); after(); }) }) : null }], open)));
    }
    if (SM.can('supplier_pay')) {
      const amount = h('input', { class: 'input', inputmode: 'decimal' });
      const note = h('input', { class: 'input', maxlength: '200' });
      const source = SM.select([{ value: 'safe', label: t('party.from_safe') }, { value: 'drawer', label: t('party.from_drawer') }], SM.can('shift') ? 'drawer' : 'safe');
      const key = SM.newKey();
      parts.push(h('div', { class: 'card' }, h('h3', { text: t('party.pay_supplier') }), h('p', { class: 'muted', text: t('party.source_help') }),
        h('div', { class: 'grid cols-3' }, SM.field(t('cash.amount'), amount), SM.field(t('party.source'), source), SM.field(t('cash.note'), note)),
        h('button', { class: 'btn primary', text: t('party.pay'), onclick: SM.guard(async () => {
          const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
          try { await SM.command('/api/suppliers/pay', key, undefined, { supplier_id: s.id, amount: v, note: note.value || undefined, source: source.value });
            SM.toast(t('saved')); d.close(); after(); } catch (e) { SM.fail(e); }
        }) })));
    }
    const d = SM.drawer({ title: st.supplier.name, body: parts });
  }

  function creditNoteDialog(ret, after) {
    const amount = h('input', { class: 'input', inputmode: 'decimal' }), ref = h('input', { class: 'input', maxlength: '80', autocomplete: 'off' });
    const key = SM.newKey();
    const d = SM.dialog({ title: t('party.credit_note'), body: [SM.field(t('cash.amount'), amount), SM.field(t('party.credit_ref'), ref)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
        try { await SM.command('/api/supplier-credit-notes', key, undefined, { return_id: ret.id, amount: v, reference: ref.value }); d.close(); SM.toast(t('party.credit_done')); after(); }
        catch (e) { SM.fail(e); }
      }) })] });
  }

  /* Owner action for an account that needs review after an upgrade: tie a collection to an invoice. */
  function allocateDialog(st, after) {
    const entry = SM.select(st.unallocated_collections.map(e => ({ value: e.id, label: '#' + e.id + ' — ' + SM.money(e.free) })));
    const sale = SM.select(st.open_sales.map(x => ({ value: x.id, label: x.no + ' — ' + SM.money(x.open) })));
    const amount = h('input', { class: 'input', inputmode: 'decimal' }), note = h('input', { class: 'input', maxlength: '300' });
    const key = SM.newKey();
    const d = SM.dialog({ title: t('party.allocate'), body: [h('p', { class: 'tip', text: t('party.allocate_help') }),
      SM.field(t('party.allocate_entry'), entry), SM.field(t('party.allocate_sale'), sale), SM.field(t('party.allocate_amount'), amount), SM.field(t('party.allocate_note'), note)],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        const v = SM.parseMoney(amount.value); if (!v) return SM.toast(t('err.bad_amount'), 'bad');
        try { await SM.command('/api/account-reviews/allocate', key, undefined, { entry_id: parseInt(entry.value, 10), sale_id: parseInt(sale.value, 10), amount: v, note: note.value });
          d.close(); SM.toast(t('saved')); after(); } catch (e) { SM.fail(e); }
      }) })] });
  }

  function addDialog(kind, after) {
    const name = h('input', { class: 'input' }), phone = h('input', { class: 'input ltr', inputmode: 'tel' });
    const limit = h('input', { class: 'input', inputmode: 'decimal', placeholder: '0' });
    const d = SM.dialog({ title: t('party.add_' + kind), body: [SM.field(t('cat.name'), name), SM.field(t('party.phone'), phone, t('party.phone_help')),
      kind === 'customer' ? SM.field(t('party.limit'), limit, t('party.limit_help')) : null],
      actions: [h('button', { class: 'btn primary', text: t('save'), onclick: SM.guard(async () => {
        try { await SM.api('POST', '/api/parties', { kind, name: name.value, phone: phone.value || null, credit_limit: limit.value ? SM.parseMoney(limit.value) : 0 });
          SM.toast(t('saved')); d.close(); after(); } catch (e) { SM.fail(e); }
      }) })] });
  }

  SM.views.parties = {
    icon: 'list',
    async mount(root) {
      const parts = [];
      const load = () => SM.go('parties');
      if (SM.can('receive') || SM.can('supplier_pay')) {
        const sup = await SM.api('GET', '/api/parties?kind=supplier').catch(() => []);
        parts.push(h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('party.suppliers') }),
          SM.can('receive') ? h('button', { class: 'btn sm', onclick: () => addDialog('supplier', load) }, icon('plus', 'sm'), t('add')) : null),
          sup.length ? SM.table([{ label: t('cat.name'), key: 'name' }, { label: t('party.phone'), render: r => h('span', { class: 'ltr', text: r.phone || '' }) },
            { label: t('party.we_owe'), num: true, render: r => SM.num(SM.money(r.balance)) }], sup, r => supplierDrawer(r, load)) : SM.empty(t('party.none'))));
      }
      if (SM.can('sell')) {
        const cus = await SM.api('GET', '/api/parties?kind=customer').catch(() => []);
        parts.push(h('div', { class: 'card' }, h('header', {}, h('h3', { text: t('party.customers') }),
          SM.can('catalog') ? h('button', { class: 'btn sm', onclick: () => addDialog('customer', load) }, icon('plus', 'sm'), t('add')) : null),
          cus.length ? SM.table([{ label: t('cat.name'), key: 'name' }, { label: t('party.phone'), render: r => h('span', { class: 'ltr', text: r.phone || '' }) },
            { label: t('party.they_owe'), num: true, render: r => SM.num(SM.money(r.balance)) },
            { label: t('party.limit'), num: true, render: r => SM.num(SM.money(r.credit_limit)) }], cus, r => customerDrawer(r, load)) : SM.empty(t('party.none'))));
      }
      SM.put(root, ...parts);
    },
  };
})();
