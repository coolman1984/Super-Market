// Super Market cloud backup: one private space per shop PC, on the owner's own Cloudflare account.
// The shop PC is never reachable from the internet: it calls this Worker, never the other way round.
// D1 (binding DB) keeps the list of PCs and copies; R2 (binding BUCKET) keeps the copies themselves.
// MASTER_KEY (a Worker secret) derives each PC's secret: HMAC(MASTER_KEY, "SM-PC1|" + pcId). It is never stored.
const enc = new TextEncoder();
const PC_RE = /^[0-9a-f]{32}$/;
const NAME_RE = /^(hourly|nightly)-\d{8}-\d{6}$/;
const HEX64 = /^[0-9a-f]{64}$/;
export const KEEP = { hourly: 48, nightly: 30 };            // the last two days of hourly copies, a month of nightly ones
const MAX_COPY = 95 * 1024 * 1024;                           // one compressed copy (a Worker request is at most 100 MB)
const MAX_PC_BYTES = 4 * 1024 * 1024 * 1024;                 // all copies of one PC
const MAX_DAY_BYTES = 3 * 1024 * 1024 * 1024;                // uploads of one PC in one day (abuse limit)
const REGISTER_BODY_MAX = 2048;
const REGISTER_PER_HOUR = 5, REGISTER_PER_DAY = 30, REGISTER_GLOBAL_PER_HOUR = 30, REGISTER_GLOBAL_PER_DAY = 200;
const CLOCK_SKEW = 300;
const HEADERS = { 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store' };

const now = () => Math.floor(Date.now() / 1000);
const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
export const sha256Hex = async (data) => hex(await crypto.subtle.digest('SHA-256', typeof data === 'string' ? enc.encode(data) : data));
export async function hmacHex(secret, msg) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', key, enc.encode(msg)));
}
function same(a, b) {               // bounded constant-time compare of two 64-character hex values
  let d = (a ? a.length : 0) ^ (b ? b.length : 0);
  for (let i = 0; i < 64; i++) d |= (a && i < a.length ? a.charCodeAt(i) : 0) ^ (b && i < b.length ? b.charCodeAt(i) : 0);
  return d === 0;
}
function masterKey(env) {
  const k = typeof env.MASTER_KEY === 'string' ? env.MASTER_KEY : '';
  return k.trim().length >= 32 ? k : '';   // missing or short: refuse, never fall open
}
export const pcSecret = (master, pcId) => hmacHex(master, 'SM-PC1|' + pcId);
class Fail extends Error { constructor(status, code, extra) { super(code); this.status = status; this.code = code; this.extra = extra || {}; } }
function reply(status, data, extra) {
  return new Response(JSON.stringify(data), { status, headers: { ...HEADERS, 'Content-Type': 'application/json; charset=utf-8', ...(extra || {}) } });
}
const iso = (s) => new Date(s * 1000).toISOString();

async function readBody(request, max) {
  const declared = request.headers.get('Content-Length');
  if (declared !== null && Number(declared) > max) throw new Fail(413, 'too_large');
  const buf = new Uint8Array(await request.arrayBuffer());
  if (buf.length > max) throw new Fail(413, 'too_large');
  return buf;
}
async function limitedSpan(env, key, max, span) {
  const w = Math.floor(now() / span) * span;
  const r = await env.DB.prepare('INSERT INTO rate(key, window, count) VALUES(?, ?, 1) ON CONFLICT(key, window) DO UPDATE SET count = count + 1 RETURNING count').bind(key, w).first();
  return r.count > max;
}

