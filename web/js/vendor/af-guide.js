/* Vendored from Apps-Factory packages/af-guide 0.1.3 - do not edit here. */
/* Update with: python scripts/vendor_guide.py <product repo> (from the Apps-Factory checkout) */
/* af-guide 0.1.0: the factory's in-app guide (role courses, coach with auto-advance, per-page help, problem
   entries, per-guide language switch, progress saved per person on the server). No dependencies; DOM built with textContent only.
   Reads the same catalogue.json + <lang>.json as af_guide.py. See packages/af-guide/README.md.

     const guide = AFGuide.init({catalogue, texts: {ar, en}, role: 'cashier', ui: (key, lang) => t(key, lang),
                                 uiLang: () => currentLang, route: () => currentPage, go: page => navigate(page),
                                 can: perm => me.perms.includes(perm), track: (type, data) => {}, onReport: ctx => {}});
     guide.signal('shift.opened');       // after the server confirms an action (auto-advance "event" steps)
     guide.explain('sale.no_shift');     // open the problem entry for an error code (HELP-09)
     header.append(guide.helpButton());  // the per-page "?"                                                      */
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AFGuide = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  'use strict';
  const VERSION = '0.1.3';
  const UI_REF = /\[\[([A-Za-z0-9_.\-]+)\]\]/g;
  const WORDS = {
    ar: {guide: 'الدليل', path: 'طريقك', page: 'هذه الصفحة', problems: 'مشكلات هذه الصفحة', start: 'ابدأ',
      again: 'أعد الدرس', next: 'التالي', back: 'السابق', there: 'خذني هناك', stop: 'إيقاف', close: 'إغلاق',
      resume: 'أكمل الدرس', later: 'لاحقًا', finished: 'انتهيت', step: 'خطوة {i} من {n}', progress: 'أنهيت {d} من {t}',
      minutes: '{m} دقائق', see: 'ماذا ترى', why: 'السبب', todo: 'ماذا تفعل', still: 'إذا لم ينجح ذلك',
      report: 'أبلغ عن مشكلة', openGuide: 'افتح الدرس', openPage: 'اذهب إلى الصفحة', other: 'English',
      done: 'تم', locked: 'بعد درس سابق', auto: 'تم من بياناتك', empty: 'لا يوجد درس لهذه الصفحة بعد.',
      waiting: 'سأنتقل وحدي عندما تنتهي من هذه الخطوة.', notHere: 'هذه الخطوة في صفحة أخرى.', help: 'مساعدة هذه الصفحة',
      mistake: 'إذا أخطأت', ok: 'كيف تعرف أنها نجحت'},
    en: {guide: 'Guide', path: 'Your path', page: 'This page', problems: 'Problems on this page', start: 'Start',
      again: 'Do it again', next: 'Next', back: 'Back', there: 'Take me there', stop: 'Stop', close: 'Close',
      resume: 'Resume the lesson', later: 'Later', finished: 'Finish', step: 'Step {i} of {n}', progress: '{d} of {t} done',
      minutes: '{m} min', see: 'What you see', why: 'Why', todo: 'What to do', still: 'If that did not work',
      report: 'Report a problem', openGuide: 'Open the lesson', openPage: 'Go to the page', other: 'العربية',
      done: 'Done', locked: 'After an earlier lesson', auto: 'Done from your data', empty: 'No lesson for this page yet.',
      waiting: 'I will move on by myself when you finish this step.', notHere: 'This step is on another page.',
      help: 'Help for this page', mistake: 'If you make a mistake', ok: 'How you know it worked'}
  };
  const fmt = (s, v) => String(s).replace(/\{(\w+)\}/g, (_, k) => (k in v ? v[k] : ''));
  // `replaceChildren(a, null, b)` prints the null as the word "null" (it reached the guide's coach on every step, 0.1.2): only real nodes go in.
  const kept = (list) => list.filter((x) => x !== null && x !== undefined && x !== false);

  // ------------------------------------------------------------ pure helpers (unit-tested with node --test)
  function parts(text, label) {
    const out = [];
    let last = 0;
    String(text || '').replace(UI_REF, (m, key, at) => {
      if (at > last) out.push({t: text.slice(last, at)});
      out.push({ui: key, label: label ? label(key) : key});
      last = at + m.length;
      return m;
    });
    if (last < String(text || '').length) out.push({t: String(text).slice(last)});
    return out;
  }
  function plain(text, label) {
    return parts(text, label).map(p => (p.ui ? '«' + p.label + '»' : p.t)).join('');
  }
  function untilOf(step) {
    if (step.until) return step.until;
    if (step.k === 'go' && step.page) return {route: step.page};
    return null;
  }
  function untilMet(step, env) {
    const u = untilOf(step);
    if (!u) return false;
    const [kind, val] = Object.entries(u)[0];
    if (kind === 'route') return env.route === val;
    if (kind === 'target') return !!env.present(val);
    if (kind === 'gone') return !env.present(val);
    if (kind === 'filled') return !!env.filled(val);
    if (kind === 'dialog') return !!env.dialog(val);
    if (kind === 'event') return env.events.has(val);
    return false;
  }
  function course(cat, roleId, progress, states, can) {
    const role = (cat.roles || []).find(r => r.id === roleId);
    const guides = Object.fromEntries((cat.guides || []).map(g => [g.id, g]));
    if (!role) return {role: null, items: [], done: 0, total: 0, next: null};
    const live = new Set(states || []);
    const done = (progress && progress.done) || {};
    const items = [];
    for (const id of role.path || []) {
      const g = guides[id];
      if (!g || (can && g.perm && g.perm !== '*' && !can(g.perm))) continue;
      const auto = !!(g.done && live.has(g.done.state));
      items.push({id, done: id in done || auto, auto: auto && !(id in done), minutes: g.minutes});
    }
    const doneIds = new Set(items.filter(i => i.done).map(i => i.id));
    const inPath = new Set(items.map(i => i.id));
    for (const i of items) i.locked = (guides[i.id].requires || []).some(r => inPath.has(r) && !doneIds.has(r));
    const nxt = items.find(i => !i.done && !i.locked);
    return {role: roleId, items, done: doneIds.size, total: items.length, next: nxt ? nxt.id : null};
  }
  function pageOfStep(cat, s) {
    if (s.page) return s.page;
    const t = s.target && (cat.targets || {})[s.target];
    return t ? t.page || null : null;
  }
  function stepPage(cat, guide, n) {
    let page = null;
    for (let i = 0; i <= n && i < guide.steps.length; i++) page = pageOfStep(cat, guide.steps[i]) || page;
    return page;
  }
  function guidesForPage(cat, page, can) {
    return (cat.guides || []).filter(g => (!can || !g.perm || g.perm === '*' || can(g.perm)) &&
      g.steps.some(s => pageOfStep(cat, s) === page));
  }
  function problemsForPage(cat, page, roleId) {
    return (cat.problems || []).filter(p => p.page === page && (!roleId || !p.roles || !p.roles.length || p.roles.includes(roleId)));
  }
  function problemFor(cat, code) {
    return (cat.problems || []).find(p => (p.errors || []).includes(code)) || null;
  }

  // ------------------------------------------------------------ browser controller
  function init(opts) {
    const doc = root.document;
    const cat = opts.catalogue;
    const texts = opts.texts || {};
    const langs = cat.languages || ['ar'];
    const key = 'afg.' + cat.product + '.' + (opts.person || 'me');
    const val = (v, d) => (typeof v === 'function' ? v() : (v === undefined ? d : v));
    const request = opts.request || ((method, url, body) => root.fetch(url, {
      method, credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))));
    const base = opts.api === undefined ? '/api/guide' : opts.api;
    const track = (type, data) => { try { if (opts.track) opts.track(type, data); } catch (e) { /* never break the app */ } };
    const st = {progress: {v: 1, done: {}, current: null, lang: null, dismissed: false}, states: [],
      events: new Set(), open: false, page: null, coach: null, paused: false, timer: null, highlighted: null};
    let cached = null;
    try { cached = JSON.parse(root.localStorage.getItem(key) || 'null'); } catch (e) { cached = null; }
    if (cached && cached.v === 1) st.progress = cached;
    const lang = () => (langs.includes(st.progress.lang) ? st.progress.lang : (langs.includes(val(opts.uiLang)) ? val(opts.uiLang) : cat.defaultLang || langs[0]));
    const W = k => (WORDS[lang()] || WORDS.en)[k];
    const T = k => (texts[lang()] || {})[k] || '';
    const uiLabel = k => {
      const ul = val(opts.uiLang, cat.defaultLang);
      if (typeof opts.ui === 'function') return opts.ui(k, ul) || k;
      return ((opts.ui || {})[ul] || {})[k] || k;
    };
    const guides = Object.fromEntries((cat.guides || []).map(g => [g.id, g]));

    function el(tag, attrs, ...kids) {
      const n = doc.createElement(tag);
      for (const [k, v] of Object.entries(attrs || {})) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'on') for (const [ev, fn] of Object.entries(v)) n.addEventListener(ev, fn);
        else if (k === 'text') n.textContent = v;
        else if (k === 'pct') n.style.width = v + '%';   // CSSOM, not a style attribute: works under a strict CSP
        else n.setAttribute(k, v === true ? '' : v);
      }
      for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid.nodeType ? kid : doc.createTextNode(String(kid)));
      return n;
    }
    function rich(text) {
      return parts(text, uiLabel).map(p => (p.ui ? el('bdi', {class: 'afg-ui', dir: 'auto', text: '«' + p.label + '»'}) : p.t));
    }
    function save(update) {
      try { root.localStorage.setItem(key, JSON.stringify(st.progress)); } catch (e) { /* private mode */ }
      if (base === null) return Promise.resolve(null);
      return request('POST', base + '/progress', {update}).then(r => {
        if (r && r.progress) st.progress = r.progress;
        if (r && r.course) st.course = r.course;
        return r;
      }).catch(() => null);
    }
    function local(update) {
      const p = st.progress;
      if (update.op === 'step') p.current = {guide: update.guide, step: update.step};
      if (update.op === 'done') { p.done[update.guide] = new Date().toISOString(); if (p.current && p.current.guide === update.guide) p.current = null; }
      if (update.op === 'reset') delete p.done[update.guide];
      if (update.op === 'stop') p.current = null;
      if (update.op === 'lang') p.lang = update.lang;
      if (update.op === 'dismiss') p.dismissed = !!update.value;
      return save(update);
    }
    const myCourse = () => course(cat, opts.role, st.progress, st.states, opts.can);

    // DOM ------------------------------------------------------------------------------------------
    const fab = el('button', {type: 'button', class: 'afg-fab', 'aria-haspopup': 'dialog', 'aria-expanded': 'false',
      'data-afg': 'fab', on: {click: () => (st.open ? closePanel() : openPanel())}});
    const panel = el('section', {class: 'afg-panel', role: 'dialog', 'aria-modal': 'false', hidden: true, 'data-afg': 'panel'});
    const coach = el('div', {class: 'afg-coach', role: 'region', 'aria-live': 'polite', hidden: true, 'data-afg': 'coach'});
    const mount = opts.mount || doc.body;
    mount.append(fab, panel, coach);
    doc.addEventListener('keydown', e => {
      if (e.key === 'Escape' && st.open) closePanel();
      if (e.key === 'F1') { e.preventDefault(); openPanel(val(opts.route)); }
    });

    function setDir(node) { node.setAttribute('lang', lang()); node.setAttribute('dir', lang() === 'ar' ? 'rtl' : 'ltr'); }
    function renderFab() {
      const c = myCourse();
      // no course (signed out, or a role without one): no badge. replaceChildren would print a null as the word "null"
      fab.replaceChildren(el('span', {class: 'afg-fab-label', text: W('guide')}),
        ...(c.total ? [el('span', {class: 'afg-badge', 'aria-label': fmt(W('progress'), {d: c.done, t: c.total}), text: c.done + '/' + c.total})] : []));
      setDir(fab);
    }
    function guideRow(g, item) {
      const title = T('guide.' + g.id + '.title');
      const state = item ? (item.done ? (item.auto ? W('auto') : W('done')) : (item.locked ? W('locked') : '')) : '';
      return el('li', {class: 'afg-row' + (item && item.done ? ' is-done' : '') + (item && item.locked ? ' is-locked' : ''), 'data-guide-id': g.id},
        el('div', {class: 'afg-row-main'}, el('strong', {text: title}), el('small', {text: T('guide.' + g.id + '.why')}),
          el('small', {class: 'afg-meta', text: fmt(W('minutes'), {m: g.minutes}) + (state ? ' · ' + state : '')})),
        el('button', {type: 'button', class: 'afg-btn', disabled: item && item.locked ? true : null,
          'data-afg': 'start', on: {click: () => start(g.id)}, text: item && item.done ? W('again') : W('start')}));
    }
    function problemBlock(p, expanded) {
      const steps = [];
      for (let i = 1; T('problem.' + p.id + '.do.' + i); i++) steps.push(el('li', {}, rich(T('problem.' + p.id + '.do.' + i))));
      const body = el('div', {class: 'afg-problem-body'},
        el('p', {}, el('b', {text: W('why') + ': '}), rich(T('problem.' + p.id + '.why'))),
        el('p', {}, el('b', {text: W('todo') + ':'})), el('ol', {}, steps),
        T('problem.' + p.id + '.still') ? el('p', {}, el('b', {text: W('still') + ': '}), rich(T('problem.' + p.id + '.still'))) : null,
        el('div', {class: 'afg-actions'},
          p.guide ? el('button', {type: 'button', class: 'afg-btn', on: {click: () => start(p.guide)}, text: W('openGuide')}) : null,
          p.page && p.page !== val(opts.route) && opts.go ? el('button', {type: 'button', class: 'afg-btn afg-quiet', on: {click: () => { opts.go(p.page); closePanel(); }}, text: W('openPage')}) : null,
          opts.onReport ? el('button', {type: 'button', class: 'afg-btn afg-quiet', on: {click: () => report({problem: p.id})}, text: W('report')}) : null));
      const d = el('details', {class: 'afg-problem', 'data-problem-id': p.id, open: expanded || null,
        on: {toggle: e => { if (e.target.open) track('problem.open', {problem: p.id}); }}},
        el('summary', {}, rich(T('problem.' + p.id + '.see'))), body);
      return d;
    }
    function renderPanel(focusProblem) {
      const page = st.page || val(opts.route);
      const c = myCourse();
      const head = el('header', {class: 'afg-head'},
        el('h2', {id: 'afg-title', text: W('guide')}),
        langs.length > 1 ? el('button', {type: 'button', class: 'afg-btn afg-quiet', 'data-afg': 'lang', lang: lang() === 'ar' ? 'en' : 'ar',
          on: {click: () => switchLang(langs.find(l => l !== lang()))}, text: W('other')}) : null,
        el('button', {type: 'button', class: 'afg-x', 'aria-label': W('close'), on: {click: closePanel}, text: '×'}));
      const sections = [head];
      const pg = guidesForPage(cat, page, opts.can);
      const pr = problemsForPage(cat, page, opts.role);
      if (focusProblem) sections.push(el('div', {class: 'afg-sec'}, problemBlock(focusProblem, true)));
      sections.push(el('div', {class: 'afg-sec'}, el('h3', {text: W('page')}),
        pg.length ? el('ul', {class: 'afg-list'}, pg.map(g => guideRow(g, c.items.find(i => i.id === g.id)))) : el('p', {class: 'afg-meta', text: W('empty')})));
      if (pr.length) sections.push(el('div', {class: 'afg-sec'}, el('h3', {text: W('problems')}), pr.filter(p => p !== focusProblem).map(p => problemBlock(p, false))));
      if (c.total) {
        const bar = el('div', {class: 'afg-bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(c.total), 'aria-valuenow': String(c.done)},
          el('span', {pct: Math.round(100 * c.done / c.total)}));
        sections.push(el('div', {class: 'afg-sec', 'data-afg': 'course'}, el('h3', {text: T('role.' + opts.role + '.title') || W('path')}),
          el('p', {text: T('role.' + opts.role + '.intro')}), bar, el('p', {class: 'afg-meta', text: fmt(W('progress'), {d: c.done, t: c.total})}),
          el('ol', {class: 'afg-list'}, c.items.map(i => guideRow(guides[i.id], i)))));
      }
      if (opts.onReport) sections.push(el('div', {class: 'afg-sec'}, el('button', {type: 'button', class: 'afg-btn afg-quiet', 'data-afg': 'report', on: {click: () => report({})}, text: W('report')})));
      panel.replaceChildren(...kept(sections));
      panel.setAttribute('aria-labelledby', 'afg-title');
      setDir(panel);
    }
    function openPanel(page, problem) {
      st.page = page || val(opts.route);
      st.open = true;
      renderPanel(problem);
      panel.hidden = false;
      fab.setAttribute('aria-expanded', 'true');
      const first = panel.querySelector('button, summary');
      if (first) first.focus();
    }
    function closePanel() {
      st.open = false;
      panel.hidden = true;
      fab.setAttribute('aria-expanded', 'false');
      fab.focus();
    }
    function report(ctx) {
      closePanel();
      opts.onReport(Object.assign({page: val(opts.route), guide: st.coach ? st.coach.guide : null, lang: lang()}, ctx));
    }
    function switchLang(l) {
      local({op: 'lang', lang: l});
      track('guide.lang', {lang: l});
      renderFab();
      if (st.open) renderPanel();
      if (st.coach) renderCoach();
    }

    // coach -----------------------------------------------------------------------------------------
    function targetEl(id) {
      const t = (cat.targets || {})[id];
      if (!t) return null;
      const n = doc.querySelector(t.sel);
      return n && (n.offsetParent !== null || n.getClientRects().length) ? n : null;
    }
    const env = () => ({route: val(opts.route), events: st.events, present: id => !!targetEl(id),
      filled: id => { const n = targetEl(id); return !!(n && String(n.value || '').trim()); },
      dialog: id => !!doc.querySelector('[data-guide-dialog="' + id + '"]:not([hidden])')});
    function highlight(id) {
      if (st.highlighted) st.highlighted.classList.remove('afg-target');
      st.highlighted = null;
      const n = id && targetEl(id);
      if (n) {
        n.classList.add('afg-target');
        st.highlighted = n;
        if (n.scrollIntoView) n.scrollIntoView({block: 'nearest', inline: 'nearest'});
      }
    }
    function start(id, at) {
      if (!guides[id]) return;
      closePanelQuiet();
      st.coach = {guide: id, step: at || 0, since: Date.now()};
      st.paused = false;
      st.events.clear();
      if (!at) track('guide.start', {guide: id});
      local({op: 'step', guide: id, step: st.coach.step});
      renderCoach();
      tickLoop();
    }
    function closePanelQuiet() { st.open = false; panel.hidden = true; fab.setAttribute('aria-expanded', 'false'); }
    function move(delta) {
      const g = guides[st.coach.guide];
      const n = Math.max(0, Math.min(g.steps.length - 1, st.coach.step + delta));
      if (n === st.coach.step) return;
      track('guide.step', {guide: g.id, step: st.coach.step + 1, kind: g.steps[st.coach.step].k, seconds: Math.round((Date.now() - st.coach.since) / 1000)});
      st.coach.step = n;
      st.coach.since = Date.now();
      if (g.steps[n].k === 'done') finish(false);
      else local({op: 'step', guide: g.id, step: n});
      renderCoach();
    }
    function finish(close) {
      const g = guides[st.coach.guide];
      if (!st.progress.done[g.id]) { local({op: 'done', guide: g.id}); track('guide.done', {guide: g.id}); }
      renderFab();
      if (close) stop(true);
    }
    function stop(completed) {
      if (st.coach && !completed && guides[st.coach.guide].steps[st.coach.step].k !== 'done') {
        track('guide.abandon', {guide: st.coach.guide, step: st.coach.step + 1});
        local({op: 'stop'});
      }
      st.coach = null;
      highlight(null);
      coach.hidden = true;
      clearInterval(st.timer);
      st.timer = null;
    }
    function renderCoach() {
      if (st.paused && st.progress.current) {
        const id = st.progress.current.guide;
        coach.replaceChildren(el('p', {}, el('strong', {text: T('guide.' + id + '.title')})),
          el('div', {class: 'afg-actions'},
            el('button', {type: 'button', class: 'afg-btn', 'data-afg': 'resume', on: {click: () => start(id, st.progress.current.step)}, text: W('resume')}),
            el('button', {type: 'button', class: 'afg-btn afg-quiet', on: {click: () => { st.paused = false; local({op: 'stop'}); coach.hidden = true; }}, text: W('later')})));
        setDir(coach);
        coach.hidden = false;
        return;
      }
      if (!st.coach) return;
      const g = guides[st.coach.guide];
      const i = st.coach.step;
      const s = g.steps[i];
      const total = g.steps.length;
      const page = stepPage(cat, g, i);
      const away = page && page !== val(opts.route) && s.k !== 'go';
      const isDone = s.k === 'done';
      const u = untilOf(s);
      coach.replaceChildren(...kept([
        el('div', {class: 'afg-coach-head'}, el('strong', {text: T('guide.' + g.id + '.title')}),
          el('span', {class: 'afg-meta', 'data-afg': 'step', text: fmt(W('step'), {i: i + 1, n: total})}),
          el('button', {type: 'button', class: 'afg-x', 'aria-label': W('stop'), on: {click: () => stop(false)}, text: '×'})),
        el('div', {class: 'afg-bar', role: 'progressbar', 'aria-valuemin': '1', 'aria-valuemax': String(total), 'aria-valuenow': String(i + 1)},
          el('span', {pct: Math.round(100 * (i + 1) / total)})),
        el('p', {class: 'afg-say afg-' + s.k, 'data-afg': 'say'}, rich(T('guide.' + g.id + '.' + (i + 1)))),
        isDone && T('guide.' + g.id + '.ok') ? el('p', {class: 'afg-meta'}, el('b', {text: W('ok') + ': '}), rich(T('guide.' + g.id + '.ok'))) : null,
        isDone && T('guide.' + g.id + '.mistake') ? el('p', {class: 'afg-meta'}, el('b', {text: W('mistake') + ': '}), rich(T('guide.' + g.id + '.mistake'))) : null,
        away ? el('p', {class: 'afg-meta', text: W('notHere')}) : (u && !isDone ? el('p', {class: 'afg-meta', text: W('waiting')}) : null),
        el('div', {class: 'afg-actions'},
          i > 0 && !isDone ? el('button', {type: 'button', class: 'afg-btn afg-quiet', 'data-afg': 'back', on: {click: () => move(-1)}, text: W('back')}) : null,
          (away || s.k === 'go') && opts.go && page ? el('button', {type: 'button', class: 'afg-btn afg-quiet', 'data-afg': 'there', on: {click: () => opts.go(page)}, text: W('there')}) : null,
          isDone ? el('button', {type: 'button', class: 'afg-btn', 'data-afg': 'finish', on: {click: () => finish(true)}, text: W('finished')})
            : el('button', {type: 'button', class: 'afg-btn', 'data-afg': 'next', on: {click: () => move(1)}, text: W('next')}))]));
      setDir(coach);
      coach.hidden = false;
      highlight(away ? null : s.target);
    }
    function tick() {
      if (!st.coach) return;
      const g = guides[st.coach.guide];
      const s = g.steps[st.coach.step];
      const route = val(opts.route);
      if (s.k !== 'done' && untilMet(s, env())) move(1);
      else if (route !== st.lastRoute) renderCoach();
      else if (s.target && !st.highlighted && route === stepPage(cat, g, st.coach.step)) highlight(s.target);
      st.lastRoute = route;
    }
    function tickLoop() {
      clearInterval(st.timer);
      st.timer = setInterval(tick, opts.interval || 400);
    }
    root.addEventListener('hashchange', () => { if (st.coach) { tick(); renderCoach(); } });

    // public ---------------------------------------------------------------------------------------
    const ctl = {
      version: VERSION,
      open: openPanel, close: closePanel, start, stop: () => stop(false),
      signal(name) { st.events.add(name); tick(); },
      explain(code) {
        const p = problemFor(cat, code);
        if (!p) return false;
        track('problem.open', {problem: p.id, error: code});
        openPanel(p.page || val(opts.route), p);
        return true;
      },
      errorButton(code) {
        if (!problemFor(cat, code)) return null;
        return el('button', {type: 'button', class: 'afg-btn afg-quiet', 'data-afg': 'explain', on: {click: () => ctl.explain(code)}, text: lang() === 'ar' ? 'ماذا أفعل؟' : 'What do I do?'});
      },
      helpButton(page) {
        const b = el('button', {type: 'button', class: 'afg-help', 'aria-label': W('help'), title: W('help'), 'data-afg': 'help',
          on: {click: () => openPanel(page || val(opts.route))}, text: '?'});
        return b;
      },
      setStates(list) { st.states = list || []; renderFab(); if (st.open) renderPanel(); },
      setLang: switchLang,
      lang,
      progress: () => JSON.parse(JSON.stringify(st.progress)),
      course: myCourse,
      routeChanged() { if (st.open) renderPanel(); tick(); if (st.coach) renderCoach(); },
      ready: null
    };
    ctl.ready = (base === null ? Promise.resolve(null) : request('GET', base + '/state').catch(() => null)).then(r => {
      if (r && r.progress) st.progress = r.progress;
      if (r && Array.isArray(r.states)) st.states = r.states;
      renderFab();
      if (st.progress.current && guides[st.progress.current.guide]) { st.paused = true; renderCoach(); }
      return ctl;
    });
    renderFab();
    return ctl;
  }

  return {version: VERSION, init, parts, plain, untilOf, untilMet, course, guidesForPage, problemsForPage, problemFor, stepPage, WORDS};
});
