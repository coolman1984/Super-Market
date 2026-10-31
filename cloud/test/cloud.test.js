// The cloud backup Worker: each PC connects itself once, gets its own space, and only its own copies.
import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes, createHash } from 'node:crypto';
import worker, { hmacHex, pcSecret, KEEP } from '../src/worker.js';
import { makeEnv } from '../dev/server.js';

const MASTER = 'master-key-for-tests-0123456789abcdef-0123';
const sha = (b) => createHash('sha256').update(b).digest('hex');
const EMPTY = sha(Buffer.alloc(0));
const newEnv = () => makeEnv({ MASTER_KEY: MASTER });
const call = (env, method, path, { body, headers } = {}) =>
  worker.fetch(new Request('http://cloud.test' + path, { method, headers: { 'CF-Connecting-IP': '9.9.9.9', ...(headers || {}) }, body }), env);
const install = () => randomBytes(16).toString('hex');

async function register(env, id = install(), ip = '203.0.113.1') {
  const r = await call(env, 'POST', '/pc/register', { body: JSON.stringify({ installId: id, label: 'Shop' }), headers: { 'CF-Connecting-IP': ip } });
  const j = await r.json();
  assert.equal(r.status, 200, JSON.stringify(j));
  return j;
}
async function signed(env, pc, method, path, body, over = {}) {
  const bytes = body || Buffer.alloc(0);
  const t = String(over.time ?? Math.floor(Date.now() / 1000)), nonce = over.nonce ?? randomBytes(16).toString('hex');
  const bodySha = over.bodySha ?? sha(bytes);
  const sig = await hmacHex(over.secret ?? pc.secret, [method, path, t, nonce, bodySha, pc.pcId].join('\n'));
  const headers = { 'X-SM-PC': pc.pcId, 'X-SM-Time': t, 'X-SM-Nonce': nonce, 'X-SM-Sig': sig, 'X-SM-Body': bodySha, ...(over.headers || {}) };
  if (method === 'PUT') Object.assign(headers, { 'Content-Length': String(bytes.length), 'X-SM-Db-Sha256': 'a'.repeat(64), 'X-SM-Schema': '7', 'X-SM-App': '1.0' });
  return call(env, method, path, { body: method === 'GET' ? undefined : bytes, headers });
}

test('a PC connects itself once; the same PC asking again gets the same space, a new PC its own', async () => {
  const env = newEnv();
  const id = install();
  const a = await register(env, id), again = await register(env, id), b = await register(env);
  assert.equal(a.pcId, again.pcId);
  assert.equal(a.secret, again.secret);
  assert.notEqual(a.pcId, b.pcId);
  assert.equal(a.secret, await pcSecret(MASTER, a.pcId));
  const row = await env.DB.prepare('SELECT * FROM pcs WHERE id = ?').bind(a.pcId).first();
  assert.notEqual(row.install_hash, id, 'the install id itself is never stored');
  assert.equal(JSON.stringify(row).includes(a.secret), false, 'the secret is never stored');
});

test('without a master key nothing connects and nothing is accepted', async () => {
  const env = makeEnv({ MASTER_KEY: 'short' });
  const r = await call(env, 'POST', '/pc/register', { body: JSON.stringify({ installId: install() }) });
  assert.equal(r.status, 503);
});

test('upload, list and download one copy; the bytes come back the same', async () => {
  const env = newEnv();
  const pc = await register(env);
  const copy = randomBytes(5000);
  let r = await signed(env, pc, 'PUT', '/pc/copies/hourly-20261031-140000', copy);
  assert.equal(r.status, 200, await r.clone().text());
  r = await signed(env, pc, 'GET', '/pc/copies');
  const list = await r.json();
  assert.equal(list.copies.length, 1);
  assert.equal(list.copies[0].sha256, sha(copy));
  r = await signed(env, pc, 'GET', '/pc/copies/hourly-20261031-140000');
  assert.equal(r.status, 200);
  assert.equal(sha(Buffer.from(await r.arrayBuffer())), sha(copy));
  assert.equal(r.headers.get('X-SM-Sha256'), sha(copy));
});

test('each PC sees only its own copies', async () => {
  const env = newEnv();
  const a = await register(env), b = await register(env);
  await signed(env, a, 'PUT', '/pc/copies/nightly-20261031-020000', randomBytes(100));
  const r = await signed(env, b, 'GET', '/pc/copies');
  assert.deepEqual((await r.json()).copies, []);
  const d = await signed(env, b, 'GET', '/pc/copies/nightly-20261031-020000');
  assert.equal(d.status, 404);
  // B cannot pretend to be A: A's id with B's secret does not sign
  const fake = await signed(env, { pcId: a.pcId, secret: b.secret }, 'GET', '/pc/copies');
  assert.equal(fake.status, 401);
});

