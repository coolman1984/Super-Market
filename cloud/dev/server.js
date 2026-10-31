// Local copy of the cloud service for development and tests: the real worker code + a D1 stand-in.
//   node dev/server.js        (PORT, MASTER_KEY, ADMIN_KEY, SYNC_DB are read from the environment)
import http from 'node:http';
import { fileURLToPath } from 'node:url';
import worker from '../src/worker.js';
import { D1 } from './d1.js';

export function makeEnv(extra = {}) {
  return { DB: new D1(process.env.SYNC_DB || ':memory:'), MASTER_KEY: process.env.MASTER_KEY || '', ADMIN_KEY: process.env.ADMIN_KEY || '', ...extra };
}

export function serve(env, port = 0) {
  const server = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const body = Buffer.concat(chunks);
    const headers = new Headers();
    for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v);
    headers.set('CF-Connecting-IP', headers.get('x-test-ip') || req.socket.remoteAddress || 'local');
    const request = new Request(`http://${req.headers.host}${req.url}`, { method: req.method, headers, body: ['GET', 'HEAD'].includes(req.method) ? undefined : body });
    const r = await worker.fetch(request, env);
    res.writeHead(r.status, Object.fromEntries(r.headers));
    res.end(Buffer.from(await r.arrayBuffer()));
  });
  return new Promise((ok) => server.listen(port, '127.0.0.1', () => ok(server)));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const s = await serve(makeEnv(), Number(process.env.PORT || 8788));
  console.log('READY ' + s.address().port);
}
