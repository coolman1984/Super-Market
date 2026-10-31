// Super Market cloud copy - a Cloudflare Worker + D1. No dependencies.
//
//   shop PC (role "shop")   POST /v1/push        sends changed rows           (signed)
//                           GET  /v1/commands    asks for pending commands    (signed)
//                           POST /v1/commands/ack  reports what it did        (signed)
//   phone (role "mobile")   GET  /v1/reports/... reads the REPORTS the shop PC published (read-only, signed)
//                           POST /v1/commands    only {kind:"ping"}; every other command is refused
//                           (a phone cannot push, pull raw rows, or change anything)
//   first run of an installed program  POST /v1/enroll   makes its own store + device, no human step (build token, rate limited)
//   replacement PC          POST /v1/recover     the store's recovery code trades for a new device (old devices are cut off)
//   owner (admin key)       POST /admin/stores, /admin/pairing, /admin/revoke, /admin/recovery, /admin/stores/status,
//                           /admin/builds/revoke, GET /admin/stores/:id        (support fallback; not needed day to day)
//   new device              POST /v1/pair        trades a one-time code for its own secret (admin fallback)
//
// The shop PC's database is the only source of truth. The cloud never merges or recalculates money:
// it keeps the latest row per (table, id), refuses to let an older version overwrite a newer one, and
// relays commands that the shop PC applies (or rejects) with its own rules.
// Secrets are never stored here: each device's signing secret is derived from MASTER_KEY, pairing codes
// are kept only as hashes, and nothing sensitive is ever logged.

const enc = new TextEncoder();
const VERSION = '1.2.0';
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
const BUILD_RE = /^[A-Za-z0-9._-]{1,32}$/;
const INSTALL_RE = /^[0-9a-f]{32}$/;
const TRIAL_DAYS = 30;
// A store made by a first run starts 'pending' (trial). Until a licence activates it, its writes per day are capped
// so one abusive install cannot use up the free D1 allowance (100k row writes a day for the whole account).
const DAILY_ROWS = { pending: 20000, active: 60000 };
const ENROLL_PER_IP_HOUR = 6, ENROLL_PER_IP_DAY = 20, ENROLL_ALL_DAY = 400, RECOVER_PER_IP_HOUR = 10;

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

// Adds `add` to a counter and says whether it went over `max` (used for the daily row budget of a store).
async function budgetOver(env, key, add, max, span) {
  const w = Math.floor(now() / span) * span;
  const r = await env.DB.prepare('INSERT INTO rate(key, window, count) VALUES(?, ?, ?) ON CONFLICT(key, window) DO UPDATE SET count = count + ? RETURNING count').bind(key, w, add, add).first();
  return r.count > max;
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
  const dev = await env.DB.prepare('SELECT d.role AS role, d.revoked_at AS revoked_at, m.status AS status FROM devices d LEFT JOIN store_meta m ON m.store_id = d.store_id WHERE d.store_id = ? AND d.id = ?').bind(store, device).first();
  if (!dev || dev.revoked_at) throw new Fail(401, 'Device is not allowed');
  if (dev.status === 'suspended') throw new Fail(403, 'Store is suspended');
  if (method !== 'GET') {
    // Every write is idempotent already; the nonce only stops a captured request from being replayed.
    const r = await env.DB.prepare('INSERT OR IGNORE INTO nonces(nonce, at) VALUES(?, ?)').bind(store + device + nonce, now()).run();
    if (!(r.meta && r.meta.changes)) throw new Fail(401, 'Replay');
  }
  return { store, device, role: dev.role, status: dev.status || 'active' };
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
  const meta = await env.DB.prepare('SELECT status, build_id, trial_until, created_via FROM store_meta WHERE store_id = ?').bind(id).first();
  return { store: s, devices, meta: meta || { status: 'active', created_via: 'admin' } };
}
async function adminRevoke(env, body) {
  const store = String(body.store_id || ''), device = String(body.device_id || '');
  if (!STORE_RE.test(store) || !DEVICE_RE.test(device)) throw new Fail(400, 'store_id and device_id are required');
  const r = await env.DB.prepare('UPDATE devices SET revoked_at = ? WHERE store_id = ? AND id = ? AND revoked_at IS NULL').bind(now(), store, device).run();
  return { revoked: (r.meta && r.meta.changes) || 0 };
}

