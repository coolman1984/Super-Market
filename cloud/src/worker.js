// Super Market cloud copy - a Cloudflare Worker + D1. No dependencies.
//
//   shop PC (role "shop")   POST /v1/push        sends changed rows           (signed)
//                           GET  /v1/commands    asks for pending commands    (signed)
//                           POST /v1/commands/ack  reports what it did        (signed)
//   phone (role "mobile")   GET  /v1/pull        reads the copy, read-only    (signed)
//                           POST /v1/commands    asks the shop PC to do something (signed)
//   owner (admin key)       POST /admin/stores, /admin/pairing, /admin/revoke, GET /admin/stores/:id
//   new device              POST /v1/pair        trades a one-time code for its own secret
//
// The shop PC's database is the only source of truth. The cloud never merges or recalculates money:
// it keeps the latest row per (table, id), refuses to let an older version overwrite a newer one, and
// relays commands that the shop PC applies (or rejects) with its own rules.
// Secrets are never stored here: each device's signing secret is derived from MASTER_KEY, pairing codes
// are kept only as hashes, and nothing sensitive is ever logged.

const enc = new TextEncoder();
const VERSION = '1.0.0';
const MAX_BODY = 900 * 1024;
const MAX_RECORDS = 200;
const MAX_RECORD_JSON = 200 * 1024;
const MAX_SMALL = 16 * 1024;
const PULL_DEFAULT = 300, PULL_MAX = 500;
const SKEW = 300;                                   // seconds the device clock may differ
const STORE_RE = /^[0-9a-f]{32}$/;
const DEVICE_RE = /^[0-9a-f]{16}$/;
const TBL_RE = /^[a-z][a-z0-9_]{0,63}$/;
const KEY_RE = /^[A-Za-z0-9_.:-]{8,80}$/;
const KIND_RE = /^[a-z][a-z0-9_.]{0,39}$/;
const NONCE_RE = /^[A-Za-z0-9_-]{16,64}$/;
const CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
const MAX_PENDING = 500;
const ROLES = new Set(['shop', 'mobile']);
const DAY = 86400;

const HEADERS = {
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'",
  'Cache-Control': 'no-store',
};

const now = () => Math.floor(Date.now() / 1000);
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
const sha256Hex = async (data) => hex(await crypto.subtle.digest('SHA-256', typeof data === 'string' ? enc.encode(data) : data));
async function hmacHex(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(msg)));
}
// Bounded constant-time compare of two 64-character hex strings.
function same(a, b) {
  let d = (a ? a.length : 0) ^ (b ? b.length : 0);
  for (let i = 0; i < 64; i++) {
    const x = a && i < a.length ? a.charCodeAt(i) : 0;
    const y = b && i < b.length ? b.charCodeAt(i) : 0;
    d |= x ^ y;
  }
  return d === 0;
}
const randomHex = (bytes) => hex(crypto.getRandomValues(new Uint8Array(bytes)));
function randomCode() {
  const b = crypto.getRandomValues(new Uint8Array(12));
  const s = [...b].map((x) => CODE_ALPHABET[x % 32]).join('');
  return s.slice(0, 4) + '-' + s.slice(4, 8) + '-' + s.slice(8, 12);        // 60 bits, easy to type
}
const normCode = (c) => String(c || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const secretOk = (v) => typeof v === 'string' && v.trim().length >= 16 ? v : '';

function reply(status, data, extra) {
  return new Response(data === null ? null : JSON.stringify(data), { status, headers: { ...HEADERS, 'Content-Type': 'application/json; charset=utf-8', ...(extra || {}) } });
}
class Fail extends Error { constructor(status, msg, extra) { super(msg); this.status = status; this.extra = extra; } }

async function readBody(request, max) {
  const declared = request.headers.get('Content-Length');
  if (declared !== null && Number(declared) > max) throw new Fail(413, 'Too large');
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > max) { try { await reader.cancel(); } catch { /* already failed */ } throw new Fail(413, 'Too large'); }
      chunks.push(value);
    }
  } catch (e) {
    if (e instanceof Fail) throw e;
    throw new Fail(400, 'Bad body');
  }
  const buf = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { buf.set(c, off); off += c.byteLength; }
  return buf;
}
function parseJson(buf) {
  if (!buf.length) return {};
  try {
    const v = JSON.parse(new TextDecoder().decode(buf));
    if (v === null || typeof v !== 'object' || Array.isArray(v)) throw new Error('not an object');
    return v;
  } catch { throw new Fail(400, 'Not a JSON object'); }
}

