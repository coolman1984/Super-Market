// One-command deploy of the cloud copy (Worker + D1 database) to a Cloudflare account. Safe to run again.
//   Needs: Node 20+ and a Cloudflare account (the free plan is enough to start).
//   Windows: double-click deploy.bat      Others: node deploy.mjs
// Login: `npx wrangler login` once (browser), or CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID in the environment.
// Keys: MASTER_KEY (derives every device secret) and ADMIN_KEY (the owner's key for new shops/devices) are created
//   ONCE, stored encrypted at Cloudflare and never printed. A re-run never replaces an existing key (that would
//   lock every connected device out). A new ADMIN_KEY is saved to cloud/.admin-key (git-ignored) on this PC only.
import { spawnSync } from 'node:child_process';
import { chmodSync, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const toml = join(here, 'wrangler.toml');
const DB_NAME = 'super-market-sync';
const WRANGLER = 'wrangler@4.45.0';
const shell = process.platform === 'win32';

function wr(args, input) {
  const r = spawnSync('npx', ['--yes', WRANGLER, ...args], { cwd: here, encoding: 'utf8', input, shell });
  return { ok: r.status === 0, out: (r.stdout || '') + (r.stderr || '') };
}
function step(msg) { console.log('\n== ' + msg); }
function fail(msg, out) { console.error('\nFAILED: ' + msg + (out ? '\n' + out.trim().split('\n').slice(-12).join('\n') : '')); process.exit(1); }
const jsonOf = (text) => { const i = text.search(/[\[{]/); return JSON.parse(i < 0 ? '[]' : text.slice(i)); };

step('1/5 Database (D1) "' + DB_NAME + '"');
let cfg = readFileSync(toml, 'utf8');
const m = cfg.match(/database_id\s*=\s*"([^"]+)"/);
if (!m || m[1].startsWith('REPLACE')) {
  let id = null;
  const list = wr(['d1', 'list', '--json']);
  if (list.ok) { try { id = (jsonOf(list.out).find((d) => d.name === DB_NAME) || {}).uuid || null; } catch { /* create below */ } }
  if (!id) {
    const c = wr(['d1', 'create', DB_NAME]);
    if (!c.ok) fail('Could not create the database. Are you logged in? (npx wrangler login)', c.out);
    id = (c.out.match(/database_id"?\s*[:=]\s*"([0-9a-f-]{36})"/i) || [])[1] || null;
    if (!id) { try { id = jsonOf(wr(['d1', 'list', '--json']).out).find((d) => d.name === DB_NAME).uuid; } catch { /* below */ } }
  }
  if (!id) fail('The database was created but its id could not be read. Put it in wrangler.toml by hand (database_id).');
  cfg = cfg.replace(/database_id\s*=\s*"[^"]+"/, 'database_id = "' + id + '"');
  writeFileSync(toml, cfg);
  console.log('database id saved in wrangler.toml (an id, not a secret)');
} else console.log('database already configured');

step('2/5 Tables (schema.sql is safe to re-run)');
let r = wr(['d1', 'execute', DB_NAME, '--remote', '--file=schema.sql']);
if (!r.ok) fail('Could not apply schema.sql', r.out);

step('3/5 Deploy the Worker');
r = wr(['deploy']);
if (!r.ok) fail('Deploy failed', r.out);
const url = (r.out.match(/https:\/\/[^\s]+\.workers\.dev/) || [''])[0];

step('4/5 Keys (created once, never replaced, never printed)');
let existing = new Set();
const sl = wr(['secret', 'list', '--format', 'json']);
if (sl.ok) { try { existing = new Set(jsonOf(sl.out).map((s) => s.name)); } catch { /* treated as none; wrangler refuses nothing here, so we ask below */ } }
else fail('Could not read the list of keys at Cloudflare', sl.out);
const keyFile = join(here, '.admin-key');
for (const name of ['MASTER_KEY', 'ADMIN_KEY']) {
  if (existing.has(name)) { console.log(name + ': already set, left as it is'); continue; }
  if (name === 'ADMIN_KEY' && process.env.CI && !process.env.ADMIN_KEY) fail('On a CI runner the new ADMIN_KEY would be lost with the runner. Add a repository secret SM_ADMIN_KEY (a long random value you keep) and run again.');
  const value = process.env[name] || randomBytes(32).toString('hex');
  r = wr(['secret', 'put', name], value + '\n');
  if (!r.ok) fail('Could not set ' + name);   // wrangler output is not printed: it might echo the value
  console.log(name + ': created');
  if (name === 'ADMIN_KEY' && !process.env.ADMIN_KEY) {
    writeFileSync(keyFile, value + '\n');
    try { chmodSync(keyFile, 0o600); } catch { /* Windows */ }
    console.log('ADMIN_KEY saved to cloud/.admin-key on this PC only. Keep a copy somewhere safe: it cannot be shown again.');
  }
}

step('5/5 Check');
if (url) {
  try {
    const res = await fetch(url + '/');
    const body = await res.json();
    console.log(body.ok ? 'The cloud service answers: ' + body.service + ' ' + body.version : 'Unexpected answer from the service');
  } catch (e) { console.log('Could not reach ' + url + ' yet (' + e.message + '). Try again in a minute.'); }
}
console.log('\nDONE. Cloud address: ' + (url || '(see the line "https://...workers.dev" above)'));
console.log('Next: python tools/syncctl.py admin new-store --url <address> --name "Shop"   (see docs/CLOUD_SYNC.md)');
