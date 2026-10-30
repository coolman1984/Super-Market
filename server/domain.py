"""Small executable stock-domain foundation, not a production service.

Amounts are integer piastres; quantities are integer grams (g) or units (unit).
Individual quantities and costs must fit SQLite signed 64-bit integers.
Batch IDs are globally unique, with one receipt/production/return origin each.
Expiry dates are ISO dates, saleable through that date. Locations are opaque IDs.
Production accepts explicit raw/packaging batch portions and gram outputs only;
declared gram loss carries no stock or cost: all actual input cost goes to yield.
All gram inputs are food and must be dated for production. Output expiry must be
between today and the earliest food-input expiry, inclusive. Recipe-specific
shelf-life shortening within those bounds remains caller policy.
No transfers, permissions, migrations, tax, selling prices, or durable audit seal.
Use one Ledger per connection; SQLite serializes file writers. Busy/commit errors
may be retried by callers with the same key/payload, including uncertain commits.
"""

import datetime as dt
import json
import sqlite3
import threading


class DomainError(ValueError):
    """Invalid request, insufficient usable stock, or broken domain invariant."""


class Conflict(DomainError):
    """An idempotency key was already used with a different payload."""


def integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise DomainError("expected SQLite integer >= %s" % minimum)
    return value


def identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise DomainError("expected nonempty identifier")
    return value


def date(value):
    if not isinstance(value, str):
        raise DomainError("expected ISO date")
    try:
        if dt.date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError as exc:
        raise DomainError("expected ISO date") from exc
    return value


