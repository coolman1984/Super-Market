"""Optional cloud copy of the shop (Cloudflare Worker + D1): backup, and a read-only feed for phones.
Standard library only; nothing here runs unless the owner connected the shop (tools/syncctl.py setup).

Rules (docs/CLOUD_SYNC.md):
* The shop PC's database stays the ONE source of truth. The cloud holds the latest copy of every row.
* Change capture: once enabled, triggers note every inserted/updated/deleted row in `sync_changes`.
  Posted rows never change, so "latest row wins" is exact; the few state columns (lot status, shift
  close...) are simply re-sent. Each row carries a version, and the cloud ignores an older one.
* Other devices never write rows. They send COMMANDS; the shop PC runs each one through its normal
  services (permissions, idempotency key, audit) and answers applied/rejected.
* Left out on purpose: sessions, derived stock balances (rebuilt by trigger), the print outbox, local
  sync state, and password hashes (restore asks the owner for new passwords).
* Training data never leaves the PC: enable() refuses anything but the real shop.
* Every request is signed (HMAC-SHA256, per-device secret) and carries a time and a one-time nonce.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import pathlib
import secrets
import sqlite3
import time
import urllib.error
import urllib.request

from .core import ShopError, add_days, now_iso

log = logging.getLogger("supermarket.sync")

CONFIG_NAME = "sync.json"
CAPTURE = "sync_cap_"
NEVER_SYNCED = {"sessions", "stock_balances", "outbox", "sync_changes", "sync_meta", "sqlite_sequence"}
LOCAL_SETTINGS = {"last_app_version"}
BATCH_KEYS = 200
BATCH_TIMEOUT = 40
REPORT_DAYS = 14                 # how many business days of daily figures phones can read
REPORT_EVERY = 60                # seconds between publications while the shop is busy
REPORT_REFRESH = 600             # and at least this often (expiry states move with the date)
USER_AGENT = "SuperMarket-CloudCopy/1"
RESTORE_LAST = ("barcodes", "day_closes", "day_corrections")   # triggers there need the other tables first


class CloudError(Exception):
    """A request to the cloud failed. `status` is 0 when the cloud could not be reached at all."""

    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status = status
        self.detail = detail or {}


# ---------------------------------------------------------------- change capture (inside the shop DB)
def synced_tables(db):
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            if r[0] not in NEVER_SYNCED]


def meta(db, key, default=None):
    row = db.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute("INSERT INTO sync_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _capture_sql(table):
    t = '"%s"' % table
    body = "INSERT INTO sync_changes(t,r) VALUES('%s', %%s.rowid);" % table
    return [
        "CREATE TRIGGER IF NOT EXISTS %si_%s AFTER INSERT ON %s BEGIN %s END" % (CAPTURE, table, t, body % "NEW"),
        "CREATE TRIGGER IF NOT EXISTS %su_%s AFTER UPDATE ON %s BEGIN %s END" % (CAPTURE, table, t, body % "NEW"),
        "CREATE TRIGGER IF NOT EXISTS %sd_%s AFTER DELETE ON %s BEGIN %s END" % (CAPTURE, table, t, body % "OLD"),
    ]


def ensure_capture(db):
    """Called at every start: when the cloud copy is on, make sure every table (also one a later
    version added) has its triggers. Costs nothing when it is off."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_meta'").fetchone():
        return
    if meta(db, "enabled") != "1":
        return
    have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ?", (CAPTURE + "%",))}
    for table in synced_tables(db):
        if "%si_%s" % (CAPTURE, table) not in have or "%sd_%s" % (CAPTURE, table) not in have:
            for sql in _capture_sql(table):
                db.execute(sql)


def enable(db, mode):
    """Turn change capture on and queue every existing row once (a full first copy)."""
    if mode != "shop":
        raise ShopError("sync_shop_only")          # training data never goes to the cloud
    db.execute("BEGIN IMMEDIATE")
    try:
        set_meta(db, "enabled", "1")
        for table in synced_tables(db):
            for sql in _capture_sql(table):
                db.execute(sql)
        if meta(db, "bootstrapped") != "1":
            db.execute("DELETE FROM sync_changes")
            for table in synced_tables(db):
                db.execute('INSERT INTO sync_changes(t,r) SELECT ?, rowid FROM "%s"' % table, (table,))
            set_meta(db, "bootstrapped", "1")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def disable(db):
    """Stop capturing. The cloud keeps what it has; turning it on again queues everything afresh."""
    db.execute("BEGIN IMMEDIATE")
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ?", (CAPTURE + "%",)).fetchall():
            db.execute('DROP TRIGGER "%s"' % name)
        set_meta(db, "enabled", "0")
        set_meta(db, "bootstrapped", "0")
        db.execute("DELETE FROM sync_changes")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- row <-> JSON
