import test from 'node:test';
import assert from 'node:assert/strict';
import { newEnv, call, admin, signed, makeStore, pairDevice, rec, MASTER } from './helpers.js';
import worker, { cleanup, hmacHex, sha256Hex } from '../src/worker.js';

async function setup() {
  const env = newEnv();
  const store = await makeStore(env);
  const shop = await pairDevice(env, store, 'shop');
  const phone = await pairDevice(env, store, 'mobile');
  return { env, store, shop, phone };
}
const json = (r) => r.json();

test('health answers without secrets', async () => {
  const env = newEnv();
  const r = await call(env, 'GET', '/');
  assert.equal(r.status, 200);
  const j = await json(r);
  assert.equal(j.ok, true);
  assert.ok(!JSON.stringify(j).includes(MASTER));
});

test('admin calls need the admin key', async () => {
  const env = newEnv();
  assert.equal((await admin(env, 'POST', '/admin/stores', { name: 'x' }, 'wrong-key-wrong-key-wrong')).status, 401);
  assert.equal((await admin(env, 'POST', '/admin/stores', { name: 'x' }, '')).status, 401);
  assert.equal((await admin(env, 'POST', '/admin/stores', { name: 'x' })).status, 200);
});

test('without ADMIN_KEY or MASTER_KEY the service refuses (never opens)', async () => {
  const noAdmin = newEnv({ ADMIN_KEY: '' });
  assert.equal((await admin(noAdmin, 'POST', '/admin/stores', { name: 'x' }, '')).status, 503);
  const noMaster = newEnv({ MASTER_KEY: 'short' });
  const store = await makeStore(noMaster);
  const c = await (await admin(noMaster, 'POST', '/admin/pairing', { store_id: store, role: 'shop' })).json();
  const r = await call(noMaster, 'POST', '/v1/pair', { body: JSON.stringify({ store_id: store, code: c.code, name: 'a' }) });
  assert.equal(r.status, 503);
});

test('pairing: one-time, expires, wrong code refused, store must match', async () => {
  const env = newEnv();
  const store = await makeStore(env);
  const other = await makeStore(env, 'Other');
  const c = await (await admin(env, 'POST', '/admin/pairing', { store_id: store, role: 'mobile' })).json();
  assert.match(c.code, /^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$/);
  const body = (o) => JSON.stringify({ store_id: store, code: c.code, name: 'Phone', ...o });
  assert.equal((await call(env, 'POST', '/v1/pair', { body: body({ store_id: other }) })).status, 403);
  assert.equal((await call(env, 'POST', '/v1/pair', { body: body({ code: 'AAAA-BBBB-CCCC' }) })).status, 403);
  const ok = await call(env, 'POST', '/v1/pair', { body: body({ code: c.code.toLowerCase().replace(/-/g, ' ') }) });
  assert.equal(ok.status, 200);
  const dev = await json(ok);
  assert.equal(dev.role, 'mobile');
  assert.match(dev.secret, /^[0-9a-f]{64}$/);
  assert.equal((await call(env, 'POST', '/v1/pair', { body: body() })).status, 403, 'a code works once');
  const old = await (await admin(env, 'POST', '/admin/pairing', { store_id: store, role: 'shop', ttl_min: 1 })).json();
  await env.DB.prepare('UPDATE pairings SET expires_at = 1').run();
  assert.equal((await call(env, 'POST', '/v1/pair', { body: JSON.stringify({ store_id: store, code: old.code, name: 'x' }) })).status, 403, 'expired');
});

test('pairing is rate limited per address', async () => {
  const env = newEnv();
  const store = await makeStore(env);
  let last = 0;
  for (let i = 0; i < 25; i++) last = (await call(env, 'POST', '/v1/pair', { body: JSON.stringify({ store_id: store, code: 'AAAA-BBBB-CCCC', name: 'x' }), headers: { 'CF-Connecting-IP': '1.2.3.4' } })).status;
  assert.equal(last, 429);
});

test('signed requests: good, bad signature, other secret, clock, replay, unsigned', async () => {
  const { env, shop } = await setup();
  assert.equal((await signed(env, shop, 'GET', '/v1/summary')).status, 200);
  assert.equal((await signed(env, shop, 'GET', '/v1/summary', undefined, { sig: 'a'.repeat(64) })).status, 401);
  assert.equal((await signed(env, shop, 'GET', '/v1/summary', undefined, { secret: 'b'.repeat(64) })).status, 401);
  const late = await signed(env, shop, 'GET', '/v1/summary', undefined, { time: Math.floor(Date.now() / 1000) - 3600 });
  assert.equal(late.status, 401);
  assert.ok((await json(late)).server_time > 0, 'the answer tells the device the real time');
  assert.equal((await call(env, 'GET', '/v1/summary')).status, 401);
  const nonce = 'n'.repeat(24);
  assert.equal((await signed(env, shop, 'POST', '/v1/push', { records: [rec('a', 1, 1)] }, { nonce })).status, 200);
  assert.equal((await signed(env, shop, 'POST', '/v1/push', { records: [rec('a', 1, 1)] }, { nonce })).status, 401, 'replayed request');
});

