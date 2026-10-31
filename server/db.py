"""SQLite store: one shop folder = one database. Writes are serialized,
every money/stock command runs in one BEGIN IMMEDIATE transaction with an
idempotency record bound to a request fingerprint and an audit row.
"""

import hashlib
import json
import pathlib
import sqlite3
import threading

from . import schema
from .core import Conflict, ShopError, add_days, business_day_at, now, now_iso, today

DB_NAME = "shop.db"


class Store:
    def __init__(self, folder, mode="shop", timeout=10):
        self.folder = pathlib.Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / DB_NAME
        self.mode = mode
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=%d" % (timeout * 1000))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        try:
            self.migrate()
            self._check_mode()
        except BaseException:
            self.db.close()  # never leave the file locked (Windows) after a refusal
            raise

    # ---------- schema ----------
    def schema_version(self):
        exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        if not exists:
            return 0
        return self.db.execute("SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]

    def migrate(self, migrations=None):
        migrations = migrations or schema.MIGRATIONS
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations("
                            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
            current = self.schema_version()
            newest = migrations[-1][0]
            if current > newest:
                raise ShopError("database_newer_than_app", {"db": current, "app": newest})
            if 0 < current < newest:
                self._pre_upgrade_copy(current)
            for version, name, fn in migrations:
                if version <= current:
                    continue
                self.db.execute("BEGIN IMMEDIATE")
                try:
                    fn(self.db)
                    self.db.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (version, name, now_iso()))
                    self.db.execute("COMMIT")
                except BaseException:
                    if self.db.in_transaction:
                        self.db.execute("ROLLBACK")
                    raise

    def _pre_upgrade_copy(self, current):
        """Upgrade rule: a verified copy exists before any migration runs."""
        folder = self.folder / "pre-upgrade"
        folder.mkdir(exist_ok=True)
        target_path = folder / ("schema-%d-%s.db" % (current, now_iso().replace(":", "")[:15]))
        target = sqlite3.connect(str(target_path))
        try:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ShopError("pre_upgrade_copy_failed")
        finally:
            target.close()

    def _check_mode(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()
        if row is None:
            self.db.execute("INSERT INTO settings VALUES('mode',?)", (self.mode,))
        elif row[0] != self.mode:
            # training data never opens as a shop and vice versa (A04)
            raise ShopError("wrong_data_folder", {"folder_mode": row[0], "requested": self.mode})

    def close(self):
        self.db.close()

    # ---------- helpers ----------
    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def day_start_hour(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='day_start_hour'").fetchone()
        return json.loads(row[0]) if row else 4

    def current_business_day(self):
        """Today's business day for reading (never refuses, unlike posting)."""
        try:
            return self.business_day_for(None)
        except ShopError:
            return today()

    def business_day_for(self, user_id=None):
        """The business day a posting belongs to (gap row 15):
        1. the person's open shift: the day the shift was opened on (a sale at 01:30 in a
           shift opened at 18:00 is that evening's);
        2. otherwise the shop's day-start hour (default 4 a.m.);
        3. a closed day takes nothing more: work after the close starts the next day; a
           clock set back BEFORE the closed day is refused, never posted into the past."""
        closed = self.db.execute("SELECT MAX(day) FROM day_closes").fetchone()[0]
        if closed is not None and today() < closed:          # the clock is behind a closed day: refuse, for everyone
            raise ShopError("day_closed", {"day": closed})
        if user_id is not None:
            row = self.db.execute("SELECT COALESCE(business_day, substr(opened_at,1,10)) FROM shifts WHERE user_id=? AND closed_at IS NULL",
                                  (user_id,)).fetchone()
            if row:
                return row[0]
        day = business_day_at(now(), self.day_start_hour())
        if closed is not None and day <= closed:
            day = add_days(closed, 1)
        return day

    def scalar(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return row[0] if row else None

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row and key != "mode" else (row[0] if row else default)

    def next_seq(self, name):
        self.db.execute("INSERT INTO seq VALUES(?,0) ON CONFLICT(name) DO NOTHING", (name,))
        self.db.execute("UPDATE seq SET value=value+1 WHERE name=?", (name,))
        return self.scalar("SELECT value FROM seq WHERE name=?", (name,))

    def audit(self, actor, action, ref=None, detail=None):
        self.db.execute("INSERT INTO audit(at,user_id,workstation,action,ref,detail) VALUES(?,?,?,?,?,?)",
                        (now_iso(), actor.get("id") if actor else None,
                         actor.get("workstation") if actor else None, action, ref,
                         json.dumps(detail, sort_keys=True, ensure_ascii=False) if detail else None))

    def tx(self, work):
        """Plain write transaction (settings, master data)."""
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                result = work()
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def command(self, kind, key, payload, actor, work):
        """Idempotent command: same key + same payload returns the stored
        result; same key + other payload is a conflict; failures keep nothing."""
        if not isinstance(key, str) or not 8 <= len(key) <= 80:
            raise ShopError("bad_request_key")
        fingerprint = hashlib.sha256(json.dumps([kind, payload], sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT o.fingerprint, r.result, o.user_id, o.workstation FROM ops o "
                                      "JOIN op_results r ON r.op_id=o.id WHERE o.kind=? AND o.key=?",
                                      (kind, key)).fetchone()
                if old:
                    if old[0] != fingerprint:
                        raise Conflict("request_key_reused")
                    # a replay answers only the user and till that created the operation
                    if old[2] != actor["id"] or old[3] != actor.get("workstation"):
                        raise Conflict("request_key_other_user")
                    self.db.execute("COMMIT")
                    result = json.loads(old[1])
                    result["replayed"] = True
                    return result
                day = self.business_day_for(actor["id"])
                cur = self.db.execute(
                    "INSERT INTO ops(kind,key,fingerprint,user_id,workstation,at,business_day) VALUES(?,?,?,?,?,?,?)",
                    (kind, key, fingerprint, actor["id"], actor.get("workstation"), now_iso(), day))
                op_id = cur.lastrowid
                result = work(op_id)
                result["op"] = op_id
                self._store_result(op_id, result)
                self.audit(actor, kind, str(op_id), {"key": key})
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def _store_result(self, op_id, result):
        self.db.execute("INSERT INTO op_results(op_id, result) VALUES(?,?)",
                        (op_id, json.dumps(result, sort_keys=True, ensure_ascii=False)))