def _to_json(value):
    if isinstance(value, bytes):
        return {"$b64": base64.b64encode(value).decode()}
    return value


def _from_json(value):
    if isinstance(value, dict) and "$b64" in value:
        return base64.b64decode(value["$b64"])
    return value


def export_row(db, table, rid):
    """The row as the cloud stores it, or None when it no longer exists (a deletion)."""
    cur = db.execute('SELECT rowid AS _rid, * FROM "%s" WHERE rowid=?' % table, (rid,))
    row = cur.fetchone()
    if row is None:
        return None
    names = [d[0] for d in cur.description]
    data = {n: _to_json(v) for n, v in zip(names, row) if n != "_rid"}
    if table == "users":
        data.pop("pw_hash", None)       # passwords never leave the shop PC
        data.pop("pw_salt", None)
    return data


def is_local_row(table, data):
    return table == "settings" and data is not None and data.get("key") in LOCAL_SETTINGS


# ---------------------------------------------------------------- the cloud client
def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def sign(secret, method, target, stamp, nonce, body, store_id, device_id):
    message = "\n".join([method, target, str(stamp), nonce, _sha256(body), store_id, device_id])
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


class Cloud:
    """Signed HTTP calls to the Worker. `config` = {url, store_id, device_id, secret}."""

    def __init__(self, config, timeout=BATCH_TIMEOUT):
        self.url = config["url"].rstrip("/")
        self.store_id = config["store_id"]
        self.device_id = config["device_id"]
        self.secret = config["secret"]
        self.role = config.get("role")
        self.timeout = timeout

    def call(self, method, target, payload=None):
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        nonce = secrets.token_hex(16)
        stamp = int(time.time())
        headers = {"X-SM-Store": self.store_id, "X-SM-Device": self.device_id, "X-SM-Time": str(stamp),
                   "X-SM-Nonce": nonce, "Content-Type": "application/json",
                   "X-SM-Sig": sign(self.secret, method, target, stamp, nonce, body, self.store_id, self.device_id)}
        return _request(method, self.url + target, body if method != "GET" else None, headers, self.timeout)

    def push(self, records):
        return self.call("POST", "/v1/push", {"records": records})

    def pull(self, after=0, limit=300, table=None):
        target = "/v1/pull?after=%d&limit=%d" % (after, limit) + ("&table=" + table if table else "")
        return self.call("GET", target)

    def summary(self):
        return self.call("GET", "/v1/summary")

    def report(self, name, **query):
        """Read one published report (phones and the shop PC). name: days, day, stock, debts, day-closes."""
        qs = "".join(("&" if i else "?") + "%s=%s" % kv for i, kv in enumerate(query.items()))
        return self.call("GET", "/v1/reports/" + name + qs)

    def submit(self, key, kind, payload):
        return self.call("POST", "/v1/commands", {"key": key, "kind": kind, "payload": payload})

    def command(self, command_id):
        return self.call("GET", "/v1/commands/%d" % command_id)

    def commands(self):
        return self.call("GET", "/v1/commands")["commands"]

    def ack(self, command_id, status, result):
        return self.call("POST", "/v1/commands/ack", {"id": command_id, "status": status, "result": result})


def _request(method, url, body, headers, timeout):
    # Cloudflare's edge rejects the default "Python-urllib" agent with a 403, so every call names itself.
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b"{}")
        except ValueError:
            detail = {}
        raise CloudError(exc.code, detail.get("error") or ("HTTP %d" % exc.code), detail) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CloudError(0, "cannot reach the cloud: %s" % getattr(exc, "reason", exc)) from None


def pair(url, store_id, code, name):
    """Trade a one-time pairing code for this device's own secret."""
    answer = _request("POST", url.rstrip("/") + "/v1/pair", json.dumps({"store_id": store_id, "code": code, "name": name}).encode(),
                      {"Content-Type": "application/json"}, 30)
    return {"url": url.rstrip("/"), "store_id": answer["store_id"], "device_id": answer["device_id"],
            "secret": answer["secret"], "role": answer["role"]}