async function limitedSpan(env, key, max, span) {
  const w = Math.floor(now() / span) * span;
  const r = await env.DB.prepare('INSERT INTO rate(key, window, count) VALUES(?, ?, 1) ON CONFLICT(key, window) DO UPDATE SET count = count + 1 RETURNING count').bind(key, w).first();
  return r.count > max;
}
const clientIp = (request) => request.headers.get('CF-Connecting-IP') || 'unknown';

// ---------------------------------------------------------------- authentication
async function deviceSecret(env, store, device) {
  const master = secretOk(env.MASTER_KEY);
  if (!master) throw new Fail(503, 'Server is not configured');       // never fall open
  return hmacHex(master, 'SM-DEV1|' + store + '|' + device);
}

// Signed request: HMAC(secret, METHOD \n path+query \n time \n nonce \n sha256(body) \n store \n device)
async function authenticate(env, request, url, bodyBytes) {
  const store = request.headers.get('X-SM-Store') || '';
  const device = request.headers.get('X-SM-Device') || '';
  const time = request.headers.get('X-SM-Time') || '';
  const nonce = request.headers.get('X-SM-Nonce') || '';
  const sig = request.headers.get('X-SM-Sig') || '';
  if (!STORE_RE.test(store) || !DEVICE_RE.test(device) || !NONCE_RE.test(nonce) || !/^\d{9,11}$/.test(time)) throw new Fail(401, 'Not signed');
  if (Math.abs(now() - Number(time)) > SKEW) throw new Fail(401, 'Clock is off', { server_time: now() });
  const secret = await deviceSecret(env, store, device);
  const method = request.method;
  const expected = await hmacHex(secret, [method, url.pathname + url.search, time, nonce, await sha256Hex(bodyBytes), store, device].join('\n'));
  if (!same(expected, sig)) throw new Fail(401, 'Bad signature');
  const dev = await env.DB.prepare('SELECT role, revoked_at FROM devices WHERE store_id = ? AND id = ?').bind(store, device).first();
  if (!dev || dev.revoked_at) throw new Fail(401, 'Device is not allowed');
  if (method !== 'GET') {
    // Every write is idempotent already; the nonce only stops a captured request from being replayed.
    const r = await env.DB.prepare('INSERT OR IGNORE INTO nonces(nonce, at) VALUES(?, ?)').bind(store + device + nonce, now()).run();
    if (!(r.meta && r.meta.changes)) throw new Fail(401, 'Replay');
  }
  return { store, device, role: dev.role };
}
function need(auth, role) { if (auth.role !== role) throw new Fail(403, 'This device may not do that'); }

async function adminCheck(env, request) {
  const admin = secretOk(env.ADMIN_KEY);
  if (!admin) throw new Fail(503, 'Admin is not configured');
  if (await limitedSpan(env, 'admin|' + clientIp(request), 60, 600)) throw new Fail(429, 'Too many requests');
  const given = request.headers.get('X-SM-Admin') || '';
  if (!same(await hmacHex('SM-ADMIN-CMP', given), await hmacHex('SM-ADMIN-CMP', admin))) throw new Fail(401, 'Wrong admin key');
}

// ---------------------------------------------------------------- admin
async function adminCreateStore(env, body) {
  const name = typeof body.name === 'string' ? body.name.trim().slice(0, 80) : '';
  if (!name) throw new Fail(400, 'name is required');
  const id = randomHex(16);
  await env.DB.prepare('INSERT INTO stores(id, name, created_at) VALUES(?, ?, ?)').bind(id, name, now()).run();
  return { store_id: id, name };
}
async function adminPairing(env, body) {
  const store = String(body.store_id || '');
  const role = String(body.role || '');
  if (!STORE_RE.test(store) || !ROLES.has(role)) throw new Fail(400, 'store_id and role (shop|mobile) are required');
  if (!(await env.DB.prepare('SELECT 1 FROM stores WHERE id = ?').bind(store).first())) throw new Fail(404, 'No such store');
  const minutes = Math.min(60, Math.max(1, Number.isInteger(body.ttl_min) ? body.ttl_min : 15));
  const code = randomCode();
  await env.DB.prepare('INSERT INTO pairings(code_hash, store_id, role, expires_at) VALUES(?, ?, ?, ?)')
    .bind(await sha256Hex('SM-PAIR1|' + store + '|' + normCode(code)), store, role, now() + minutes * 60).run();
  return { code, role, expires_in_minutes: minutes };
}
async function adminStore(env, id) {
  if (!STORE_RE.test(id)) throw new Fail(400, 'Bad store id');
  const s = await env.DB.prepare('SELECT id, name, created_at, seq, last_push FROM stores WHERE id = ?').bind(id).first();
  if (!s) throw new Fail(404, 'No such store');
  const devices = (await env.DB.prepare('SELECT id, name, role, created_at, last_seen, revoked_at FROM devices WHERE store_id = ? ORDER BY created_at').bind(id).all()).results;
  return { store: s, devices };
}
async function adminRevoke(env, body) {
  const store = String(body.store_id || ''), device = String(body.device_id || '');
  if (!STORE_RE.test(store) || !DEVICE_RE.test(device)) throw new Fail(400, 'store_id and device_id are required');
  const r = await env.DB.prepare('UPDATE devices SET revoked_at = ? WHERE store_id = ? AND id = ? AND revoked_at IS NULL').bind(now(), store, device).run();
  return { revoked: (r.meta && r.meta.changes) || 0 };
}