test('a signature for one path or body does not work for another', async () => {
  const { env, shop } = await setup();
  const time = String(Math.floor(Date.now() / 1000));
  const body = JSON.stringify({ records: [rec('a', 1, 1)] });
  const sig = await hmacHex(shop.secret, ['POST', '/v1/push', time, 'x'.repeat(20), await sha256Hex(new TextEncoder().encode(body)), shop.store_id, shop.device_id].join('\n'));
  const h = { 'X-SM-Store': shop.store_id, 'X-SM-Device': shop.device_id, 'X-SM-Time': time, 'X-SM-Nonce': 'x'.repeat(20), 'X-SM-Sig': sig };
  assert.equal((await call(env, 'POST', '/v1/push', { body: JSON.stringify({ records: [rec('a', 2, 1)] }), headers: h })).status, 401, 'other body');
  assert.equal((await call(env, 'POST', '/v1/commands/ack', { body, headers: h })).status, 401, 'other path');
  assert.equal((await call(env, 'POST', '/v1/push', { body, headers: h })).status, 200, 'the real one');
});

test('a device of one store cannot read another store', async () => {
  const { env, shop } = await setup();
  const store2 = await makeStore(env, 'Second');
  const shop2 = await pairDevice(env, store2, 'shop', '203.0.113.6');
  await signed(env, shop, 'POST', '/v1/push', { records: [rec('sales', 1, 1, { id: 1, total: 100 })] });
  const r = await json(await signed(env, shop2, 'GET', '/v1/pull'));
  assert.equal(r.records.length, 0);
  const forged = { ...shop2, store_id: shop.store_id };
  assert.equal((await signed(env, forged, 'GET', '/v1/pull')).status, 401);
});

test('push stores rows, an older version never overwrites a newer one, tombstones', async () => {
  const { env, shop, phone } = await setup();
  let r = await json(await signed(env, shop, 'POST', '/v1/push', { records: [rec('sales', 1, 5, { id: 1, total: 500 }), rec('sales', 2, 6, { id: 2, total: 200 })] }));
  assert.deepEqual(r, { accepted: 2, stale: 0 });
  r = await json(await signed(env, shop, 'POST', '/v1/push', { records: [rec('sales', 1, 4, { id: 1, total: 111 })] }));
  assert.deepEqual(r, { accepted: 0, stale: 1 }, 'older version refused');
  r = await json(await signed(env, shop, 'POST', '/v1/push', { records: [rec('sales', 1, 5, { id: 1, total: 500 })] }));
  assert.equal(r.stale, 1, 'same version again changes nothing');
  r = await json(await signed(env, shop, 'POST', '/v1/push', { records: [rec('sales', 1, 9, { id: 1, total: 777 })] }));
  assert.equal(r.accepted, 1);
  let p = await json(await signed(env, phone, 'GET', '/v1/pull'));
  const byRid = Object.fromEntries(p.records.map((x) => [x.r, x]));
  assert.equal(byRid[1].d.total, 777);
  assert.equal(byRid[2].d.total, 200);
  await signed(env, shop, 'POST', '/v1/push', { records: [{ t: 'sales', r: 2, v: 10, d: null }] });
  p = await json(await signed(env, phone, 'GET', '/v1/pull?after=0'));
  assert.equal(p.records.find((x) => x.r === 2).d, null, 'tombstone is delivered');
  const s = await json(await signed(env, phone, 'GET', '/v1/summary'));
  assert.deepEqual(s.tables.map((t) => [t.tbl, t.n]), [['sales', 1]]);
});

test('pull pages by change number and a device only sees what is new', async () => {
  const { env, shop, phone } = await setup();
  const records = Array.from({ length: 50 }, (_, i) => rec('lots', i + 1, i + 1));
  await signed(env, shop, 'POST', '/v1/push', { records });
  const seen = [];
  let after = 0, more = true, pages = 0;
  while (more) {
    const p = await json(await signed(env, phone, 'GET', `/v1/pull?after=${after}&limit=20`));
    seen.push(...p.records.map((x) => x.r));
    after = p.next; more = p.more; pages++;
  }
  assert.equal(pages, 3);
  assert.equal(new Set(seen).size, 50);
  await signed(env, shop, 'POST', '/v1/push', { records: [rec('lots', 7, 100, { id: 7, changed: true })] });
  const p = await json(await signed(env, phone, 'GET', `/v1/pull?after=${after}`));
  assert.deepEqual(p.records.map((x) => [x.r, x.d.changed]), [[7, true]]);
  const q = await json(await signed(env, phone, 'GET', `/v1/pull?after=0&table=nothing`));
  assert.equal(q.records.length, 0);
});

