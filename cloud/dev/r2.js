// A small stand-in for Cloudflare R2 (put/get/delete/list) for local runs and tests only.
// Like R2, put() with a sha256 checks the bytes and refuses a body that does not match.
import { createHash } from 'node:crypto';

export class R2 {
  constructor() { this.objects = new Map(); }
  async put(key, body, opts = {}) {
    const bytes = body instanceof Uint8Array ? body : new Uint8Array(await new Response(body).arrayBuffer());
    if (opts.sha256 && createHash('sha256').update(bytes).digest('hex') !== String(opts.sha256)) throw new Error('The SHA-256 checksum you specified did not match');
    this.objects.set(key, { bytes, customMetadata: opts.customMetadata || {} });
    return { key, size: bytes.length };
  }
  async get(key) {
    const o = this.objects.get(key);
    if (!o) return null;
    return { body: new Blob([o.bytes]).stream(), size: o.bytes.length, customMetadata: o.customMetadata };
  }
  async delete(key) { this.objects.delete(key); }
  async list({ prefix = '' } = {}) { return { objects: [...this.objects.keys()].filter((k) => k.startsWith(prefix)).map((key) => ({ key })) }; }
}