// ------------------------------------------------------------ registration: the PC's one-time setup, no typing
// installId is a 128-bit value the PC keeps and sends again if the answer was lost; D1 keeps only its hash.
async function register(request, env) {
  const master = masterKey(env);
  if (!master) throw new Fail(503, 'not_ready');
  let data;
  try { data = JSON.parse(new TextDecoder().decode(await readBody(request, REGISTER_BODY_MAX))); } catch (e) { if (e instanceof Fail) throw e; throw new Fail(400, 'bad_json'); }
  const install = String((data && data.installId) || '').trim().toLowerCase();
  if (!PC_RE.test(install)) throw new Fail(400, 'bad_install');
  const label = String((data && data.label) || '').replace(/[\u0000-\u001f]/g, '').slice(0, 60);
  const hash = await sha256Hex(install);
  const known = await env.DB.prepare('SELECT id FROM pcs WHERE install_hash = ?').bind(hash).first();
  if (known) {
    await env.DB.prepare('UPDATE pcs SET last_seen = ? WHERE id = ?').bind(now(), known.id).run();
    return reply(200, { pcId: known.id, secret: await pcSecret(master, known.id) });
  }
  const ip = request.headers.get('CF-Connecting-IP') || 'local';
  if (await limitedSpan(env, 'reg:h:' + ip, REGISTER_PER_HOUR, 3600) || await limitedSpan(env, 'reg:d:' + ip, REGISTER_PER_DAY, 86400)
      || await limitedSpan(env, 'reg:gh', REGISTER_GLOBAL_PER_HOUR, 3600) || await limitedSpan(env, 'reg:gd', REGISTER_GLOBAL_PER_DAY, 86400)) {
    throw new Fail(429, 'busy', { 'Retry-After': '3600' });
  }
  const id = hex(crypto.getRandomValues(new Uint8Array(16)));
  const t = now();
  try {
    await env.DB.prepare('INSERT INTO pcs(id, created_at, last_seen, install_hash, label) VALUES(?, ?, ?, ?, ?)').bind(id, t, t, hash, label).run();
  } catch (e) {
    if (!/UNIQUE/i.test(String(e && e.message || e))) throw e;
    const again = await env.DB.prepare('SELECT id FROM pcs WHERE install_hash = ?').bind(hash).first();
    return reply(200, { pcId: again.id, secret: await pcSecret(master, again.id) });
  }
  return reply(200, { pcId: id, secret: await pcSecret(master, id) });
}

// ------------------------------------------------------------ signed calls of one PC
// Signed: method, path+query, time, nonce, sha256 of the body (declared in X-SM-Body for an upload), pc id.
async function pcAuth(request, env, url) {
  const master = masterKey(env);
  if (!master) throw new Fail(503, 'not_ready');
  const pc = String(request.headers.get('X-SM-PC') || '').toLowerCase();
  const t = Number(request.headers.get('X-SM-Time')), nonce = request.headers.get('X-SM-Nonce') || '';
  const sig = String(request.headers.get('X-SM-Sig') || '').toLowerCase(), body = String(request.headers.get('X-SM-Body') || '').toLowerCase();
  if (!PC_RE.test(pc) || !t || !/^[0-9a-f]{32}$/.test(nonce) || !HEX64.test(sig) || !HEX64.test(body)) throw new Fail(401, 'unauthorised');
  if (Math.abs(now() - t) > CLOCK_SKEW) throw new Fail(401, 'clock', { skewSeconds: t - now(), serverTime: iso(now()) });
  const expected = await hmacHex(await pcSecret(master, pc), [request.method, url.pathname + url.search, String(t), nonce, body, pc].join('\n'));
  if (!same(expected, sig)) throw new Fail(401, 'unauthorised');
  const row = await env.DB.prepare('SELECT id FROM pcs WHERE id = ?').bind(pc).first();
  if (!row) throw new Fail(401, 'unknown_pc');
  const r = await env.DB.prepare('INSERT OR IGNORE INTO nonces(nonce, at) VALUES(?, ?)').bind(nonce, now()).run();
  if (!(r.meta ? r.meta.changes : r.changes)) throw new Fail(401, 'replay');
  await env.DB.prepare('UPDATE pcs SET last_seen = ? WHERE id = ?').bind(now(), pc).run();
  return { pc, body };
}
const EMPTY = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const objectKey = (pc, name) => 'pc/' + pc + '/' + name + '.db.gz';

async function prune(env, pc, kind) {
  const old = await env.DB.prepare('SELECT name FROM copies WHERE pc = ? AND kind = ? ORDER BY name DESC LIMIT -1 OFFSET ?').bind(pc, kind, KEEP[kind]).all();
  for (const row of old.results || []) {
    await env.BUCKET.delete(objectKey(pc, row.name));
    await env.DB.prepare('DELETE FROM copies WHERE pc = ? AND name = ?').bind(pc, row.name).run();
  }
}

