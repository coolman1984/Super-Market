import test from 'node:test';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { newEnv, call, admin, signed, buildToken, ENROLL, rec } from './helpers.js';
import { cleanup } from '../src/worker.js';

const install = () => randomBytes(16).toString('hex');
const enrollBody = async (o = {}) => JSON.stringify({ token: await buildToken(), install_id: install(), name: 'Test PC', ...o });
const post = async (env, path, body, ip = '198.51.100.7') => call(env, 'POST', path, { body, headers: { 'CF-Connecting-IP': ip } });
const json = (r) => r.json();

test('first run: a program with a valid build token gets its own pending store, device and recovery code', async () => {
  const env = newEnv();
  const r = await post(env, '/v1/enroll', await enrollBody());
  assert.equal(r.status, 200);
  const dev = await json(r);
  assert.equal(dev.role, 'shop');
  assert.equal(dev.status, 'pending');
  assert.match(dev.store_id, /^[0-9a-f]{32}$/);
  assert.match(dev.secret, /^[0-9a-f]{64}$/);
  assert.match(dev.recovery_code, /^[A-Z2-9]{5}(-[A-Z2-9]{5}){3}$/);
  // it can push at once and the admin sees a pending trial store
  const p = await signed(env, dev, 'POST', '/v1/push', { records: [rec('products', 1, 1)] });
  assert.equal(p.status, 200);
  const info = await json(await admin(env, 'GET', '/admin/stores/' + dev.store_id));
  assert.equal(info.meta.status, 'pending');
  assert.equal(info.devices.length, 1);
});

test('enrol is idempotent by install id: a lost answer never makes a second store', async () => {
  const env = newEnv();
  const body = await enrollBody();
  const a = await json(await post(env, '/v1/enroll', body));
  const b = await json(await post(env, '/v1/enroll', body));
  assert.deepEqual(a, b);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM stores').first()).n, 1);
});

test('enrol refuses: no token, forged token, wrong key, revoked build, bad install id', async () => {
  const env = newEnv();
  assert.equal((await post(env, '/v1/enroll', JSON.stringify({ install_id: install() }))).status, 403);
  assert.equal((await post(env, '/v1/enroll', await enrollBody({ token: 'v0.1.0.' + '0'.repeat(32) }))).status, 403);
  assert.equal((await post(env, '/v1/enroll', await enrollBody({ token: await buildToken('v0.1.0', 'another-enroll-key-0123456789abcdef') }))).status, 403);
  assert.equal((await post(env, '/v1/enroll', await enrollBody({ install_id: 'short' }))).status, 400);
  assert.equal((await post(env, '/v1/enroll', await enrollBody())).status, 200);
  assert.equal((await admin(env, 'POST', '/admin/builds/revoke', { build_id: 'v0.1.0' })).status, 200);
  assert.equal((await post(env, '/v1/enroll', await enrollBody())).status, 403, 'a revoked build cannot enrol new installs');
  assert.equal((await post(env, '/v1/enroll', await enrollBody({ token: await buildToken('v0.2.0') }))).status, 200, 'a newer build still can');
});

test('without ENROLL_KEY the service refuses enrolment (never opens)', async () => {
  const env = newEnv({ ENROLL_KEY: '' });
  assert.equal((await post(env, '/v1/enroll', await enrollBody())).status, 503);
});

test('rate limits: per address per hour, and a different address is not blocked', async () => {
  const env = newEnv();
  for (let i = 0; i < 6; i++) assert.equal((await post(env, '/v1/enroll', await enrollBody(), '203.0.113.1')).status, 200);
  assert.equal((await post(env, '/v1/enroll', await enrollBody(), '203.0.113.1')).status, 429);
  assert.equal((await post(env, '/v1/enroll', await enrollBody(), '203.0.113.2')).status, 200);
});

test('a pending store has a daily write budget; an active one a larger budget; a suspended one is cut off', async () => {
  const env = newEnv();
  const dev = await json(await post(env, '/v1/enroll', await enrollBody()));
  let r;
  let sent = 0, n = 0;
  while (sent < 20000) {
    r = await signed(env, dev, 'POST', '/v1/push', { records: Array.from({ length: 200 }, (_, i) => rec('t', n * 200 + i + 1, 1)) });
    assert.equal(r.status, 200, 'under the budget');
    sent += 200; n++;
  }
  r = await signed(env, dev, 'POST', '/v1/push', { records: [rec('t', 999999, 1)] });
  assert.equal(r.status, 429);
  assert.equal((await admin(env, 'POST', '/admin/stores/status', { store_id: dev.store_id, status: 'active' })).status, 200);
  assert.equal((await signed(env, dev, 'POST', '/v1/push', { records: [rec('t', 999999, 1)] })).status, 200);
  assert.equal((await admin(env, 'POST', '/admin/stores/status', { store_id: dev.store_id, status: 'suspended' })).status, 200);
  assert.equal((await signed(env, dev, 'POST', '/v1/push', { records: [rec('t', 999998, 1)] })).status, 403);
  assert.equal((await admin(env, 'POST', '/admin/stores/status', { store_id: 'f'.repeat(32), status: 'active' })).status, 404);
});

