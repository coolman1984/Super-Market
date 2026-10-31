// A small stand-in for Cloudflare D1 (copied from the Yousef gateway) on top of node:sqlite, for local runs and tests only (same calls: prepare/bind/run/first/all/batch).
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

class Statement {
  constructor(db, sql, args = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args) {
    // Match D1 rather than silently accepting node:sqlite's much larger limit.
    if (args.length > 100) throw new Error('D1 supports at most 100 bound parameters per query');
    return new Statement(this.db, this.sql, args.map((a) => (a === undefined ? null : a)));
  }
  _stmt() { return this.db.prepare(this.sql); }
  async run() {
    const r = this._stmt().run(...this.args);
    return { success: true, meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
  }
  async first(col) {
    const r = this._stmt().get(...this.args);
    if (!r) return null;
    return col ? r[col] : { ...r };
  }
  async all() {
    const rows = this._stmt().all(...this.args).map((r) => ({ ...r }));
    return { success: true, results: rows, meta: {} };
  }
}

export class D1 {
  constructor(path = ':memory:') {
    this.db = new DatabaseSync(path);
    this.db.exec('PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;');
    const here = dirname(fileURLToPath(import.meta.url));
    this.db.exec(readFileSync(join(here, '..', 'schema.sql'), 'utf8'));
  }
  prepare(sql) { return new Statement(this.db, sql); }
  async batch(stmts) {
    const out = [];
    this.db.exec('BEGIN');
    try { for (const s of stmts) out.push(await s.run()); this.db.exec('COMMIT'); } catch (e) { this.db.exec('ROLLBACK'); throw e; }
    return out;
  }
  close() { this.db.close(); }
}