test('a changed body, a replayed request, a wrong clock and an unknown PC are refused', async () => {
  const env = newEnv();
  const pc = await register(env);
  const copy = randomBytes(300);
  let r = await signed(env, pc, 'PUT', '/pc/copies/hourly-20261031-150000', copy, { bodySha: sha(randomBytes(300)) });
  assert.equal(r.status, 400, 'R2 refuses bytes that do not match the signed hash');
  assert.equal((await signed(env, pc, 'GET', '/pc/copies').then((x) => x.json())).copies.length, 0, 'nothing was kept');
  const nonce = randomBytes(16).toString('hex');
  assert.equal((await signed(env, pc, 'GET', '/pc/copies', null, { nonce })).status, 200);
  assert.equal((await signed(env, pc, 'GET', '/pc/copies', null, { nonce })).status, 401);
  r = await signed(env, pc, 'GET', '/pc/copies', null, { time: Math.floor(Date.now() / 1000) - 3600 });
  assert.equal(r.status, 401);
  assert.equal((await r.json()).error, 'clock');
  const ghost = { pcId: install(), secret: '' };
  ghost.secret = await pcSecret(MASTER, ghost.pcId);
  assert.equal((await signed(env, ghost, 'GET', '/pc/copies')).status, 401);
});

test('sending the same copy again after a lost answer is not a second copy; another body under the same name is refused', async () => {
  const env = newEnv();
  const pc = await register(env);
  const copy = randomBytes(400);
  assert.equal((await signed(env, pc, 'PUT', '/pc/copies/hourly-20261031-160000', copy)).status, 200);
  const again = await signed(env, pc, 'PUT', '/pc/copies/hourly-20261031-160000', copy);
  assert.equal(again.status, 200);
  assert.equal((await again.json()).replayed, true);
  assert.equal((await signed(env, pc, 'PUT', '/pc/copies/hourly-20261031-160000', randomBytes(400))).status, 409);
});

test('only the last copies are kept: 48 hourly and 30 nightly', async () => {
  const env = newEnv();
  const pc = await register(env);
  for (let i = 0; i < KEEP.hourly + 3; i++) {
    const name = 'hourly-202610' + String(10 + Math.floor(i / 24)).padStart(2, '0') + '-' + String(i % 24).padStart(2, '0') + '0000';
    assert.equal((await signed(env, pc, 'PUT', '/pc/copies/' + name, randomBytes(50))).status, 200);
  }
  await signed(env, pc, 'PUT', '/pc/copies/nightly-20261001-020000', randomBytes(50));
  const list = (await signed(env, pc, 'GET', '/pc/copies').then((x) => x.json())).copies;
  assert.equal(list.filter((c) => c.kind === 'hourly').length, KEEP.hourly);
  assert.equal(list.filter((c) => c.kind === 'nightly').length, 1, 'the nightly copy is not pruned by hourly ones');
  assert.equal(list.some((c) => c.name === 'hourly-20261010-000000'), false, 'the oldest hourly went');
  assert.equal(env.BUCKET.objects.size, KEEP.hourly + 1, 'R2 holds exactly the listed copies');
});

test('bad names and too many new PCs from one address are refused', async () => {
  const env = newEnv();
  const pc = await register(env);
  assert.equal((await signed(env, pc, 'PUT', '/pc/copies/..%2Fother', randomBytes(10))).status, 404);
  assert.equal((await signed(env, pc, 'PUT', '/pc/copies/daily-20261031-000000', randomBytes(10))).status, 404);
  for (let i = 0; i < 5; i++) await register(env, install(), '198.51.100.7');
  const r = await call(env, 'POST', '/pc/register', { body: JSON.stringify({ installId: install() }), headers: { 'CF-Connecting-IP': '198.51.100.7' } });
  assert.equal(r.status, 429);
});

test('the daily cleanup never removes a quiet PC or its copies', async () => {
  const env = newEnv();
  const pc = await register(env);
  await signed(env, pc, 'PUT', '/pc/copies/nightly-20261031-020000', randomBytes(50));
  await env.DB.prepare('UPDATE pcs SET last_seen = 0').run();
  await worker.scheduled({}, env);
  assert.equal(env.BUCKET.objects.size, 1);
  assert.ok(await env.DB.prepare('SELECT id FROM pcs WHERE id = ?').bind(pc.pcId).first());
});