def admin_call(url, admin_key, method, path, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    return _request(method, url.rstrip("/") + path, body, {"X-SM-Admin": admin_key, "Content-Type": "application/json"}, 30)


def load_config(folder):
    path = pathlib.Path(folder) / CONFIG_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(folder, config):
    path = pathlib.Path(folder) / CONFIG_NAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)           # the device secret is only for this PC (no effect on Windows; the folder is the owner's)
    except OSError:
        pass
    os.replace(tmp, path)


# ---------------------------------------------------------------- reports for phones (read-only)
def _jsonable(doc):
    return json.loads(json.dumps(doc, ensure_ascii=False))


def build_reports(shop, owner):
    """Every figure a phone may read, computed here by the shop's own services (the cloud never
    calculates money). Keys are (table, row id) of the rep_* tables the Worker serves."""
    store = shop.store
    out = {}
    today = store.current_business_day()
    for i in range(REPORT_DAYS):
        day = add_days(today, -i)
        rep = shop.day_report(owner, day)
        for heavy in ("near_expiry", "held"):      # lot lists belong to the stock report, not to every day
            rep.pop(heavy, None)
        out[("rep_day", int(day.replace("-", "")))] = rep
    per = {}
    for r in shop.stock_report(owner):
        p = per.setdefault(r["product_id"], {"product_id": r["product_id"], "code": r["code"], "name_ar": r["name_ar"],
                                             "name_en": r["name_en"], "measure": r["measure"], "kind": r["kind"],
                                             "qty": 0, "available": 0, "near_expiry": 0, "expired": 0, "held": 0})
        p["qty"] += r["qty"]
        key = {"available": "available" if r["sellable"] else "held", "near_expiry": "near_expiry", "expired": "expired"}.get(r["state"], "held")
        p[key] += r["qty"]
    levels = {r["id"]: r["reorder_level"] for r in store.all("SELECT id, reorder_level FROM products")}
    products = sorted(per.values(), key=lambda p: (p["name_ar"], p["product_id"]))
    for p in products:
        p["reorder_level"] = levels.get(p["product_id"], 0)
        p["low"] = bool(p["reorder_level"] and p["available"] <= p["reorder_level"])
    out[("rep_stock", 1)] = {"products": products, "low": [p["code"] for p in products if p["low"]],
                             "expired": [p["code"] for p in products if p["expired"]],
                             "near_expiry": [p["code"] for p in products if p["near_expiry"]]}
    debts = store.all("""SELECT c.id, c.name, c.phone, c.credit_limit, SUM(e.amount) balance FROM customers c
        JOIN customer_entries e ON e.customer_id=c.id GROUP BY c.id HAVING SUM(e.amount)>0 ORDER BY balance DESC, c.id""")
    out[("rep_debts", 1)] = {"customers": debts, "total": sum(d["balance"] for d in debts), "count": len(debts)}
    return {k: _jsonable(v) for k, v in out.items()}


