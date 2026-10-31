// Local cloud backup for development and tests: the real worker code + the D1 and R2 stand-ins.
//   node dev/server.js        (PORT and MASTER_KEY are read from the environment)
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import worker from '../src/worker.js';
import { D1 } from './d1.js';
import { R2 } from './r2.js';

export function makeEnv(extra = {}) {
  return { DB: new D1(':memory:'), BUCKET: new R2(), MASTER_KEY: process.env.MASTER_KEY || '', ...extra };
}

export function serve(env, port = 0) {
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = Buffer.concat(chunks);
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v);
    // DEV_EACH_CALL_NEW_IP=1 (tests): every call looks like another shop, so the per-address limit does not stop a test of many PCs
    headers.set('CF-Connecting-IP', process.env.DEV_EACH_CALL_NEW_IP === '1' ? 'test-' + Math.random() : (req.socket.remoteAddress || 'local'));
    const request = new Request(`http://${req.headers.host}${req.url}`, { method: req.method, headers, body: ['GET', 'HEAD'].includes(req.method) ? undefined : body, duplex: 'half' });
    const r = await worker.fetch(request, env);
    res.writeHead(r.status, Object.fromEntries(r.headers));
    res.end(Buffer.from(await r.arrayBuffer()));
  });
  return new Promise((ok) => server.listen(port, '127.0.0.1', () => ok(server)));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const s = await serve(makeEnv(), Number(process.env.PORT || 0));
  console.log('READY ' + s.address().port);
}