test('bad pushes are refused whole', async () => {
  const { env, shop, phone } = await setup();
  for (const bad of [
    { records: [] }, {}, { records: [{ t: 'Bad Name', r: 1, v: 1, d: {} }] }, { records: [{ t: 'a', r: 0, v: 1, d: {} }] },
    { records: [{ t: 'a', r: 1, v: 0, d: {} }] }, { records: [{ t: 'a', r: 1.5, v: 1, d: {} }] }, { records: [{ t: 'a', r: 1, v: 1, d: [1] }] },
    { records: Array.from({ length: 201 }, (_, i) => rec('a', i + 1, 1)) },
  ]) {
    const tooMany = bad.records && bad.records.length > 200;
    assert.equal((await signed(env, shop, 'POST', '/v1/push', bad)).status, tooMany ? 413 : 400);
  }
  assert.equal((await signed(env, shop, 'POST', '/v1/push', { records: [rec('a', 1, 1, { big: 'x'.repeat(210 * 1024) })] })).status, 413);
  const s = await json(await signed(env, phone, 'GET', '/v1/summary'));
  assert.equal(s.tables.length, 0);
  assert.equal((await signed(env, phone, 'POST', '/v1/push', { records: [rec('a', 1, 1)] })).status, 403, 'a phone cannot write rows');
});

test('revoking a device cuts it off at once', async () => {
  const { env, store, shop, phone } = await setup();
  assert.equal((await signed(env, phone, 'GET', '/v1/summary')).status, 200);
  const r = await json(await admin(env, 'POST', '/admin/revoke', { store_id: store, device_id: phone.device_id }));
  assert.equal(r.revoked, 1);
  assert.equal((await signed(env, phone, 'GET', '/v1/summary')).status, 401);
  assert.equal((await signed(env, shop, 'GET', '/v1/summary')).status, 200);
  const info = await json(await admin(env, 'GET', '/admin/stores/' + store));
  assert.equal(info.devices.length, 2);
  assert.ok(info.devices.find((d) => d.id === phone.device_id).revoked_at);
  assert.ok(!JSON.stringify(info).includes(phone.secret));
});

test('commands: phone asks, shop PC applies; retries never duplicate; roles enforced', async () => {
  const { env, shop, phone } = await setup();
  const cmd = { key: 'count-2026-10-31-aaaa', kind: 'count.submit', payload: { lot: 3, qty: 12 } };
  let a = await json(await signed(env, phone, 'POST', '/v1/commands', cmd));
  assert.equal(a.status, 'pending');
  const again = await json(await signed(env, phone, 'POST', '/v1/commands', cmd));
  assert.equal(again.id, a.id);
  assert.equal(again.replayed, true);
  assert.equal((await signed(env, phone, 'POST', '/v1/commands', { ...cmd, payload: { lot: 3, qty: 99 } })).status, 409, 'same key, other content');
  assert.equal((await signed(env, phone, 'POST', '/v1/commands', { ...cmd, key: 'bad key' })).status, 400);
  assert.equal((await signed(env, phone, 'GET', '/v1/commands')).status, 403, 'a phone cannot read the queue');
  assert.equal((await signed(env, phone, 'POST', '/v1/commands/ack', { id: a.id, status: 'applied' })).status, 403);
  const list = await json(await signed(env, shop, 'GET', '/v1/commands'));
  assert.equal(list.commands.length, 1);
  assert.deepEqual(list.commands[0].payload, { lot: 3, qty: 12 });
  assert.equal((await json(await signed(env, shop, 'POST', '/v1/commands/ack', { id: a.id, status: 'rejected', result: { code: 'not_allowed' } }))).updated, 1);
  assert.equal((await json(await signed(env, shop, 'POST', '/v1/commands/ack', { id: a.id, status: 'applied' }))).updated, 0, 'decided once');
  const got = await json(await signed(env, phone, 'GET', '/v1/commands/' + a.id));
  assert.equal(got.status, 'rejected');
  assert.deepEqual(got.result, { code: 'not_allowed' });
  assert.equal((await json(await signed(env, shop, 'GET', '/v1/commands'))).commands.length, 0);
  const phone2 = await pairDevice(env, shop.store_id, 'mobile', '203.0.113.7');
  assert.equal((await signed(env, phone2, 'GET', '/v1/commands/' + a.id)).status, 404, "another phone cannot read someone else's command");
});

test('cleanup expires old commands and drops stale nonces', async () => {
  const { env, phone } = await setup();
  await signed(env, phone, 'POST', '/v1/commands', { key: 'old-command-1', kind: 'ping', payload: {} });
  await env.DB.prepare('UPDATE commands SET created_at = 1').run();
  await env.DB.prepare('UPDATE nonces SET at = 1').run();
  await cleanup(env);
  assert.equal((await env.DB.prepare("SELECT status FROM commands").first()).status, 'expired');
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM nonces').first()).n, 0);
});

test('unknown paths and errors never leak details', async () => {
  const { env, shop } = await setup();
  assert.equal((await call(env, 'GET', '/nothing')).status, 404);
  assert.equal((await signed(env, shop, 'GET', '/v1/nothing')).status, 404);
  const r = await call(env, 'POST', '/v1/pair', { body: 'not json' });
  assert.equal(r.status, 400);
  const broken = { ...env, DB: { prepare() { throw new Error('secret table path /x/y'); } } };
  const e = await call(broken, 'GET', '/v1/summary', { headers: { 'X-SM-Store': 'a'.repeat(32) } });
  assert.ok(!(await e.text()).includes('secret table'));
});