test('recover: the recovery code gives a replacement PC the same store and cuts the old PC off', async () => {
  const env = newEnv();
  const old = await json(await post(env, '/v1/enroll', await enrollBody()));
  await signed(env, old, 'POST', '/v1/push', { records: [rec('products', 1, 1, { id: 1, name: 'x' })] });
  const body = await enrollBody({ code: old.recovery_code.toLowerCase(), install_id: install() });
  const fresh = await json(await post(env, '/v1/recover', body));
  assert.equal(fresh.store_id, old.store_id);
  assert.notEqual(fresh.device_id, old.device_id);
  assert.equal(fresh.recovery_code, old.recovery_code);
  assert.equal((await signed(env, old, 'GET', '/v1/summary')).status, 401, 'the old PC is cut off');
  const pulled = await json(await signed(env, fresh, 'GET', '/v1/pull?after=0&limit=10'));
  assert.equal(pulled.records.length, 1);
  assert.deepEqual(await json(await post(env, '/v1/recover', body)), fresh, 'a retry after a lost answer');
});

test('recover: wrong code refused and counted per address; token still needed; installs of another shop refused', async () => {
  const env = newEnv();
  const a = await json(await post(env, '/v1/enroll', await enrollBody()));
  const b = await json(await post(env, '/v1/enroll', await enrollBody()));
  assert.equal((await post(env, '/v1/recover', await enrollBody({ code: 'AAAAA-BBBBB-CCCCC-DDDDD' }))).status, 403);
  assert.equal((await post(env, '/v1/recover', JSON.stringify({ code: a.recovery_code, install_id: install() }))).status, 403, 'no build token');
  const mine = install();
  assert.equal((await post(env, '/v1/recover', await enrollBody({ code: a.recovery_code, install_id: mine }))).status, 200);
  assert.equal((await post(env, '/v1/recover', await enrollBody({ code: b.recovery_code, install_id: mine }))).status, 409);
  for (let i = 0; i < 10; i++) await post(env, '/v1/recover', await enrollBody({ code: 'AAAAA-BBBBB-CCCCC-DDDDD' }), '192.0.2.9');
  assert.equal((await post(env, '/v1/recover', await enrollBody({ code: a.recovery_code }), '192.0.2.9')).status, 429);
});

test('admin fallback keeps working: pairing, and the support can read a store\'s recovery code', async () => {
  const env = newEnv();
  const dev = await json(await post(env, '/v1/enroll', await enrollBody()));
  const r = await json(await admin(env, 'POST', '/admin/recovery', { store_id: dev.store_id }));
  assert.equal(r.recovery_code, dev.recovery_code);
  assert.equal((await admin(env, 'POST', '/admin/recovery', { store_id: dev.store_id }, 'wrong-key-wrong-key-wrong')).status, 401);
  const hand = await json(await admin(env, 'POST', '/admin/stores', { name: 'by hand' }));
  assert.equal((await admin(env, 'POST', '/admin/recovery', { store_id: hand.store_id })).status, 404);
  const c = await json(await admin(env, 'POST', '/admin/pairing', { store_id: dev.store_id, role: 'mobile' }));
  const paired = await post(env, '/v1/pair', JSON.stringify({ store_id: dev.store_id, code: c.code, name: 'phone' }));
  assert.equal(paired.status, 200);
});

test('cleanup forgets an enrolled store that never sent anything for 14 days, keeps one that did', async () => {
  const env = newEnv();
  const empty = await json(await post(env, '/v1/enroll', await enrollBody()));
  const used = await json(await post(env, '/v1/enroll', await enrollBody()));
  await signed(env, used, 'POST', '/v1/push', { records: [rec('t', 1, 1)] });
  await env.DB.prepare('UPDATE stores SET created_at = 1').run();
  await cleanup(env);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM stores WHERE id = ?').bind(empty.store_id).first()).n, 0);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM devices WHERE store_id = ?').bind(empty.store_id).first()).n, 0);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM stores WHERE id = ?').bind(used.store_id).first()).n, 1);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM store_meta').first()).n, 1);
});
