# Vendored from Apps-Factory packages/af-guide 0.1.4 - do not edit here.
# Update with: python scripts/vendor_guide.py <product repo> (from the Apps-Factory checkout)
"""af-guide: the factory's in-app guide engine (standard library only, one file, vendorable).

A product describes its guides as a *catalogue* (JSON, see README.md) plus one *text file per guide language*.
This module

  * checks the catalogue and the texts against the factory rules - `check(catalogue, texts, ...)` (HELP-07..10);
  * lints the Arabic (and English) wording against the «العربية الميسّرة» rules - `lint(texts, lang)` (HELP-11);
  * keeps each person's progress on the server - `blank()`, `apply(record, update, catalogue)`, `course(...)`,
    `state(...)`, and the `SQL` table plus `load()` / `save()` helpers for any DB-API connection (HELP-07, HELP-08);
  * prints an outline of every course for the reading pass - `outline(catalogue, texts, lang)`.

    python af_guide.py check guide/ [--ui ui-ar.json --ui ui-en.json] [--access access.json] [--errors errors.json]
                                    [--release]          # exit 1 on any error (lint warnings become errors)
    python af_guide.py lint guide/ar.json [--release]
    python af_guide.py outline guide/ [ar|en]

`guide/` holds catalogue.json and <lang>.json for every language in catalogue["languages"].
The browser half is af-guide.js (+ af-guide.css); both read the same catalogue and texts.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = '0.1.4'
FORMAT = 1

ID_RE = re.compile(r'^[a-z][a-z0-9]*(-[a-z0-9]+)*$')
DOTTED_RE = re.compile(r'^[a-z][a-z0-9_]*([.-][a-z0-9_]+)*$')
KINDS = {'go', 'click', 'type', 'choose', 'check', 'tip', 'warn', 'done'}
NEEDS_TARGET = {'click', 'type', 'choose', 'check'}
UNTIL = {'route', 'target', 'gone', 'filled', 'dialog', 'event'}
UI_REF = re.compile(r'\[\[([A-Za-z0-9_.\-]+)\]\]')
LANGS = {'ar', 'en'}
GUIDE_TEXTS = ('title', 'why', 'ok')
PROBLEM_TEXTS = ('see', 'why', 'do.1')
ROLE_TEXTS = ('title', 'intro')
_HERE = Path(__file__).resolve().parent
# In the package: style/ar-lexicon.json. Vendored into a product (scripts/vendor_guide.py): afguide_ar_lexicon.json
# next to server/afguide.py.
STYLE = next((p for p in (_HERE / 'style' / 'ar-lexicon.json', _HERE / 'afguide_ar_lexicon.json') if p.exists()),
             _HERE / 'style' / 'ar-lexicon.json')


class Finding:
    def __init__(self, level, code, where, message):
        self.level, self.code, self.where, self.message = level, code, where, message

    def __repr__(self):
        return f'{self.level.upper()} {self.code} [{self.where}] {self.message}'


# ---------------------------------------------------------------- catalogue checks (HELP-07..10)

def _pages(cat, access):
    pages = set(cat.get('pages') or [])
    if access and isinstance(access.get('pages'), dict):
        pages |= set(access['pages'])
    return pages


def _access_perms(access):
    if not access:
        return None
    return {p['id'] for g in access.get('groups', []) for p in g.get('permissions', [])}


def _profile_perms(access):
    if not access:
        return {}
    return {p['id']: set(p.get('perms', [])) for p in access.get('profiles', [])}


def _cycle(guides):
    """First guide id found on a requires-cycle, or None."""
    graph = {g['id']: list(g.get('requires', [])) for g in guides if isinstance(g.get('id'), str)}
    colour = {}

    def visit(n):
        colour[n] = 1
        for m in graph.get(n, []):
            if colour.get(m) == 1 or (colour.get(m) is None and m in graph and visit(m)):
                return True
        colour[n] = 2
        return False
    for n in graph:
        if colour.get(n) is None and visit(n):
            return n
    return None


def step_text_keys(guide):
    gid = guide['id']
    return [f'guide.{gid}.{i}' for i in range(1, len(guide.get('steps', [])) + 1)]


def required_keys(cat):
    keys = []
    for r in cat.get('roles', []):
        keys += [f"role.{r.get('id')}.{t}" for t in ROLE_TEXTS]
    for g in cat.get('guides', []):
        if isinstance(g.get('id'), str):
            keys += [f"guide.{g['id']}.{t}" for t in GUIDE_TEXTS] + step_text_keys(g)
    for p in cat.get('problems', []):
        keys += [f"problem.{p.get('id')}.{t}" for t in PROBLEM_TEXTS]
    return keys


def check(cat, texts, ui=None, access=None, error_codes=None, release=False):
    """All findings for one product. texts = {lang: {key: text}}, ui = {lang: {key: label}} (the product's UI
    dictionaries, optional), access = the af-access catalogue (optional), error_codes = every error code the server can
    return (optional). With release=True every style warning is an error (HELP-11)."""
    out = []

    def err(code, where, msg):
        out.append(Finding('error', code, where, msg))

    def warn(code, where, msg):
        out.append(Finding('warning', code, where, msg))

    if cat.get('format') != FORMAT:
        err('format', 'catalogue', f'format must be {FORMAT}')
    langs = cat.get('languages') or []
    if not langs or not set(langs) <= LANGS:
        err('languages', 'catalogue', f'languages must be a non-empty subset of {sorted(LANGS)}')
    if cat.get('defaultLang') not in langs:
        err('languages', 'catalogue', 'defaultLang must be one of languages')
    ui_langs = cat.get('uiLanguages') or langs
    pages = _pages(cat, access)
    perms = _access_perms(access)
    profile_perms = _profile_perms(access)
    states = set(cat.get('states') or [])
    targets = cat.get('targets') or {}
    for tid, t in targets.items():
        if not DOTTED_RE.match(tid):
            err('target-id', f'target {tid}', 'target ids are lower-case dotted words')
        if not isinstance(t, dict) or not isinstance(t.get('sel'), str) or not t['sel'].strip():
            err('target-shape', f'target {tid}', 'a target needs "sel" (a CSS selector, preferably [data-guide=...])')
        elif t.get('page') is not None and t['page'] not in pages:
            err('unknown-page', f'target {tid}', f"page {t['page']!r} is not a page of the product")

    guides = cat.get('guides') or []
    ids = {}
    for g in guides:
        gid = g.get('id')
        if not isinstance(gid, str) or not ID_RE.match(gid):
            err('guide-id', f'guide {gid}', 'guide ids are lower-case words joined by "-"')
            continue
        if gid in ids:
            err('duplicate', f'guide {gid}', 'guide id used twice')
        ids[gid] = g
    for g in guides:
        gid = g.get('id')
        if not isinstance(gid, str):
            continue
        where = f'guide {gid}'
        if perms is not None and g.get('perm') not in perms and g.get('perm') != '*':
            err('unknown-perm', where, f"perm {g.get('perm')!r} is not in the access catalogue")
        elif perms is None and not isinstance(g.get('perm'), str):
            err('unknown-perm', where, 'every guide names the permission it needs ("*" for everybody)')
        if not isinstance(g.get('minutes'), int) or not 1 <= g['minutes'] <= 15:
            err('minutes', where, 'minutes is a whole number 1..15 (split longer guides)')
        for r in g.get('requires', []):
            if r not in ids:
                err('unknown-guide', where, f'requires unknown guide {r!r}')
        done = g.get('done')
        if done is not None and (not isinstance(done, dict) or set(done) - {'state'} or done.get('state') not in states):
            err('unknown-state', where, 'done must be {"state": <one of catalogue states>}')
        steps = g.get('steps') or []
        if not steps:
            err('steps', where, 'a guide needs at least one step')
        if steps and steps[-1].get('k') != 'done':
            err('steps', where, 'the last step is a "done" step')
        page = None
        for i, s in enumerate(steps, 1):
            sw = f'{where} step {i}'
            k = s.get('k')
            if k not in KINDS:
                err('unknown-kind', sw, f'kind {k!r} (use {sorted(KINDS)})')
                continue
            if s.get('page') is not None:
                if s['page'] not in pages:
                    err('unknown-page', sw, f"page {s['page']!r} is not a page of the product")
                page = s['page']
            if k == 'go' and not s.get('page'):
                err('step-shape', sw, 'a "go" step names the page')
            if k in NEEDS_TARGET:
                if s.get('target') not in targets:
                    err('unknown-target', sw, f"target {s.get('target')!r} is not in catalogue targets")
                elif page and targets[s['target']].get('page') not in (None, page):
                    err('target-page', sw, f"target {s['target']!r} lives on {targets[s['target']].get('page')!r}, the step is on {page!r}")
            elif s.get('target') is not None and s['target'] not in targets:
                err('unknown-target', sw, f"target {s['target']!r} is not in catalogue targets")
            u = s.get('until')
            if u is not None:
                if not isinstance(u, dict) or len(u) != 1 or next(iter(u)) not in UNTIL:
                    err('until', sw, f'until is one of {sorted(UNTIL)}: {{"route": "page"}}, {{"target": "id"}} ...')
                else:
                    kind, val = next(iter(u.items()))
                    if kind == 'route' and val not in pages:
                        err('unknown-page', sw, f'until route {val!r} is not a page')
                    if kind in ('target', 'gone', 'filled') and val not in targets:
                        err('unknown-target', sw, f'until {kind} {val!r} is not in catalogue targets')
            elif k in NEEDS_TARGET:
                warn('no-auto-advance', sw, 'an action step without "until" waits for the person to press Next (HELP-08)')
    loop = _cycle([g for g in guides if isinstance(g.get('id'), str)])
    if loop:
        err('requires-cycle', f'guide {loop}', 'guides require each other in a circle')

    seen_roles = set()
    for r in cat.get('roles') or []:
        rid = r.get('id')
        where = f'role {rid}'
        if not isinstance(rid, str) or not ID_RE.match(rid) or rid in seen_roles:
            err('duplicate' if rid in seen_roles else 'role-id', where, 'role ids are unique lower-case words')
        seen_roles.add(rid)
        path = r.get('path') or []
        if not path:
            err('role-without-path', where, 'every role has an ordered path of guides (its course)')
        for i, gid in enumerate(path):
            if gid not in ids:
                err('unknown-guide', where, f'path names unknown guide {gid!r}')
                continue
            for req in ids[gid].get('requires', []):
                if req not in path:
                    err('path-order', where, f'{gid!r} requires {req!r}, which is not in this path')
                elif path.index(req) > i:
                    err('path-order', where, f'{gid!r} comes before {req!r}, which it requires')
        if access and r.get('profiles'):
            for prof in r['profiles']:
                if prof not in profile_perms:
                    err('unknown-profile', where, f'profile {prof!r} is not in the access catalogue')
                    continue
                for gid in path:
                    need = ids.get(gid, {}).get('perm')
                    if need and need != '*' and need not in profile_perms[prof]:
                        err('role-perm', where, f'profile {prof!r} cannot do guide {gid!r} (needs {need!r})')
    for gid in cat.get('checklist') or []:
        if gid not in ids:
            err('coverage-checklist', 'checklist', f'checklist item {gid!r} has no guide')

    problems = cat.get('problems') or []
    pids = set()
    explained = set()
    for p in problems:
        pid = p.get('id')
        where = f'problem {pid}'
        if not isinstance(pid, str) or not ID_RE.match(pid):
            err('problem-shape', where, 'problem ids are lower-case words joined by "-"')
            continue
        if pid in pids:
            err('duplicate', where, 'problem id used twice')
        pids.add(pid)
        if not isinstance(p.get('errors', []), list) or not isinstance(p.get('roles', []), list):
            err('problem-shape', where, 'errors and roles are lists')
        if not p.get('page') and not p.get('guide'):
            err('problem-without-link', where, 'a problem links to the page where it is fixed or to a guide')
        if p.get('page') and p['page'] not in pages:
            err('unknown-page', where, f"page {p['page']!r} is not a page of the product")
        if p.get('guide') and p['guide'] not in ids:
            err('unknown-guide', where, f"guide {p['guide']!r} is unknown")
        for rid in p.get('roles', []) if isinstance(p.get('roles', []), list) else []:
            if rid not in seen_roles:
                err('unknown-role', where, f'role {rid!r} is unknown')
        explained |= set(p.get('errors', []) if isinstance(p.get('errors', []), list) else [])
    if error_codes is not None:
        for code in sorted(set(error_codes) - explained):
            err('error-unexplained', f'error {code}', 'every error the server can return links to a problem entry (HELP-09)')
        for code in sorted(explained - set(error_codes)):
            warn('error-unknown', f'error {code}', 'a problem names an error code the server never returns')

    exempt = set(cat.get('unguided') or {})
    visited = {s.get('page') for g in guides for s in g.get('steps', [])} | {p.get('page') for p in problems}
    visited |= {t.get('page') for g in guides for s in g.get('steps', []) if s.get('target') in targets
                for t in [targets[s['target']]]}
    for pg in sorted(pages - visited - exempt):
        err('page-unguided', f'page {pg}', 'no guide or problem covers this page, so its "?" would be empty')

    for lang in langs:
        t = texts.get(lang)
        if not isinstance(t, dict):
            err('text-missing', f'{lang}.json', 'no text file for this guide language')
            continue
        for key in required_keys(cat):
            if not isinstance(t.get(key), str) or not t[key].strip():
                err('text-missing', f'{lang}.json', f'{key} is missing')
        for key, value in t.items():
            if not isinstance(value, str):
                err('text-missing', f'{lang}.json', f'{key} is not text')
                continue
            for ref in UI_REF.findall(value):
                for ul in ui_langs:
                    if ui is not None and ul in ui and ref not in ui[ul]:
                        err('ui-key-missing', f'{lang}.json {key}', f'[[{ref}]] is not in the {ul} UI dictionary')
        for f in lint(t, lang):
            out.append(Finding('error' if release else f.level, f.code, f.where, f.message))
    return out


def errors(cat, texts, **kw):
    return [f for f in check(cat, texts, **kw) if f.level == 'error']


# ---------------------------------------------------------------- style lint (HELP-11)

def lexicon():
    return json.loads(STYLE.read_text(encoding='utf-8'))


def _sentences(text):
    plain = UI_REF.sub('X', text)
    return [s for s in re.split(r'[.!?؟\n]+', plain) if s.strip()]


def lint(texts, lang, lex=None):
    """Warnings about wording. Arabic: «العربية الميسّرة» (style/ar-lexicon.json). English: plain short sentences."""
    lex = lex or lexicon()
    rules = lex['rules']
    out = []
    banned = [(re.compile(r'(?<![\w\u0600-\u06FF])' + re.escape(b['word']) + r'(?![\w\u0600-\u06FF])'), b)
              for b in lex['banned']] if lang == 'ar' else []
    max_words = rules['max_words_ar'] if lang == 'ar' else rules['max_words_en']
    for key, text in texts.items():
        if not isinstance(text, str) or key.startswith('_'):
            continue
        for s in _sentences(text):
            n = len(s.split())
            if n > max_words:
                out.append(Finding('warning', 'sentence-long', key, f'{n} words in one sentence (max {max_words}): split it'))
        if lang == 'ar':
            for rx, b in banned:
                if rx.search(UI_REF.sub(' ', text)):
                    out.append(Finding('warning', 'banned-word', key, f"«{b['word']}» → {b['use']}"))
            if re.search('[\u0660-\u0669\u06F0-\u06F9]', text):
                out.append(Finding('warning', 'eastern-digits', key, 'write numbers with 0-9 like the screens do'))
        if re.search(r'[«"]\s*\[\[[^\]]+\]\]\s*[»"]', text):
            out.append(Finding('warning', 'quoted-ui', key, 'do not quote [[...]]: the guide adds «» itself'))
        if re.match(r'^guide\.[a-z0-9-]+\.\d+$', key):
            joins = rules['joins_ar'] if lang == 'ar' else rules['joins_en']
            actions = len(UI_REF.findall(text))
            joined = sum(len(re.findall(r'(?<!\w)' + re.escape(j) + r'(?!\w)', text)) for j in joins)
            if actions > 1 and joined:
                out.append(Finding('warning', 'one-action', key, 'one action per step: split this step'))
    return out


# ---------------------------------------------------------------- progress per person (HELP-07, HELP-08)

SQL = """CREATE TABLE IF NOT EXISTS guide_progress (
    user_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def blank():
    return {'v': 1, 'done': {}, 'current': None, 'lang': None, 'dismissed': False}


def _clean(record):
    rec = blank()
    if isinstance(record, dict):
        if isinstance(record.get('done'), dict):
            rec['done'] = {k: v for k, v in record['done'].items() if isinstance(k, str) and isinstance(v, str)}
        cur = record.get('current')
        if isinstance(cur, dict) and isinstance(cur.get('guide'), str) and isinstance(cur.get('step'), int):
            rec['current'] = {'guide': cur['guide'], 'step': cur['step']}
        if record.get('lang') in LANGS:
            rec['lang'] = record['lang']
        rec['dismissed'] = bool(record.get('dismissed'))
    return rec


def apply(record, update, cat, when=None):
    """A new progress record after one update from the browser. Raises ValueError on anything malformed, so the
    server answers 400. Updates: {"op": "step", "guide": id, "step": n} | {"op": "done", "guide": id} |
    {"op": "reset", "guide": id} | {"op": "stop"} | {"op": "lang", "lang": "ar"|"en"} | {"op": "dismiss", "value": bool}"""
    rec = _clean(record)
    if not isinstance(update, dict) or set(update) - {'op', 'guide', 'step', 'lang', 'value'}:
        raise ValueError('bad update')
    guides = {g['id']: g for g in cat.get('guides', [])}
    op = update.get('op')
    gid = update.get('guide')
    if op in ('step', 'done', 'reset') and gid not in guides:
        raise ValueError('unknown guide')
    if op == 'step':
        n = update.get('step')
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(guides[gid]['steps']):
            raise ValueError('bad step')
        rec['current'] = {'guide': gid, 'step': n}
    elif op == 'done':
        rec['done'][gid] = when or now_iso()
        if rec['current'] and rec['current']['guide'] == gid:
            rec['current'] = None
    elif op == 'reset':
        rec['done'].pop(gid, None)
    elif op == 'stop':
        rec['current'] = None
    elif op == 'lang':
        if update.get('lang') not in set(cat.get('languages', [])):
            raise ValueError('bad language')
        rec['lang'] = update['lang']
    elif op == 'dismiss':
        rec['dismissed'] = bool(update.get('value'))
    else:
        raise ValueError('bad op')
    return rec


def course(cat, role_id, record, live_states=(), can=None):
    """The person's course: [{'id', 'done', 'auto', 'locked'}...], counts and the next guide to take.
    live_states: names from catalogue["states"] that are already true in the data (done without the guide).
    can(perm) -> bool hides guides the person may not do."""
    rec = _clean(record)
    role = next((r for r in cat.get('roles', []) if r['id'] == role_id), None)
    guides = {g['id']: g for g in cat.get('guides', [])}
    if role is None:
        return {'role': None, 'items': [], 'done': 0, 'total': 0, 'next': None}
    live = set(live_states)
    items = []
    for gid in role.get('path', []):
        g = guides.get(gid)
        if g is None or (can and g.get('perm') not in (None, '*') and not can(g['perm'])):
            continue
        auto = bool(g.get('done') and g['done'].get('state') in live)
        items.append({'id': gid, 'done': gid in rec['done'] or auto, 'auto': auto and gid not in rec['done'],
                      'minutes': g.get('minutes')})
    done_ids = {i['id'] for i in items if i['done']}
    for i in items:
        i['locked'] = any(r not in done_ids for r in guides[i['id']].get('requires', []) if r in {x['id'] for x in items})
    nxt = next((i['id'] for i in items if not i['done'] and not i['locked']), None)
    return {'role': role_id, 'items': items, 'done': len(done_ids), 'total': len(items), 'next': nxt}


def state(cat, role_id, record, live_states=(), can=None):
    """What GET /api/guide/state returns: the course, the saved record (for resume) and the live states."""
    return {'course': course(cat, role_id, record, live_states, can), 'progress': _clean(record),
            'states': sorted(set(live_states) & set(cat.get('states', [])))}


def load(conn, user_id):
    row = conn.execute('SELECT data FROM guide_progress WHERE user_id = ?', (str(user_id),)).fetchone()
    if not row:
        return blank()
    try:
        return _clean(json.loads(row[0]))
    except ValueError:
        return blank()


def save(conn, user_id, record):
    conn.execute('INSERT INTO guide_progress (user_id, data, updated_at) VALUES (?, ?, ?) '
                 'ON CONFLICT(user_id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at',
                 (str(user_id), json.dumps(_clean(record), ensure_ascii=False), now_iso()))


def problem_for(cat, error_code):
    """The problem entry that explains an error code (HELP-09), or None."""
    return next((p for p in cat.get('problems', []) if error_code in p.get('errors', [])), None)


# ---------------------------------------------------------------- outline and CLI

def outline(cat, texts, lang):
    t = texts.get(lang, {})
    guides = {g['id']: g for g in cat.get('guides', [])}
    lines = [f"# {cat.get('product')} guides ({lang})", '']
    for r in cat.get('roles', []):
        lines += [f"## {t.get('role.' + r['id'] + '.title', r['id'])}", t.get(f"role.{r['id']}.intro", ''), '']
        for n, gid in enumerate(r.get('path', []), 1):
            g = guides.get(gid, {'steps': []})
            lines.append(f"{n}. **{t.get('guide.' + gid + '.title', gid)}** ({g.get('minutes', '?')} min)")
            for i in range(1, len(g['steps']) + 1):
                lines.append(f"   {i}. {t.get(f'guide.{gid}.{i}', '')}")
        lines.append('')
    return '\n'.join(lines)


def load_dir(folder):
    folder = Path(folder)
    cat = json.loads((folder / 'catalogue.json').read_text(encoding='utf-8'))
    texts = {}
    for lang in cat.get('languages', []):
        f = folder / f'{lang}.json'
        if f.exists():
            texts[lang] = json.loads(f.read_text(encoding='utf-8'))
    return cat, texts


def _ui_files(paths):
    ui = {}
    for p in paths:
        m = re.search(r'(ar|en)', Path(p).stem)
        ui[m.group(1) if m else Path(p).stem] = json.loads(Path(p).read_text(encoding='utf-8'))
    return ui


def main(argv):
    args = list(argv[1:])
    release = '--release' in args
    args = [a for a in args if a != '--release']

    def opt(name):
        vals = []
        while name in args:
            i = args.index(name)
            vals.append(args[i + 1])
            del args[i:i + 2]
        return vals
    if len(args) >= 2 and args[0] == 'check':
        ui, acc, errs = opt('--ui'), opt('--access'), opt('--errors')
        cat, texts = load_dir(args[1])
        found = check(cat, texts, ui=_ui_files(ui) if ui else None,
                      access=json.loads(Path(acc[0]).read_text(encoding='utf-8')) if acc else None,
                      error_codes=json.loads(Path(errs[0]).read_text(encoding='utf-8')) if errs else None,
                      release=release)
        for f in found:
            print(f)
        bad = [f for f in found if f.level == 'error']
        print(f"{'FAIL' if bad else 'OK'}: {len(cat.get('guides', []))} guides, {len(cat.get('problems', []))} problems, "
              f'{len(bad)} errors, {len(found) - len(bad)} warnings')
        return 1 if bad else 0
    if len(args) == 2 and args[0] == 'lint':
        f = Path(args[1])
        found = lint(json.loads(f.read_text(encoding='utf-8')), 'en' if 'en' in f.stem else 'ar')
        for x in found:
            print(x)
        return 1 if (found and release) else 0
    if len(args) in (2, 3) and args[0] == 'outline':
        cat, texts = load_dir(args[1])
        print(outline(cat, texts, args[2] if len(args) == 3 else cat.get('defaultLang', 'ar')))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