class Ledger:
    """SQLite append-only stock/cost ledger; public mutations are atomic.

    Successful keys return the original JSON-compatible result on exact replay.
    Failed requests reserve neither keys nor stock. Owns its connection; close it.
    Direct SQL insertion is trusted administration, not a supported domain API.
    """

    def __init__(self, path=":memory:", timeout=5):
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=timeout, isolation_level=None,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS operations(
          key TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS products(
          id TEXT PRIMARY KEY, unit TEXT NOT NULL CHECK(unit IN ('g','unit')));
        CREATE TABLE IF NOT EXISTS batches(
          id TEXT PRIMARY KEY, product TEXT NOT NULL REFERENCES products(id),
          expires TEXT, quarantine INTEGER NOT NULL CHECK(quarantine IN (0,1)));
        CREATE TABLE IF NOT EXISTS entries(
          op TEXT NOT NULL REFERENCES operations(key) DEFERRABLE INITIALLY DEFERRED,
          batch TEXT NOT NULL REFERENCES batches(id), location TEXT NOT NULL,
          qty INTEGER NOT NULL, cost INTEGER NOT NULL,
          PRIMARY KEY(op,batch,location),
          CHECK(typeof(qty)='integer' AND typeof(cost)='integer'),
          CHECK((qty>0 AND cost>=0) OR (qty<0 AND cost<=0)));
        CREATE TABLE IF NOT EXISTS edges(
          parent TEXT NOT NULL REFERENCES batches(id),
          child TEXT NOT NULL REFERENCES batches(id), PRIMARY KEY(parent,child));
        CREATE TABLE IF NOT EXISTS holds(
          batch TEXT PRIMARY KEY REFERENCES batches(id),
          op TEXT NOT NULL REFERENCES operations(key) DEFERRABLE INITIALLY DEFERRED);
        CREATE TABLE IF NOT EXISTS returns(
          op TEXT PRIMARY KEY REFERENCES operations(key) DEFERRABLE INITIALLY DEFERRED,
          sale TEXT NOT NULL REFERENCES operations(key),
          source TEXT NOT NULL REFERENCES batches(id), qty INTEGER NOT NULL,
          cost INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS nonnegative BEFORE INSERT ON entries
        WHEN NEW.qty + COALESCE((SELECT SUM(qty) FROM entries
          WHERE batch=NEW.batch AND location=NEW.location),0)<0
          OR NEW.cost + COALESCE((SELECT SUM(cost) FROM entries
          WHERE batch=NEW.batch AND location=NEW.location),0)<0
        BEGIN SELECT RAISE(ABORT,'negative stock or cost'); END;
        """)
        for table in ("operations", "products", "batches", "entries", "edges",
                      "holds", "returns"):
            for action in ("UPDATE", "DELETE"):
                self.db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_{action}
                    BEFORE {action} ON {table} BEGIN
                    SELECT RAISE(ABORT,'immutable posted data'); END""")

    def close(self):
        self.db.close()

    def _post(self, key, kind, payload, work):
        identifier(key)
        encoded = json.dumps([kind, payload], sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
                if old:
                    if old["payload"] != encoded:
                        raise Conflict("idempotency payload conflict")
                    result = json.loads(old["result"])
                else:
                    result = work()
                    self.db.execute("INSERT INTO operations VALUES(?,?,?)",
                                    (key, encoded, json.dumps(result, sort_keys=True)))
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def _batch(self, batch):
        row = self.db.execute("""SELECT b.*, p.unit FROM batches b
            JOIN products p ON p.id=b.product WHERE b.id=?""", (batch,)).fetchone()
        if row is None:
            raise DomainError("unknown batch")
        return row

    def balance(self, batch, location):
        """Return physical [quantity, carrying cost], including held stock."""
        with self._lock:
            row = self.db.execute("""SELECT COALESCE(SUM(qty),0), COALESCE(SUM(cost),0)
                FROM entries WHERE batch=? AND location=?""", (batch, location)).fetchone()
            return list(row)

    def _new_batch(self, batch, product, unit, expires, quarantine=False):
        identifier(batch), identifier(product)
        if unit not in ("g", "unit"):
            raise DomainError("unit must be g or unit")
        if expires is not None:
            date(expires)
        existing = self.db.execute("SELECT unit FROM products WHERE id=?", (product,)).fetchone()
        if existing and existing[0] != unit:
            raise DomainError("product unit cannot change")
        self.db.execute("INSERT OR IGNORE INTO products VALUES(?,?)", (product, unit))
        if self.db.execute("SELECT 1 FROM batches WHERE id=?", (batch,)).fetchone():
            raise DomainError("batch already exists")
        self.db.execute("INSERT INTO batches VALUES(?,?,?,?)",
                        (batch, product, expires, int(quarantine)))

    def _entry(self, key, batch, location, qty, cost):
        identifier(location)
        integer(abs(qty), 1), integer(abs(cost))
        self.db.execute("INSERT INTO entries VALUES(?,?,?,?,?)",
                        (key, batch, location, qty, cost))

    def _usable(self, batch, today):
        row = self._batch(batch)
        if row["quarantine"] or (row["expires"] and row["expires"] < today):
            raise DomainError("expired or quarantined batch")
        if self.db.execute("SELECT 1 FROM holds WHERE batch=?", (batch,)).fetchone():
            raise DomainError("recalled batch")
        return row

    def _consume(self, key, batch, location, qty, today):
        integer(qty, 1)
        self._usable(batch, today)
        available, cost = self.balance(batch, location)
        if qty > available:
            raise DomainError("insufficient usable stock")
        taken = cost * qty // available
        self._entry(key, batch, location, -qty, -taken)
        return taken

    def receipt(self, key, product, batch, location, qty, cost, unit="g", expires=None):
        """Receive a new batch at its actual total acquisition cost."""
        def work():
            integer(qty, 1), integer(cost)
            self._new_batch(batch, product, unit, expires)
            self._entry(key, batch, location, qty, cost)
            return {"batch": batch, "qty": qty, "cost": cost}
        return self._post(key, "receipt", [product, batch, location, qty, cost, unit, expires], work)

    def sale(self, key, product, location, qty, today):
        """FEFO sale; ties use batch ID, undated stock sorts last. Atomic fill only."""
        def work():
            integer(qty, 1), date(today), identifier(product), identifier(location)
            candidates = self.db.execute("""SELECT b.id FROM batches b
                WHERE b.product=? AND b.quarantine=0
                  AND (b.expires IS NULL OR b.expires>=?)
                  AND NOT EXISTS(SELECT 1 FROM holds h WHERE h.batch=b.id)
                ORDER BY b.expires IS NULL, b.expires, b.id""", (product, today)).fetchall()
            left, parts = qty, []
            for row in candidates:
                available = self.balance(row[0], location)[0]
                take = min(left, available)
                if take:
                    cost = self._consume(key, row[0], location, take, today)
                    parts.append({"batch": row[0], "qty": take, "cost": cost})
                    left -= take
                if not left:
                    break
            if left:
                raise DomainError("insufficient usable stock")
            return parts
        return self._post(key, "sale", [product, location, qty, today], work)

    def produce(self, key, inputs, outputs, loss, today):
        """Inputs: [batch, location, qty]; outputs: dicts with batch/product/location/
        qty and mandatory expires. Output unit is g. Unit inputs are packaging
        and may be undated. Every gram input is food and must have an expiry.
        Output expiry must be >= today and <= the earliest food-input expiry;
        recipe-specific shortening beyond this constraint is caller policy.
        Cost uses gram weights, floors then one piastre per batch in sorted order.
        All inputs link to every output, conservatively preserving full genealogy.
        """
        def work():
            integer(loss), date(today)
            if not isinstance(inputs, (list, tuple)) or not isinstance(outputs, (list, tuple)) or not inputs or not outputs:
                raise DomainError("inputs and outputs required")
            for portion in inputs:
                if not isinstance(portion, (list, tuple)) or len(portion) != 3:
                    raise DomainError("input portion must contain batch, location, quantity")
                identifier(portion[0]), identifier(portion[1]), integer(portion[2], 1)
            required = {"batch", "product", "location", "qty", "expires"}
            for output in outputs:
                if not isinstance(output, dict) or set(output) != required:
                    raise DomainError("output requires batch, product, location, quantity and expiry")
                identifier(output["batch"]), identifier(output["product"]), identifier(output["location"])
                integer(output["qty"], 1), date(output["expires"])
            seen, grams, total, food_expiries = set(), 0, 0, []
            for batch, location, qty in inputs:
                if (batch, location) in seen:
                    raise DomainError("duplicate input portion")
                seen.add((batch, location))
                integer(qty, 1)
                origin = self._batch(batch)
                if origin["unit"] == "g":
                    if origin["expires"] is None:
                        raise DomainError("food input expiry required")
                    food_expiries.append(origin["expires"])
                    grams += qty
                total += self._consume(key, batch, location, qty, today)
            if not food_expiries:
                raise DomainError("gram food input required")
            earliest = min(food_expiries)
            for output in outputs:
                if set(output) - {"batch", "product", "location", "qty", "expires"}:
                    raise DomainError("unknown output field")
                integer(output["qty"], 1)
                expires = date(output.get("expires"))
                if not today <= expires <= earliest:
                    raise DomainError("output expiry outside food-input bounds")
            ordered = sorted(outputs, key=lambda o: o["batch"])
            yield_qty = sum(o["qty"] for o in ordered)
            if grams != yield_qty + loss:
                raise DomainError("gram mass must equal yield plus explicit loss")
            costs = [total * o["qty"] // yield_qty for o in ordered]
            remainder = total - sum(costs)
            result = []
            for index, output in enumerate(ordered):
                batch = output["batch"]
                cost = costs[index] + (index < remainder)
                self._new_batch(batch, output["product"], "g", output["expires"])
                self._entry(key, batch, output["location"], output["qty"], cost)
                for parent in sorted({p[0] for p in inputs}):
                    self.db.execute("INSERT INTO edges VALUES(?,?)", (parent, batch))
                result.append({"batch": batch, "qty": output["qty"], "cost": cost})
            return result
        return self._post(key, "production", [inputs, outputs, loss, today], work)

    def customer_return(self, key, sale, source, batch, location, qty):
        """Return part of a sold batch into a new, permanently quarantined batch.
        Cumulative quantities are capped at that sale; actual sold cost is restored.
        """
        def work():
            integer(qty, 1)
            operation = self.db.execute("SELECT payload FROM operations WHERE key=?", (sale,)).fetchone()
            if not operation or json.loads(operation[0])[0] != "sale":
                raise DomainError("original sale required")
            sold = self.db.execute("SELECT -SUM(qty), -SUM(cost) FROM entries WHERE op=? AND batch=?",
                                   (sale, source)).fetchone()
            previous = self.db.execute("""SELECT COALESCE(SUM(qty),0), COALESCE(SUM(cost),0)
                FROM returns WHERE sale=? AND source=?""", (sale, source)).fetchone()
            if sold[0] is None or qty + previous[0] > sold[0]:
                raise DomainError("return exceeds sold quantity")
            cost = sold[1] * (previous[0] + qty) // sold[0] - previous[1]
            origin = self._batch(source)
            self._new_batch(batch, origin["product"], origin["unit"], origin["expires"], True)
            self._entry(key, batch, location, qty, cost)
            self.db.execute("INSERT INTO edges VALUES(?,?)", (source, batch))
            self.db.execute("INSERT INTO returns VALUES(?,?,?,?,?)", (key, sale, source, qty, cost))
            return {"batch": batch, "qty": qty, "cost": cost}
        return self._post(key, "return", [sale, source, batch, location, qty], work)

    def descendants(self, batch):
        """Return sorted transitive descendants, including the specified batch."""
        with self._lock:
            self._batch(batch)
            return [r[0] for r in self.db.execute("""WITH RECURSIVE tree(id) AS (
                SELECT ? UNION SELECT e.child FROM edges e JOIN tree ON e.parent=tree.id)
                SELECT id FROM tree ORDER BY id""", (batch,))]

    def recall(self, key, batch):
        """Append holds for this batch and all existing descendants; return IDs.
        Held inputs cannot be consumed, so new production cannot extend the tree.
        """
        def work():
            affected = self.descendants(batch)
            for item in affected:
                self.db.execute("INSERT OR IGNORE INTO holds VALUES(?,?)", (item, key))
            return affected
        return self._post(key, "recall", [batch], work)