// A new device trades the one-time code for its own device id + secret. The code is single-use and short-lived;
// a wrong guess is counted per address and per store, so it cannot be brute-forced.
async function pair(env, request, body) {
  const ip = clientIp(request);
  if (await limitedSpan(env, 'pair|' + ip, 20, 3600)) throw new Fail(429, 'Too many tries');
  const store = String(body.store_id || '');
  const code = normCode(body.code);
  const name = typeof body.name === 'string' ? body.name.trim().slice(0, 60) : '';
  if (!STORE_RE.test(store) || code.length !== 12 || !name) throw new Fail(400, 'store_id, code and name are required');
  if (await limitedSpan(env, 'pairstore|' + store, 30, 3600)) throw new Fail(429, 'Too many tries');
  const hash = await sha256Hex('SM-PAIR1|' + store + '|' + code);
  const t = now();
  // claim the code atomically: only the first caller gets it
  const claim = await env.DB.prepare('UPDATE pairings SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ? RETURNING role')
    .bind(t, hash, t).first();
  if (!claim) throw new Fail(403, 'The code is wrong, used or expired');
  const device = randomHex(8);
  await env.DB.prepare('INSERT INTO devices(store_id, id, name, role, created_at, last_seen) VALUES(?, ?, ?, ?, ?, ?)').bind(store, device, name, claim.role, t, t).run();
  return { store_id: store, device_id: device, secret: await deviceSecret(env, store, device), role: claim.role };
}

// ---------------------------------------------------------------- push / pull
function checkRecord(r) {
  if (!r || typeof r !== 'object') throw new Fail(400, 'Bad record');
  if (typeof r.t !== 'string' || !TBL_RE.test(r.t)) throw new Fail(400, 'Bad table name');
  if (!Number.isSafeInteger(r.r) || r.r < 1) throw new Fail(400, 'Bad row id');
  if (!Number.isSafeInteger(r.v) || r.v < 1) throw new Fail(400, 'Bad version');
  if (r.d === null || r.d === undefined) return { t: r.t, r: r.r, v: r.v, deleted: 1, data: null };
  if (typeof r.d !== 'object' || Array.isArray(r.d)) throw new Fail(400, 'Bad row data');
  const data = JSON.stringify(r.d);
  if (data.length > MAX_RECORD_JSON) throw new Fail(413, 'A row is too large');
  return { t: r.t, r: r.r, v: r.v, deleted: 0, data };
}

async function push(env, auth, body) {
  need(auth, 'shop');
  if (!Array.isArray(body.records) || !body.records.length) throw new Fail(400, 'records is required');
  if (body.records.length > MAX_RECORDS) throw new Fail(413, 'At most ' + MAX_RECORDS + ' records per push');
  const recs = body.records.map(checkRecord);
  const n = recs.length;
  const stmts = [env.DB.prepare('UPDATE stores SET seq = seq + ?, last_push = ? WHERE id = ?').bind(n, now(), auth.store)];
  // An older version never replaces a newer one (a retried or reordered push is harmless).
  const sql = 'INSERT INTO records(store_id, tbl, rid, v, seq, deleted, data) VALUES(?, ?, ?, ?, (SELECT seq FROM stores WHERE id = ?) - ? + ?, ?, ?) '
    + 'ON CONFLICT(store_id, tbl, rid) DO UPDATE SET v = excluded.v, seq = excluded.seq, deleted = excluded.deleted, data = excluded.data WHERE excluded.v > records.v';
  recs.forEach((r, i) => stmts.push(env.DB.prepare(sql).bind(auth.store, r.t, r.r, r.v, auth.store, n, i + 1, r.deleted, r.data)));
  const out = await env.DB.batch(stmts);
  let accepted = 0;
  for (let i = 1; i < out.length; i++) accepted += (out[i].meta && out[i].meta.changes) ? 1 : 0;
  return { accepted, stale: n - accepted };
}

