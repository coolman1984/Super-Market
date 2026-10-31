// One-command deploy of the Super Market cloud backup to the owner's Cloudflare account. Safe to run again.
//   Windows: double-click deploy.bat      Others: node deploy.mjs
// Login: `npx wrangler login` once (browser), or CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID in the environment.
import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));
const toml = join(here, 'wrangler.toml');
const DB_NAME = 'super-market-backup';
const BUCKET = 'super-market-backups';
const WRANGLER = 'wrangler@4.45.0';
const shell = process.platform === 'win32';

function wr(args, input) {
  const r = spawnSync('npx', ['--yes', WRANGLER, ...args], { cwd: here, encoding: 'utf8', input, shell });
  return { ok: r.status === 0, out: (r.stdout || '') + (r.stderr || '') };
}
function step(msg) { console.log('\n== ' + msg); }
function fail(msg, out) { console.error('\nFAILED: ' + msg + (out ? '\n' + out.trim().split('\n').slice(-12).join('\n') : '')); process.exit(1); }

step('1/5 Database (D1) "' + DB_NAME + '"');
let cfg = readFileSync(toml, 'utf8');
const m = cfg.match(/database_id\s*=\s*"([^"]+)"/);
if (!m || m[1].startsWith('REPLACE')) {
  let id = null;
  const list = wr(['d1', 'list', '--json']);
  if (list.ok) { try { id = (JSON.parse(list.out.slice(list.out.indexOf('['))).find((d) => d.name === DB_NAME) || {}).uuid || null; } catch { /* create below */ } }
  if (!id) {
    const c = wr(['d1', 'create', DB_NAME]);
    if (!c.ok) fail('Could not create the database. Are you logged in? (npx wrangler login)', c.out);
    id = (c.out.match(/database_id"?\s*[:=]\s*"([0-9a-f-]{36})"/i) || [])[1] || null;
    if (!id) { const l2 = wr(['d1', 'list', '--json']); try { id = JSON.parse(l2.out.slice(l2.out.indexOf('['))).find((d) => d.name === DB_NAME).uuid; } catch { /* below */ } }
  }
  if (!id) fail('The database was created but its id could not be read. Put it in wrangler.toml by hand (database_id).');
  cfg = cfg.replace(/database_id\s*=\s*"[^"]+"/, 'database_id = "' + id + '"');
  writeFileSync(toml, cfg);
  console.log('database id saved in wrangler.toml (not a secret)');
} else console.log('database already configured');

step('2/5 Storage (R2) "' + BUCKET + '"');
let r = wr(['r2', 'bucket', 'list']);
if (!r.ok) fail('Could not list R2 buckets. R2 must be enabled once in the Cloudflare dashboard (R2 > Purchase/Enable; the free plan is enough).', r.out);
if (!r.out.includes(BUCKET)) {
  r = wr(['r2', 'bucket', 'create', BUCKET]);
  if (!r.ok) fail('Could not create the R2 bucket', r.out);
} else console.log('bucket already exists');

step('3/5 Tables (schema.sql is safe to re-run)');
r = wr(['d1', 'execute', DB_NAME, '--remote', '--file=schema.sql']);
if (!r.ok) fail('Could not apply schema.sql', r.out);

step('4/5 Deploy the Worker');
r = wr(['deploy']);
if (!r.ok) fail('Deploy failed', r.out);
const url = (r.out.match(/https:\/\/[^\s]+\.workers\.dev/) || [''])[0];

step('5/5 Master key (made once, never shown, never replaced)');
// Replacing it would lock every connected PC out of its own copies, so an existing key is always kept.
r = wr(['secret', 'list']);
if (!r.ok) fail('Could not read the list of secret names', r.out);
if (r.out.includes('MASTER_KEY')) console.log('MASTER_KEY already set: kept as it is.');
else {
  r = wr(['secret', 'put', 'MASTER_KEY'], randomBytes(48).toString('base64url') + '\n');
  if (!r.ok) fail('Could not set MASTER_KEY');   // never print wrangler's output here: it might echo the value
  console.log('MASTER_KEY made and stored encrypted at Cloudflare. Nobody needs to know it.');
}

console.log('\nDone. Address: ' + (url || '(see the Cloudflare dashboard)'));
if (url && url !== 'https://super-market-backup.mflma2040.workers.dev')
  console.log('NOTE: the program looks for https://super-market-backup.mflma2040.workers.dev. Tell the developer this address so the next version uses it.');
