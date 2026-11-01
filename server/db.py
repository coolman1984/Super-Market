"""SQLite store: one shop folder = one database. Writes are serialized,
every money/stock command runs in one BEGIN IMMEDIATE transaction with an
idempotency record bound to a request fingerprint and an audit row.
"""

import hashlib
import json
import os
import pathlib
import sqlite3
import threading

from . import schema
from .core import Conflict, ShopError, add_days, business_day_at, now, now_iso, today
from .version import VERSION

DB_NAME = "shop.db"


class Store:
    def __init__(self, folder, mode="shop", timeout=10, upto=None):
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
            self.migrate([m for m in schema.MIGRATIONS if m[0] <= upto] if upto else None)
            self._check_mode()
            from . import cloudsync
            cloudsync.ensure_capture(self.db)   # no-op unless the owner connected the cloud copy
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
            # A new empty file has nothing to protect. An existing shop is copied
            # before a schema change and before a newer program opens it.
            pending = any(version > current for version, _name, _fn in migrations)
            if current > 0 and (pending or self._opened_with_version() != VERSION):
                self._pre_upgrade_copy(current)
            for version, name, fn in migrations:
                if version <= current:
                    continue
                self._run_schema(fn, version, name)
            # Version 10 was issued twice (PIN on main, the shortage column on
            # be00d4d). Re-apply both from the live tables whenever this open
            # is allowed to reach them, even if the number is already recorded.
            wanted = {version for version, _name, _fn in migrations}
            if 10 in wanted:
                self._run_schema(schema.m010)
            if 11 in wanted:
                self._run_schema(schema.m011)
            if self.schema_version() > 0:
                self._remember_opened_version()

    def _run_schema(self, fn, version=None, name=None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            fn(self.db)
            if version is not None:
                self.db.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (version, name, now_iso()))
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

    def _opened_with_version(self):
        try:
            row = self.db.execute("SELECT value FROM settings WHERE key='opened_with_version'").fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return row[0]

    def _remember_opened_version(self):
        try:
            self.db.execute(
                "INSERT INTO settings VALUES('opened_with_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(VERSION),))
        except sqlite3.OperationalError:
            return

    def _pre_upgrade_copy(self, current):
        """A consistent copy before any upgrade or migration. The SQLite backup
        API reads a snapshot (safe while WAL is on). The file is renamed into
        place only after the integrity check, so a half-written copy is never the one we keep."""
        folder = self.folder / "pre-upgrade"
        folder.mkdir(exist_ok=True)
        stamp = now_iso().replace(":", "").replace("-", "")[:15]            # 20261031T180912: seconds, so two upgrades never collide (L4)
        final = folder / ("schema-%d-%s.db" % (current, stamp))
        n = 1
        while final.exists():
            n += 1
            final = folder / ("schema-%d-%s-%d.db" % (current, stamp, n))
        partial = final.with_name(final.name + ".partial")
        target = sqlite3.connect(str(partial))
        try:
            self.db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ShopError("pre_upgrade_copy_failed")
        except BaseException:
            target.close()
            partial.unlink(missing_ok=True)
            raise
        target.close()
        os.replace(partial, final)

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

    def clock_behind(self):
        """The closed day the PC clock is behind (dead CMOS battery, wrong date), or None. Posting is refused then (M5)."""
        closed = self.db.execute("SELECT MAX(day) FROM day_closes").fetchone()[0]
        return closed if closed is not None and today() < closed else None

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