async function pull(env, auth, url) {
  const after = Number(url.searchParams.get('after') || 0);
  if (!Number.isSafeInteger(after) || after < 0) throw new Fail(400, 'Bad cursor');
  const limit = Math.min(PULL_MAX, Math.max(1, Number(url.searchParams.get('limit')) || PULL_DEFAULT));
  const tbl = url.searchParams.get('table');
  if (tbl !== null && !TBL_RE.test(tbl)) throw new Fail(400, 'Bad table name');
  const rows = (tbl
    ? await env.DB.prepare('SELECT tbl, rid, v, seq, deleted, data FROM records WHERE store_id = ? AND seq > ? AND tbl = ? ORDER BY seq LIMIT ?').bind(auth.store, after, tbl, limit + 1).all()
    : await env.DB.prepare('SELECT tbl, rid, v, seq, deleted, data FROM records WHERE store_id = ? AND seq > ? ORDER BY seq LIMIT ?').bind(auth.store, after, limit + 1).all()).results;
  const more = rows.length > limit;
  const page = more ? rows.slice(0, limit) : rows;
  const records = page.map((x) => ({ t: x.tbl, r: x.rid, v: x.v, s: x.seq, d: x.deleted ? null : JSON.parse(x.data) }));
  return { records, next: records.length ? records[records.length - 1].s : after, more };
}

async function summary(env, auth) {
  const s = await env.DB.prepare('SELECT seq, last_push FROM stores WHERE id = ?').bind(auth.store).first();
  const tables = (await env.DB.prepare('SELECT tbl, COUNT(*) AS n, MAX(v) AS max_v FROM records WHERE store_id = ? AND deleted = 0 GROUP BY tbl ORDER BY tbl').bind(auth.store).all()).results;
  const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM commands WHERE store_id = ? AND status = 'pending'").bind(auth.store).first();
  return { seq: s.seq, last_push: s.last_push, tables, pending_commands: pending.n, server_time: now() };
}

// ---------------------------------------------------------------- commands
async function submitCommand(env, auth, body) {
  const key = String(body.key || ''), kind = String(body.kind || '');
  if (!KEY_RE.test(key)) throw new Fail(400, 'key must be 8-80 letters, digits, . _ : -');
  if (!KIND_RE.test(kind)) throw new Fail(400, 'bad kind');
  if (body.payload === null || typeof body.payload !== 'object' || Array.isArray(body.payload)) throw new Fail(400, 'payload must be an object');
  const payload = JSON.stringify(body.payload);
  if (payload.length > MAX_SMALL) throw new Fail(413, 'payload too large');
  const old = await env.DB.prepare('SELECT id, kind, payload, status, result FROM commands WHERE store_id = ? AND key = ?').bind(auth.store, key).first();
  if (old) {
    if (old.kind !== kind || old.payload !== payload) throw new Fail(409, 'This key was already used for another command');
    return { id: old.id, status: old.status, replayed: true };
  }
  const open = await env.DB.prepare("SELECT COUNT(*) AS n FROM commands WHERE store_id = ? AND status = 'pending'").bind(auth.store).first();
  if (open.n >= MAX_PENDING) throw new Fail(429, 'Too many commands are waiting for the shop PC');
  const r = await env.DB.prepare('INSERT INTO commands(store_id, key, kind, payload, from_device, created_at) VALUES(?, ?, ?, ?, ?, ?)')
    .bind(auth.store, key, kind, payload, auth.device, now()).run();
  return { id: r.meta.last_row_id, status: 'pending', replayed: false };
}
async function listCommands(env, auth, url) {
  need(auth, 'shop');
  const after = Number(url.searchParams.get('after') || 0) || 0;
  const rows = (await env.DB.prepare("SELECT id, key, kind, payload, from_device, created_at FROM commands WHERE store_id = ? AND status = 'pending' AND id > ? ORDER BY id LIMIT 50").bind(auth.store, after).all()).results;
  return { commands: rows.map((c) => ({ id: c.id, key: c.key, kind: c.kind, payload: JSON.parse(c.payload), from_device: c.from_device, created_at: c.created_at })) };
}
async function ackCommand(env, auth, body) {
  need(auth, 'shop');
  const status = String(body.status || '');
  if (!Number.isSafeInteger(body.id) || !['applied', 'rejected'].includes(status)) throw new Fail(400, 'id and status (applied|rejected) are required');
  const result = body.result === undefined ? {} : body.result;
  const text = JSON.stringify(result);
  if (text.length > MAX_SMALL) throw new Fail(413, 'result too large');
  const r = await env.DB.prepare("UPDATE commands SET status = ?, result = ?, done_at = ? WHERE store_id = ? AND id = ? AND status = 'pending'").bind(status, text, now(), auth.store, body.id).run();
  return { updated: (r.meta && r.meta.changes) || 0 };
}
async function getCommand(env, auth, id) {
  const c = await env.DB.prepare('SELECT id, kind, status, result, from_device, created_at, done_at FROM commands WHERE store_id = ? AND id = ?').bind(auth.store, id).first();
  if (!c || (auth.role !== 'shop' && c.from_device !== auth.device)) throw new Fail(404, 'No such command');
  return { id: c.id, kind: c.kind, status: c.status, result: c.result ? JSON.parse(c.result) : null, created_at: c.created_at, done_at: c.done_at };
}