// ---------------------------------------------------------------- first run: the installed program enrolls itself
// Nobody types anything. The build carries a token `<build>.<signature>` that only this service can have signed;
// a build can be revoked here at any time. On top of that: per-address and global limits per hour/day, a store that
// starts 'pending' (trial, capped writes), and idempotency by a random install id so a lost answer never makes two stores.
async function enrollToken(env, token) {
  const key = secretOk(env.ENROLL_KEY);
  if (!key) throw new Fail(503, 'Enrolment is not configured');
  const text = String(token || '');
  const cut = text.lastIndexOf('.');
  const build = text.slice(0, Math.max(cut, 0)), sig = text.slice(cut + 1);
  if (!BUILD_RE.test(build || '') || !/^[0-9a-f]{32}$/.test(sig || '')) throw new Fail(403, 'Bad build token');
  const want = (await hmacHex(key, 'SM-ENROLL1|' + build)).slice(0, 32);
  if (!same(await hmacHex('SM-CMP', sig), await hmacHex('SM-CMP', want))) throw new Fail(403, 'Bad build token');
  return build;
}
async function recoveryCode(env, store) {
  const master = secretOk(env.MASTER_KEY);
  if (!master) throw new Fail(503, 'Server is not configured');
  const bytes = (await hmacHex(master, 'SM-REC1|' + store)).match(/../g).slice(0, 20).map((h) => parseInt(h, 16));
  const s = bytes.map((x) => CODE_ALPHABET[x % 32]).join('');
  return [s.slice(0, 5), s.slice(5, 10), s.slice(10, 15), s.slice(15, 20)].join('-');       // 100 bits, written on paper by the owner
}
const recoveryHash = (code) => sha256Hex('SM-REC1H|' + normCode(code));
async function newDevice(env, store, name, t) {
  const device = randomHex(8);
  await env.DB.prepare("INSERT INTO devices(store_id, id, name, role, created_at, last_seen) VALUES(?, ?, ?, 'shop', ?, ?)").bind(store, device, name, t, t).run();
  return device;
}
async function credentials(env, store, device, status) {
  return { store_id: store, device_id: device, secret: await deviceSecret(env, store, device), role: 'shop', status, recovery_code: await recoveryCode(env, store) };
}
async function enroll(env, request, body) {
  const ip = clientIp(request);
  const build = await enrollToken(env, body.token);
  if (await limitedSpan(env, 'enroll|' + ip, ENROLL_PER_IP_HOUR, 3600) || await limitedSpan(env, 'enrollday|' + ip, ENROLL_PER_IP_DAY, DAY)) throw new Fail(429, 'Too many tries from this address');
  const install = String(body.install_id || '');
  if (!INSTALL_RE.test(install)) throw new Fail(400, 'install_id is required');
  const name = typeof body.name === 'string' ? body.name.trim().slice(0, 60) : '';
  const hash = await sha256Hex('SM-INST1|' + install);
  const t = now();
  const old = await env.DB.prepare('SELECT e.store_id AS store_id, e.device_id AS device_id, m.status AS status FROM enrollments e LEFT JOIN store_meta m ON m.store_id = e.store_id WHERE e.install_hash = ?').bind(hash).first();
  if (old) {
    const dev = await env.DB.prepare('SELECT revoked_at FROM devices WHERE store_id = ? AND id = ?').bind(old.store_id, old.device_id).first();
    if (dev && !dev.revoked_at) return credentials(env, old.store_id, old.device_id, old.status || 'active');   // a retry after a lost answer
    throw new Fail(403, 'This installation was cut off; use the recovery code');
  }
  if (await limitedSpan(env, 'enrollall|' + DAY, ENROLL_ALL_DAY, DAY)) throw new Fail(429, 'The service is busy; the program tries again later');
  if (await env.DB.prepare('SELECT 1 FROM builds WHERE build_id = ? AND revoked_at IS NOT NULL').bind(build).first()) throw new Fail(403, 'This build is no longer allowed; update the program');
  const store = randomHex(16), device = randomHex(8);
  const label = name || 'Shop';
  await env.DB.batch([
    env.DB.prepare('INSERT INTO stores(id, name, created_at) VALUES(?, ?, ?)').bind(store, label + ' ' + store.slice(0, 6), t),
    env.DB.prepare("INSERT INTO store_meta(store_id, status, build_id, trial_until, created_via, recovery_hash) VALUES(?, 'pending', ?, ?, 'enroll', ?)")
      .bind(store, build, t + TRIAL_DAYS * DAY, await recoveryHash(await recoveryCode(env, store))),
    env.DB.prepare("INSERT INTO devices(store_id, id, name, role, created_at, last_seen) VALUES(?, ?, ?, 'shop', ?, ?)").bind(store, device, label, t, t),
    env.DB.prepare('INSERT INTO enrollments(install_hash, store_id, device_id, created_at) VALUES(?, ?, ?, ?)').bind(hash, store, device, t),
  ]);
  return credentials(env, store, device, 'pending');
}

