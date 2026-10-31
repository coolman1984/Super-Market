/* The in-app guide, the same engine as the Yousef app (js/vendor/af-guide.js, not edited here):
   the «الدليل» button, the course of my role, the coach that outlines the real button and moves on by
   itself, the "?" of each page and «ماذا أفعل؟» on an error. Progress is saved per person on the server
   (/api/guide); a lesson the shop's records already prove counts as done. */
'use strict';

(function () {
  let files = null, ctl = null, generation = 0, listeners = [];
  // after the server confirms an action, the coach moves on (af-guide "event" steps) and refreshes done lessons
  const EVENTS = { '/api/shift/open': 'shift.opened', '/api/sales': 'sale.saved', '/api/returns': 'return.saved',
    '/api/shift/close': 'shift.closed', '/api/receipts': 'receipt.saved', '/api/production': 'production.saved',
    '/api/suppliers/pay': 'supplier.paid', '/api/days/close': 'day.closed' };

  function loadFiles() {
    if (!files) {
      files = Promise.all(['catalogue', 'ar', 'en'].map(n => fetch('guide/' + n + '.json', { credentials: 'same-origin' })
        .then(r => { if (!r.ok) throw new Error('guide'); return r.json(); })))
        .then(a => ({ catalogue: a[0], ar: a[1], en: a[2] }), e => { files = null; throw e; });
    }
    return files;
  }
  const request = (method, url, body) => SM.api(method, url, body);

  function unmount() {
    generation++;
    if (ctl) { try { ctl.stop(); } catch (e) { /* already gone */ } }
    listeners.forEach(l => l[0].removeEventListener(l[1], l[2], l[3]));
    listeners = [];
    document.querySelectorAll('[data-afg]').forEach(n => n.remove());
    ctl = null;
  }

  function mount() {
    unmount();
    const gen = generation;
    if (!window.AFGuide || !SM.state.user) return Promise.resolve(null);
    return loadFiles().then(pack => {
      if (gen !== generation || !SM.state.user) return null;
      // the engine has stop() but no destroy(): keep its global listeners so the next person starts clean
      const targets = [document, window], originals = targets.map(t => t.addEventListener);
      try {
        targets.forEach((t, i) => { t.addEventListener = function (type, fn, opts) { listeners.push([t, type, fn, opts]); originals[i].call(t, type, fn, opts); }; });
        ctl = window.AFGuide.init({
          catalogue: pack.catalogue, texts: { ar: pack.ar, en: pack.en }, role: SM.state.user.role, person: SM.state.user.id,
          ui: (key, lang) => (SM.i18n[lang] || {})[key] !== undefined ? SM.i18n[lang][key] : (SM.i18n.en || {})[key],
          uiLang: () => SM.state.lang,
          route: () => SM.state.view,
          go: (page) => SM.go(page),
          can: (perm) => perm === '*' || SM.can(perm),
          request, api: '/api/guide',
        });
      } finally { targets.forEach((t, i) => { t.addEventListener = originals[i]; }); }
      attachHelp();
      return ctl.ready;
    }, () => null);
  }
  /* the per-page "?" in the top bar, as in the Yousef app; the shell is rebuilt on language change */
  function attachHelp() {
    const bar = document.querySelector('.topbar');
    if (!ctl || !bar || bar.querySelector('[data-afg="help"]')) return;
    bar.insertBefore(ctl.helpButton(), bar.querySelector('.grow') ? bar.querySelector('.grow').nextSibling : null);
  }

  SM.guide = {
    mount, unmount,
    control: () => ctl,
    open: (page) => { if (ctl) ctl.open(page); },
    routeChanged: () => { if (ctl) { ctl.routeChanged(); attachHelp(); } },
    signal(path) {
      if (!ctl) return;
      const name = EVENTS[path] || (/^\/api\/counts\/\d+\/line$/.test(path) ? 'count.saved' : null);
      if (name) ctl.signal(name);
      SM.api('GET', '/api/guide/state').then(st => { if (ctl && Array.isArray(st.states)) ctl.setStates(st.states); }, () => {});
    },
    /* «ماذا أفعل؟» next to an error that has a problem entry (every server error has one) */
    errorButton: (code) => (ctl && code ? ctl.errorButton('err.' + code) : null),
  };
})();