// ---------------------------------------------------------------- housekeeping (daily cron)
export async function cleanup(env) {
  const t = now();
  await env.DB.batch([
    env.DB.prepare('DELETE FROM nonces WHERE at < ?').bind(t - 2 * SKEW),
    env.DB.prepare('DELETE FROM rate WHERE window < ?').bind(t - 2 * DAY),
    env.DB.prepare('DELETE FROM pairings WHERE expires_at < ?').bind(t - DAY),
    env.DB.prepare("UPDATE commands SET status = 'expired', done_at = ? WHERE status = 'pending' AND created_at < ?").bind(t, t - 7 * DAY),
    env.DB.prepare("DELETE FROM commands WHERE status <> 'pending' AND done_at < ?").bind(t - 30 * DAY),
  ]);
}

// ---------------------------------------------------------------- router
async function handle(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  if (path === '/' && request.method === 'GET') return reply(200, { ok: true, service: 'super-market-sync', version: VERSION });

  if (path.startsWith('/admin/')) {
    await adminCheck(env, request);
    const body = request.method === 'GET' ? {} : parseJson(await readBody(request, MAX_SMALL));
    if (path === '/admin/stores' && request.method === 'POST') return reply(200, await adminCreateStore(env, body));
    if (path === '/admin/pairing' && request.method === 'POST') return reply(200, await adminPairing(env, body));
    if (path === '/admin/revoke' && request.method === 'POST') return reply(200, await adminRevoke(env, body));
    const m = path.match(/^\/admin\/stores\/([0-9a-f]{32})$/);
    if (m && request.method === 'GET') return reply(200, await adminStore(env, m[1]));
    throw new Fail(404, 'Not found');
  }
  if (path === '/v1/pair' && request.method === 'POST') return reply(200, await pair(env, request, parseJson(await readBody(request, MAX_SMALL))));

  if (path.startsWith('/v1/')) {
    const bytes = request.method === 'GET' ? new Uint8Array(0) : await readBody(request, path === '/v1/push' ? MAX_BODY : MAX_SMALL);
    const auth = await authenticate(env, request, url, bytes);
    await env.DB.prepare('UPDATE devices SET last_seen = ? WHERE store_id = ? AND id = ? AND (last_seen IS NULL OR last_seen < ?)').bind(now(), auth.store, auth.device, now() - 60).run();
    const body = request.method === 'GET' ? {} : parseJson(bytes);
    if (path === '/v1/push' && request.method === 'POST') return reply(200, await push(env, auth, body));
    if (path === '/v1/pull' && request.method === 'GET') return reply(200, await pull(env, auth, url));
    if (path === '/v1/summary' && request.method === 'GET') return reply(200, await summary(env, auth));
    if (path === '/v1/commands' && request.method === 'POST') return reply(200, await submitCommand(env, auth, body));
    if (path === '/v1/commands' && request.method === 'GET') return reply(200, await listCommands(env, auth, url));
    if (path === '/v1/commands/ack' && request.method === 'POST') return reply(200, await ackCommand(env, auth, body));
    const m = path.match(/^\/v1\/commands\/(\d{1,12})$/);
    if (m && request.method === 'GET') return reply(200, await getCommand(env, auth, Number(m[1])));
  }
  throw new Fail(404, 'Not found');
}

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (e) {
      if (e instanceof Fail) return reply(e.status, { ok: false, error: e.message, ...(e.extra || {}) });
      return reply(500, { ok: false, error: 'Server error' });        // details are never sent out
    }
  },
  async scheduled(event, env) { await cleanup(env); },
};
export { hmacHex, sha256Hex };