async function upload(request, env, pc, declared, name) {
  const size = Number(request.headers.get('Content-Length'));
  if (!Number.isInteger(size) || size <= 0) throw new Fail(411, 'length_required');
  if (size > MAX_COPY) throw new Fail(413, 'too_large');
  const meta = {
    dbSha256: String(request.headers.get('X-SM-Db-Sha256') || '').toLowerCase(),
    schema: Number(request.headers.get('X-SM-Schema')) || 0,
    app: String(request.headers.get('X-SM-App') || '').slice(0, 40),
  };
  if (!HEX64.test(meta.dbSha256)) throw new Fail(400, 'bad_meta');
  const have = await env.DB.prepare('SELECT sha256 FROM copies WHERE pc = ? AND name = ?').bind(pc, name).first();
  if (have) {                       // the answer was lost and the PC sends the same copy again
    if (have.sha256 === declared) return reply(200, { ok: true, name, replayed: true });
    throw new Fail(409, 'name_taken');
  }
  const used = await env.DB.prepare('SELECT COALESCE(SUM(size), 0) n FROM copies WHERE pc = ?').bind(pc).first();
  if (used.n + size > MAX_PC_BYTES) throw new Fail(507, 'pc_full');
  const day = Math.floor(now() / 86400);
  const u = await env.DB.prepare('INSERT INTO usage(pc, day, bytes) VALUES(?, ?, ?) ON CONFLICT(pc, day) DO UPDATE SET bytes = bytes + excluded.bytes RETURNING bytes').bind(pc, day, size).first();
  if (u.bytes > MAX_DAY_BYTES) throw new Fail(429, 'day_limit', { 'Retry-After': '3600' });
  try {
    // R2 checks the SHA-256 the PC signed; a changed or cut body is refused and nothing is kept
    await env.BUCKET.put(objectKey(pc, name), request.body, { sha256: declared, customMetadata: { dbSha256: meta.dbSha256, schema: String(meta.schema), app: meta.app } });
  } catch {
    throw new Fail(400, 'body_mismatch');
  }
  const kind = name.split('-')[0];
  await env.DB.prepare('INSERT INTO copies(pc, name, kind, size, sha256, db_sha256, schema, app, at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)')
    .bind(pc, name, kind, size, declared, meta.dbSha256, meta.schema, meta.app, now()).run();
  await prune(env, pc, kind);
  return reply(200, { ok: true, name });
}

async function pcCall(request, env, url, parts) {
  const { pc, body } = await pcAuth(request, env, url);
  const m = request.method;
  if (parts[1] === 'copies' && parts.length === 2 && m === 'GET') {
    if (body !== EMPTY) throw new Fail(401, 'unauthorised');
    const r = await env.DB.prepare('SELECT name, kind, size, sha256, db_sha256, schema, app, at FROM copies WHERE pc = ? ORDER BY name DESC').bind(pc).all();
    return reply(200, { pcId: pc, copies: (r.results || []).map((c) => ({ name: c.name, kind: c.kind, size: c.size, sha256: c.sha256, dbSha256: c.db_sha256, schema: c.schema, app: c.app, at: iso(c.at) })), keep: KEEP });
  }
  if (parts[1] === 'copies' && parts.length === 3 && NAME_RE.test(parts[2])) {
    if (m === 'PUT') return upload(request, env, pc, body, parts[2]);
    if (m === 'GET') {
      if (body !== EMPTY) throw new Fail(401, 'unauthorised');
      const row = await env.DB.prepare('SELECT sha256, db_sha256 FROM copies WHERE pc = ? AND name = ?').bind(pc, parts[2]).first();
      const obj = row && await env.BUCKET.get(objectKey(pc, parts[2]));
      if (!obj) throw new Fail(404, 'not_found');
      return new Response(obj.body, { status: 200, headers: { ...HEADERS, 'Content-Type': 'application/gzip', 'X-SM-Sha256': row.sha256, 'X-SM-Db-Sha256': row.db_sha256 } });
    }
  }
  throw new Fail(404, 'not_found');
}

export default {
  async fetch(request, env) {
    try {
      const url = new URL(request.url);
      const parts = url.pathname.split('/').filter(Boolean);
      if (url.pathname === '/' && request.method === 'GET') return reply(200, { name: 'Super Market cloud backup', ok: true, ready: !!masterKey(env) });
      if (parts[0] === 'pc' && parts[1] === 'register' && parts.length === 2 && request.method === 'POST') return await register(request, env);
      if (parts[0] === 'pc') return await pcCall(request, env, url, parts);
      return reply(404, { error: 'not_found' });
    } catch (e) {
      if (e instanceof Fail) {
        const retry = e.extra['Retry-After'];
        const extra = { ...e.extra }; delete extra['Retry-After'];
        return reply(e.status, { error: e.code, ...extra }, retry ? { 'Retry-After': retry } : undefined);
      }
      return reply(500, { error: 'server_error' });
    }
  },
  // Daily cleanup of replay marks and counters. A PC's copies are NEVER removed for being quiet:
  // a shop whose PC died is exactly the one that needs them. Only the owner removes a PC (see docs/CLOUD_BACKUP.md).
  async scheduled(_event, env) {
    await env.DB.batch([
      env.DB.prepare('DELETE FROM nonces WHERE at < ?').bind(now() - 2 * CLOCK_SKEW),
      env.DB.prepare('DELETE FROM rate WHERE window < ?').bind(now() - 2 * 86400),
      env.DB.prepare('DELETE FROM usage WHERE day < ?').bind(Math.floor(now() / 86400) - 2),
    ]);
  },
};