// A replacement PC: the recovery code (kept by the owner) is the proof. All older devices of the store are cut off
// (a lost or stolen PC stops at once). Wrong codes are counted per address; the code has 100 bits so it cannot be guessed.
async function recover(env, request, body) {
  const ip = clientIp(request);
  await enrollToken(env, body.token);
  if (await limitedSpan(env, 'recover|' + ip, RECOVER_PER_IP_HOUR, 3600)) throw new Fail(429, 'Too many tries from this address');
  const code = normCode(body.code);
  const install = String(body.install_id || '');
  if (code.length !== 20 || !INSTALL_RE.test(install)) throw new Fail(400, 'code and install_id are required');
  const name = typeof body.name === 'string' ? body.name.trim().slice(0, 60) : 'Shop PC';
  const row = await env.DB.prepare('SELECT store_id, status FROM store_meta WHERE recovery_hash = ?').bind(await recoveryHash(code)).first();
  if (!row) throw new Fail(403, 'The recovery code is wrong');
  const hash = await sha256Hex('SM-INST1|' + install);
  const again = await env.DB.prepare('SELECT store_id, device_id FROM enrollments WHERE install_hash = ?').bind(hash).first();
  if (again) {
    if (again.store_id !== row.store_id) throw new Fail(409, 'This installation belongs to another shop');
    const dev = await env.DB.prepare('SELECT revoked_at FROM devices WHERE store_id = ? AND id = ?').bind(again.store_id, again.device_id).first();
    if (dev && !dev.revoked_at) return credentials(env, again.store_id, again.device_id, row.status);   // a retry after a lost answer
  }
  const t = now();
  const device = randomHex(8);
  await env.DB.batch([
    env.DB.prepare("UPDATE devices SET revoked_at = ? WHERE store_id = ? AND role = 'shop' AND revoked_at IS NULL").bind(t, row.store_id),
    env.DB.prepare("INSERT INTO devices(store_id, id, name, role, created_at, last_seen) VALUES(?, ?, ?, 'shop', ?, ?)").bind(row.store_id, device, name, t, t),
    env.DB.prepare('INSERT OR REPLACE INTO enrollments(install_hash, store_id, device_id, created_at) VALUES(?, ?, ?, ?)').bind(hash, row.store_id, device, t),
  ]);
  return credentials(env, row.store_id, device, row.status);
}

