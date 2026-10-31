/* Help: one structured block per screen (purpose, requirements, steps tied to real
   buttons, worked example, common errors, safe correction, success result, next
   step for YOUR role and state) plus training exercises proven from the ledger. */
'use strict';

(function () {
  const { h, t, icon } = SM;
  const TOPICS = ['today', 'sell', 'cash', 'returns', 'receive', 'stock', 'prepare', 'count', 'reports', 'parties', 'catalog', 'scanner', 'settings'];
  /* [required permissions (| = any, & = all, ~shift_open = no open shift), tag]; first match wins. */
  const NEXT = {
    today: [['shift&~shift_open', 'shift'], ['receive', 'receive'], ['cutting|produce', 'prepare'], ['count', 'count'], ['reports', 'reports'], ['*', 'any']],
    sell: [['shift&~shift_open', 'open_first'], ['reports', 'manager'], ['shift', 'close'], ['*', 'any']],
    cash: [['reports', 'manager'], ['shift', 'cashier'], ['*', 'any']],
    returns: [['release_hold', 'manager'], ['*', 'any']],
    receive: [['cutting|produce', 'prepare'], ['*', 'handover']],
    stock: [['reports', 'manager'], ['*', 'any']],
    prepare: [['*', 'sell']],
    count: [['count_approve', 'approve'], ['*', 'any']],
    reports: [['release_hold', 'manager'], ['*', 'any']],
    parties: [['supplier_pay|shift', 'pay'], ['*', 'any']],
    catalog: [['receive', 'receive'], ['*', 'any']],
    scanner: [['*', 'any']],
    settings: [['users', 'owner'], ['*', 'any']],
  };
  SM.helpNextRules = NEXT;

  function matches(rule, ctx) {
    if (rule === '*') return true;
    return rule.split('&').every(part => {
      if (part === '~shift_open') return !ctx.shiftOpen;
      return part.split('|').some(p => SM.can(p));
    });
  }
  /* The tag for this user, this state. Never sends a counter to sell or an accountant to open a shift. */
  SM.helpNext = (view, ctx) => (NEXT[view].find(([rule]) => matches(rule, ctx)) || [null, 'any'])[1];

  const list = (text, ordered) => h(ordered ? 'ol' : 'ul', { class: ordered ? 'steps' : 'help-list' },
    SM.fill(text).split('|').filter(Boolean).map(s => h('li', { text: s })));

  function block(v, ctx) {
    const k = (f) => t('help.' + v + '.' + f);
    const row = (f, body) => h('div', { class: 'help-row' }, h('h4', { text: t('help.field.' + f) }), body);
    return h('details', { class: 'card faq', id: 'help-' + v, open: v === (SM.state.helpFor || 'today') },
      h('summary', {}, h('b', { text: t('nav.' + v) })),
      row('purpose', h('p', { text: k('purpose') })),
      row('need', h('p', { text: SM.fill(k('need')) })),
      row('steps', list(k('steps'), true)),
      row('example', h('p', { class: 'example', text: SM.fill(k('example')) })),
      row('errors', list(k('errors'), false)),
      row('fix', h('p', { text: SM.fill(k('fix')) })),
      row('ok', h('p', { text: k('ok') })),
      row('next', h('p', { class: 'next-step', text: t('help.' + v + '.next.' + SM.helpNext(v, ctx)) })));
  }

  async function trainingCard() {
    const data = await SM.api('GET', '/api/training/progress').catch(() => null);
    if (!data) return null;
    return h('div', { class: 'card', id: 'training' }, h('header', {}, h('h3', { text: t('train.title') })), h('p', { class: 'muted', text: t('train.help') }),
      h('ul', { class: 'help-list' }, data.exercises.map(x => h('li', { class: x.done ? 'ex-done' : 'ex-todo' },
        h('span', { class: 'badge ' + (x.done ? 'ok' : 'warn'), text: x.done ? t('train.done') : t('train.todo') }), ' ', t('train.ex.' + x.id),
        x.done ? h('span', { class: 'muted' }, ' — ', t('train.evidence'), ': ', h('span', { class: 'ltr', text: x.evidence.join(' ') })) : null,
        !x.for_you ? h('span', { class: 'muted', text: ' — ' + t('train.not_for_role') }) : null,
        x.waiting_for.length ? h('span', { class: 'muted', text: ' — ' + t('train.waiting', { list: x.waiting_for.map(n => t('train.ex.' + n)).join('، ') }) }) : null))),
      h('p', { class: 'muted', text: t('train.reset_help') }));
  }

  SM.views.help = {
    icon: 'help',
    async mount(root) {
      const shift = SM.can('shift') ? await SM.api('GET', '/api/shift').catch(() => null) : null;
      const ctx = { shiftOpen: !!(shift && shift.shift && !shift.shift.closed_at) };
      const intro = h('div', { class: 'tip' }, icon('school'), h('div', { text: SM.state.mode === 'training' ? t('help.in_training') : t('help.training') }));
      const training = SM.state.mode === 'training' ? await trainingCard() : null;
      SM.put(root, intro, training, ...TOPICS.map(v => block(v, ctx)));
    },
  };
})();