# ---------------------------------------------------------------- shop side: push, commands
class CloudSync:
    """Runs next to the shop (own database connection): sends changes, applies commands."""

    def __init__(self, folder, cloud=None, handlers=None):
        self.folder = pathlib.Path(folder)
        config = load_config(self.folder)
        if cloud is None and not config:
            raise ShopError("sync_not_connected")
        self.cloud = cloud or Cloud(config)
        self.handlers = {"ping": lambda payload: {"pong": True}}
        self.handlers.update(handlers or {})
        self.db = sqlite3.connect(str(self.folder / "shop.db"), timeout=10, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.last_error = None
        self.last_ok = None
        self._store = None
        self._published = {}
        self._pub_version = 0
        self._last_pub = 0.0
        self._dirty = False

    def close(self):
        self.db.close()
        if self._store is not None:
            self._store.close()
            self._store = None

    def publish_reports(self, force=False):
        """Send the phone reports whose figures changed. Returns how many rows were sent."""
        from .db import Store
        from .services import Shop
        if not force and time.time() - self._last_pub < REPORT_EVERY:
            return 0
        self._last_pub = time.time()
        if self._store is None:
            self._store = Store(self.folder, "shop")
        shop = Shop(self._store)
        row = self._store.one("SELECT * FROM users WHERE role='owner' AND active=1 ORDER BY id LIMIT 1")
        if row is None:
            return 0
        owner = shop.auth.public(row)
        owner["workstation"] = "cloud"
        docs = build_reports(shop, owner)
        stamp = now_iso()
        records = []
        for key, doc in docs.items():
            digest = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if self._published.get(key) == digest:
                continue
            self._pub_version = max(self._pub_version + 1, int(time.time() * 1000))
            records.append(({"t": key[0], "r": key[1], "v": self._pub_version, "d": dict(doc, published_at=stamp)}, key, digest))
        for i in range(0, len(records), BATCH_KEYS):
            chunk = records[i:i + BATCH_KEYS]
            self.cloud.push([c[0] for c in chunk])
            for _, key, digest in chunk:
                self._published[key] = digest
        return len(records)

    def pending(self):
        return self.db.execute("SELECT COUNT(*) FROM sync_changes").fetchone()[0]

    def push_once(self, limit=BATCH_KEYS):
        """Send up to `limit` changed rows. Returns how many were sent (0 = nothing waiting)."""
        db = self.db
        db.execute("BEGIN")                     # one read snapshot: the change list and the rows agree
        try:
            keys, seqs, versions = [], [], {}
            for seq, table, rid in db.execute("SELECT seq,t,r FROM sync_changes ORDER BY seq LIMIT ?", (limit * 10,)):
                key = (table, rid)
                if key not in versions:
                    if len(keys) >= limit:
                        break
                    keys.append(key)
                versions[key] = seq
                seqs.append(seq)
            records = []
            for table, rid in keys:
                data = export_row(db, table, rid)
                if is_local_row(table, data):
                    continue
                records.append({"t": table, "r": rid, "v": versions[(table, rid)], "d": data})
        finally:
            db.execute("COMMIT")
        if not seqs:
            return 0
        if records:
            self.cloud.push(records)
        for i in range(0, len(seqs), 500):
            chunk = seqs[i:i + 500]
            db.execute("DELETE FROM sync_changes WHERE seq IN (%s)" % ",".join("?" * len(chunk)), chunk)
        return len(keys)

    def drain(self, max_batches=1000):
        sent = 0
        for _ in range(max_batches):
            n = self.push_once()
            if not n:
                break
            sent += n
        return sent

    def process_commands(self):
        """Run what phones asked for, through the registry of known command kinds. Anything unknown,
        invalid or refused is answered 'rejected' with a reason; nothing is ever applied blindly."""
        done = 0
        for cmd in self.cloud.commands():
            handler = self.handlers.get(cmd["kind"])
            if handler is None:
                self.cloud.ack(cmd["id"], "rejected", {"code": "unknown_command"})
            else:
                try:
                    self.cloud.ack(cmd["id"], "applied", handler(cmd["payload"]) or {})
                except ShopError as exc:
                    self.cloud.ack(cmd["id"], "rejected", {"code": exc.code, "detail": exc.detail})
                except CloudError:
                    raise
                except Exception:   # never leak internals to a phone; the log has them
                    log.exception("command %s failed", cmd.get("id"))
                    self.cloud.ack(cmd["id"], "rejected", {"code": "failed"})
            done += 1
        return done

    def cycle(self):
        sent = self.drain()
        handled = self.process_commands()
        published = 0
        self._dirty = self._dirty or bool(sent)
        due = time.time() - self._last_pub
        if (self._dirty and due >= REPORT_EVERY) or due > REPORT_REFRESH or not self._last_pub:
            try:
                published = self.publish_reports(force=True)
                self._dirty = False
            except CloudError:
                raise
            except Exception:       # reports are extra: a failure here must never hold back the backup
                log.exception("publishing phone reports failed")
                self._last_pub = time.time()
        self.last_ok = time.time()
        self.last_error = None
        return {"sent": sent, "commands": handled, "pending": self.pending(), "reports": published}

    def run_forever(self, stop, interval=15, max_wait=600):
        """Background loop: retry with growing waits when the internet is down; never raises."""
        wait = interval
        while not stop.is_set():
            try:
                self.cycle()
                wait = interval
            except CloudError as exc:
                self.last_error = "%s (HTTP %s)" % (exc, exc.status) if exc.status else str(exc)
                if exc.status == 401 and "server_time" in exc.detail:
                    self.last_error = "this PC's clock is off by %d seconds" % abs(int(time.time()) - exc.detail["server_time"])
                wait = min(max_wait, wait * 2)
                log.warning("cloud sync: %s; next try in %ds", self.last_error, wait)
            except Exception:       # a sync problem must never reach the till
                self.last_error = "unexpected error (see server.log)"
                log.exception("cloud sync failed")
                wait = min(max_wait, wait * 2)
            stop.wait(wait)

    def status(self):
        cloud = self.cloud.summary()
        local = {}
        for table in synced_tables(self.db):
            local[table] = self.db.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        remote = {t["tbl"]: t["n"] for t in cloud["tables"]}
        differ = {t: {"here": local.get(t, 0), "cloud": remote.get(t, 0)}
                  for t in sorted(set(local) | set(remote)) if local.get(t, 0) != remote.get(t, 0)
                  and not (t == "settings" or t.startswith("rep_"))}
        return {"pending_here": self.pending(), "cloud_changes": cloud["seq"], "last_push": cloud["last_push"],
                "waiting_commands": cloud["pending_commands"], "tables_differ": differ,
                "complete": not self.pending() and not differ}


# ---------------------------------------------------------------- restore from the cloud
def _alias_pk(db, table):
    info = db.execute('PRAGMA table_info("%s")' % table).fetchall()
    pk = [r for r in info if r[5]]
    return len(pk) == 1 and pk[0][2].upper() == "INTEGER"


def restore_from_cloud(cloud, new_folder, progress=None):
    """Rebuild a shop folder from the cloud copy. Fills an EMPTY new folder only; the old one is
    never touched. Passwords are not in the cloud: every user must be given a new one with
    owner-recovery.bat. Returns the verification report."""
    from . import schema
    from .db import Store
    folder = pathlib.Path(new_folder)
    if folder.exists() and any(folder.iterdir()):
        raise ShopError("restore_target_not_empty")
    rows, after = [], 0
    while True:
        page = cloud.pull(after, 500)
        rows.extend(page["records"])
        after = page["next"]
        if progress:
            progress(len(rows))
        if not page["more"]:
            break
    versions = [r["d"]["version"] for r in rows if r["t"] == "schema_migrations" and r["d"]]
    if not versions:
        raise ShopError("cloud_copy_empty")
    if max(versions) > schema.SCHEMA_VERSION:
        raise ShopError("cloud_copy_newer_than_app", {"cloud": max(versions), "app": schema.SCHEMA_VERSION})
    store = Store(folder, "shop", upto=max(versions))     # same layout the copy was taken with; the normal upgrade follows
    try:
        db = store.db
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("BEGIN IMMEDIATE")
        try:
            known = set(synced_tables(db))
            latest = {}
            for r in rows:
                if r["t"] in known and r["d"] is not None:
                    latest[(r["t"], r["r"])] = r
            def order(r):
                t = r["t"]
                return (RESTORE_LAST.index(t) + 1 if t in RESTORE_LAST else 0, t, r["r"])
            ordered = sorted(latest.values(), key=order)
            alias = {t: _alias_pk(db, t) for t in known}
            for r in ordered:
                table, data = r["t"], {k: _from_json(v) for k, v in r["d"].items()}
                if table == "users":
                    data.update(pw_hash="!restored", pw_salt="00", must_change=1)
                if table == "schema_migrations":
                    continue
                cols = list(data)
                if not alias[table]:
                    cols = ["rowid"] + cols
                    data["rowid"] = r["r"]
                verb = "INSERT OR REPLACE" if table == "settings" else "INSERT"      # posted tables never replace a row
                db.execute('%s INTO "%s"(%s) VALUES(%s)' % (verb, table, ",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))),
                           [data[c] for c in cols])
            # the next local change must be newer than anything the cloud already holds
            top = max([r["v"] for r in rows if not r["t"].startswith("rep_")] or [0])      # rep_* versions are clock stamps, not change numbers
            db.execute("DELETE FROM sync_changes")
            if not db.execute("UPDATE sqlite_sequence SET seq=? WHERE name='sync_changes'", (top,)).rowcount:
                db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('sync_changes',?)", (top,))
            set_meta(db, "enabled", "1")          # keeps following the shop; this PC still has to join (tools/syncctl.py join)
            set_meta(db, "bootstrapped", "1")
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        problems = db.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        store.close()
    store = Store(folder, "shop")        # now upgrades to the current layout if the copy was older
    try:
        from .services import Shop
        report = Shop(store).reconcile()
        report["foreign_key_problems"] = len(problems)
        report["rows"] = len(latest)
        report["ok"] = bool(report["ok"] and not problems)
        return report
    finally:
        store.close()