async function adminRecovery(env, body) {
  const store = String(body.store_id || '');
  if (!STORE_RE.test(store)) throw new Fail(400, 'store_id is required');
  const meta = await env.DB.prepare('SELECT recovery_hash FROM store_meta WHERE store_id = ?').bind(store).first();
  if (!meta) throw new Fail(404, 'This store has no recovery code (it was made by hand: use /admin/pairing)');
  return { recovery_code: await recoveryCode(env, store) };
}
async function adminStatus(env, body) {
  const store = String(body.store_id || ''), status = String(body.status || '');
  if (!STORE_RE.test(store) || !['pending', 'active', 'suspended'].includes(status)) throw new Fail(400, 'store_id and status (pending|active|suspended) are required');
  const r = await env.DB.prepare('UPDATE store_meta SET status = ? WHERE store_id = ?').bind(status, store).run();
  if (!(r.meta && r.meta.changes)) throw new Fail(404, 'No such enrolled store');
  return { store_id: store, status };
}
async function adminBuildRevoke(env, body) {
  const build = String(body.build_id || '');
  if (!BUILD_RE.test(build)) throw new Fail(400, 'build_id is required');
  await env.DB.prepare('INSERT INTO builds(build_id, revoked_at) VALUES(?, ?) ON CONFLICT(build_id) DO UPDATE SET revoked_at = excluded.revoked_at').bind(build, now()).run();
  return { build_id: build, revoked: true };
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
  if (await budgetOver(env, 'rows|' + auth.store, n, DAILY_ROWS[auth.status] || DAILY_ROWS.active, DAY)) {
    throw new Fail(429, 'Daily copy limit reached for this shop; the copy continues tomorrow');
  }
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
  need(auth, 'shop');                       // raw rows (users, customers...) are for the shop PC's own restore only
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
  need(auth, 'shop');
  const s = await env.DB.prepare('SELECT seq, last_push FROM stores WHERE id = ?').bind(auth.store).first();
  const tables = (await env.DB.prepare('SELECT tbl, COUNT(*) AS n, MAX(v) AS max_v FROM records WHERE store_id = ? AND deleted = 0 GROUP BY tbl ORDER BY tbl').bind(auth.store).all()).results;
  const pending = await env.DB.prepare("SELECT COUNT(*) AS n FROM commands WHERE store_id = ? AND status = 'pending'").bind(auth.store).first();
  return { seq: s.seq, last_push: s.last_push, tables, pending_commands: pending.n, server_time: now() };
}

