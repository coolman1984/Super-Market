import { randomBytes } from 'node:crypto';
import worker, { hmacHex, sha256Hex } from '../src/worker.js';
import { makeEnv } from '../dev/server.js';

export const MASTER = 'master-key-for-tests-0123456789abcdef';
export const ADMIN = 'admin-key-for-tests-0123456789abcdef';
export const newEnv = (extra) => makeEnv({ MASTER_KEY: MASTER, ADMIN_KEY: ADMIN, ...(extra || {}) });

export const call = (env, method, path, { body, headers } = {}) =>
  worker.fetch(new Request('http://sync.test' + path, { method, headers: { 'CF-Connecting-IP': '9.9.9.9', ...(headers || {}) }, body }), env);

export const admin = (env, method, path, obj, key = ADMIN) =>
  call(env, method, path, { body: obj === undefined ? undefined : JSON.stringify(obj), headers: { 'X-SM-Admin': key, 'Content-Type': 'application/json' } });

// A signed device request, the way server/cloudsync.py makes it.
export async function signed(env, dev, method, path, obj, over = {}) {
  const body = obj === undefined ? undefined : JSON.stringify(obj);
  const bytes = body === undefined ? new Uint8Array(0) : new TextEncoder().encode(body);
  const time = String(over.time ?? Math.floor(Date.now() / 1000));
  const nonce = over.nonce ?? randomBytes(16).toString('hex');
  const sig = over.sig ?? await hmacHex(over.secret ?? dev.secret, [method, path, time, nonce, await sha256Hex(bytes), dev.store_id, dev.device_id].join('\n'));
  return call(env, method, path, { body, headers: { 'X-SM-Store': dev.store_id, 'X-SM-Device': dev.device_id, 'X-SM-Time': time, 'X-SM-Nonce': nonce, 'X-SM-Sig': sig, 'Content-Type': 'application/json' } });
}

export async function makeStore(env, name = 'Test shop') {
  const r = await admin(env, 'POST', '/admin/stores', { name });
  if (r.status !== 200) throw new Error('store ' + r.status);
  return (await r.json()).store_id;
}
export async function pairDevice(env, store, role = 'shop', ip = '203.0.113.5') {
  const c = await admin(env, 'POST', '/admin/pairing', { store_id: store, role });
  const { code } = await c.json();
  const r = await call(env, 'POST', '/v1/pair', { body: JSON.stringify({ store_id: store, code, name: role + ' device' }), headers: { 'CF-Connecting-IP': ip } });
  if (r.status !== 200) throw new Error('pair ' + r.status + ' ' + JSON.stringify(await r.json()));
  return r.json();
}
export const rec = (t, r, v, d = { id: r }) => ({ t, r, v, d });