// ---------------------------------------------------------------- commands
async function submitCommand(env, auth, body) {
  if (auth.role === 'mobile' && body.kind !== 'ping') throw new Fail(403, 'Phones are read-only: only ping is allowed');
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

// ---------------------------------------------------------------- reports (read-only, for phones and the shop PC)
// The shop PC computes every figure with its own rules and publishes them as rows of the rep_* tables;
// the cloud only hands them back. It never calculates money.
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
async function reportRow(env, auth, tbl, rid) {
  const row = await env.DB.prepare('SELECT data FROM records WHERE store_id = ? AND tbl = ? AND rid = ? AND deleted = 0').bind(auth.store, tbl, rid).first();
  if (!row) throw new Fail(404, 'This report has not been published yet');
  return JSON.parse(row.data);
}
async function reports(env, auth, path, url) {
  const m = path.match(/^\/v1\/reports(?:\/([a-z-]+))?$/);
  const name = m && m[1];
  const limit = Math.min(60, Math.max(1, Number(url.searchParams.get('limit')) || 14));
  if (!name) {
    const rows = (await env.DB.prepare("SELECT tbl, COUNT(*) AS n FROM records WHERE store_id = ? AND deleted = 0 AND tbl IN ('rep_day','rep_stock','rep_debts','day_closes') GROUP BY tbl").bind(auth.store).all()).results;
    return { reports: ['days', 'day', 'stock', 'debts', 'day-closes'], published: Object.fromEntries(rows.map((r) => [r.tbl, r.n])) };
  }
  if (name === 'days') {
    const rows = (await env.DB.prepare("SELECT data FROM records WHERE store_id = ? AND tbl = 'rep_day' AND deleted = 0 ORDER BY rid DESC LIMIT ?").bind(auth.store, limit).all()).results;
    return { days: rows.map((r) => JSON.parse(r.data)) };
  }
  if (name === 'day') {
    const date = url.searchParams.get('date') || '';
    if (!DAY_RE.test(date)) throw new Fail(400, 'date=YYYY-MM-DD is required');
    return await reportRow(env, auth, 'rep_day', Number(date.replace(/-/g, '')));
  }
  if (name === 'stock') return await reportRow(env, auth, 'rep_stock', 1);
  if (name === 'debts') return await reportRow(env, auth, 'rep_debts', 1);
  if (name === 'day-closes') {
    // the shop PC's own closing snapshots (fingerprinted there); only what a report needs, no user ids
    const rows = (await env.DB.prepare("SELECT data FROM records WHERE store_id = ? AND tbl = 'day_closes' AND deleted = 0 ORDER BY rid DESC LIMIT ?").bind(auth.store, limit).all()).results;
    const closes = rows.map((r) => JSON.parse(r.data)).map((d) => ({ day: d.day, at: d.at, snapshot_hash: d.snapshot_hash, snapshot: JSON.parse(d.snapshot) }));
    closes.sort((a, b) => (a.day < b.day ? 1 : -1));
    return { closes };
  }
  throw new Fail(404, 'Not found');
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
    // an enrolled store that never sent a single row for 14 days holds no data: forget it (spam / abandoned installs)
    env.DB.prepare("DELETE FROM devices WHERE store_id IN (SELECT m.store_id FROM store_meta m JOIN stores s ON s.id = m.store_id WHERE m.status = 'pending' AND s.last_push IS NULL AND s.created_at < ?)").bind(t - 14 * DAY),
    env.DB.prepare("DELETE FROM enrollments WHERE store_id IN (SELECT m.store_id FROM store_meta m JOIN stores s ON s.id = m.store_id WHERE m.status = 'pending' AND s.last_push IS NULL AND s.created_at < ?)").bind(t - 14 * DAY),
    env.DB.prepare("DELETE FROM stores WHERE id IN (SELECT m.store_id FROM store_meta m JOIN stores s ON s.id = m.store_id WHERE m.status = 'pending' AND s.last_push IS NULL AND s.created_at < ?)").bind(t - 14 * DAY),
    env.DB.prepare("DELETE FROM store_meta WHERE store_id NOT IN (SELECT id FROM stores)"),
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
    if (path === '/admin/recovery' && request.method === 'POST') return reply(200, await adminRecovery(env, body));
    if (path === '/admin/stores/status' && request.method === 'POST') return reply(200, await adminStatus(env, body));
    if (path === '/admin/builds/revoke' && request.method === 'POST') return reply(200, await adminBuildRevoke(env, body));
    const m = path.match(/^\/admin\/stores\/([0-9a-f]{32})$/);
    if (m && request.method === 'GET') return reply(200, await adminStore(env, m[1]));
    throw new Fail(404, 'Not found');
  }
  if (path === '/v1/enroll' && request.method === 'POST') return reply(200, await enroll(env, request, parseJson(await readBody(request, MAX_SMALL))));
  if (path === '/v1/recover' && request.method === 'POST') return reply(200, await recover(env, request, parseJson(await readBody(request, MAX_SMALL))));
  if (path === '/v1/pair' && request.method === 'POST') return reply(200, await pair(env, request, parseJson(await readBody(request, MAX_SMALL))));

  if (path.startsWith('/v1/')) {
    const bytes = request.method === 'GET' ? new Uint8Array(0) : await readBody(request, path === '/v1/push' ? MAX_BODY : MAX_SMALL);
    const auth = await authenticate(env, request, url, bytes);
    await env.DB.prepare('UPDATE devices SET last_seen = ? WHERE store_id = ? AND id = ? AND (last_seen IS NULL OR last_seen < ?)').bind(now(), auth.store, auth.device, now() - 60).run();
    const body = request.method === 'GET' ? {} : parseJson(bytes);
    if (path === '/v1/push' && request.method === 'POST') return reply(200, await push(env, auth, body));
    if (path === '/v1/pull' && request.method === 'GET') return reply(200, await pull(env, auth, url));
    if (path.startsWith('/v1/reports') && request.method === 'GET') return reply(200, await reports(env, auth, path, url));
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
