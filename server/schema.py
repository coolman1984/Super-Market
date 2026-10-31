"""Numbered schema migrations. Never edit a shipped migration; append a new one.

Posted documents are append-only (UPDATE/DELETE triggers abort). The few
mutable state columns (lot status, pack status, shift close, count status,
outbox status, user lockout) have column-guarded triggers.
"""

import sqlite3

M001 = r"""
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE users(
  id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL, role TEXT NOT NULL,
  pw_hash TEXT NOT NULL, pw_salt TEXT NOT NULL, pw_iter INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, failed INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT, created_at TEXT NOT NULL);
CREATE TABLE sessions(
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  workstation TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL);

CREATE TABLE ops(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
  fingerprint TEXT NOT NULL, user_id INTEGER REFERENCES users(id),
  workstation TEXT, at TEXT NOT NULL, UNIQUE(kind, key));
CREATE TABLE op_results(op_id INTEGER PRIMARY KEY REFERENCES ops(id), result TEXT NOT NULL);
CREATE TABLE audit(
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, workstation TEXT,
  action TEXT NOT NULL, ref TEXT, detail TEXT);

CREATE TABLE locations(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name_ar TEXT NOT NULL,
  name_en TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('shelf','store','display_fridge','prep_fridge','freezer','quarantine','waste')),
  sellable INTEGER NOT NULL CHECK(sellable IN (0,1)));

CREATE TABLE products(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name_ar TEXT NOT NULL,
  name_en TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('grocery','raw_meat','prepared','ingredient','packaging')),
  measure TEXT NOT NULL CHECK(measure IN ('unit','g')),
  needs_expiry INTEGER NOT NULL CHECK(needs_expiry IN (0,1)),
  reorder_level INTEGER NOT NULL DEFAULT 0, tax_status TEXT NOT NULL DEFAULT 'unverified',
  allergens TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE packagings(
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
  name_ar TEXT NOT NULL, name_en TEXT NOT NULL,
  factor_num INTEGER NOT NULL CHECK(factor_num>0), factor_den INTEGER NOT NULL CHECK(factor_den>0),
  created_at TEXT NOT NULL);
CREATE TABLE barcodes(
  code TEXT PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
  packaging_id INTEGER REFERENCES packagings(id), created_at TEXT NOT NULL,
  CHECK(typeof(code)='text'));
CREATE TABLE prices(
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
  price INTEGER NOT NULL CHECK(typeof(price)='integer' AND price>=0),
  effective_at TEXT NOT NULL, user_id INTEGER, op_id INTEGER);

CREATE TABLE suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, created_at TEXT NOT NULL);
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT,
  credit_limit INTEGER NOT NULL DEFAULT 0 CHECK(credit_limit>=0), created_at TEXT NOT NULL);

CREATE TABLE lots(
  id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
  lot_no TEXT NOT NULL UNIQUE, supplier_lot TEXT,
  origin TEXT NOT NULL CHECK(origin IN ('receipt','production','return','count')),
  origin_op INTEGER NOT NULL REFERENCES ops(id), expires TEXT,
  status TEXT NOT NULL CHECK(status IN ('released','held','recalled')),
  allergens TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE lot_edges(parent INTEGER NOT NULL REFERENCES lots(id),
  child INTEGER NOT NULL REFERENCES lots(id), PRIMARY KEY(parent, child));
CREATE TABLE lot_events(id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL REFERENCES lots(id),
  status TEXT NOT NULL, reason TEXT NOT NULL, op_id INTEGER NOT NULL REFERENCES ops(id), at TEXT NOT NULL);

CREATE TABLE stock_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT, op_id INTEGER NOT NULL REFERENCES ops(id),
  kind TEXT NOT NULL, reason TEXT, lot_id INTEGER NOT NULL REFERENCES lots(id),
  location_id INTEGER NOT NULL REFERENCES locations(id),
  qty INTEGER NOT NULL, cost INTEGER NOT NULL, at TEXT NOT NULL,
  CHECK(typeof(qty)='integer' AND typeof(cost)='integer'),
  CHECK((qty>0 AND cost>=0) OR (qty<0 AND cost<=0) OR (qty=0 AND cost<>0)));
CREATE TABLE stock_balances(
  lot_id INTEGER NOT NULL REFERENCES lots(id), location_id INTEGER NOT NULL REFERENCES locations(id),
  qty INTEGER NOT NULL CHECK(qty>=0), cost INTEGER NOT NULL CHECK(cost>=0),
  PRIMARY KEY(lot_id, location_id));
CREATE TRIGGER stock_balance_apply AFTER INSERT ON stock_entries BEGIN
  UPDATE stock_balances SET qty=qty+NEW.qty, cost=cost+NEW.cost
    WHERE lot_id=NEW.lot_id AND location_id=NEW.location_id;
  INSERT INTO stock_balances(lot_id, location_id, qty, cost)
    SELECT NEW.lot_id, NEW.location_id, NEW.qty, NEW.cost WHERE NOT EXISTS(
      SELECT 1 FROM stock_balances WHERE lot_id=NEW.lot_id AND location_id=NEW.location_id);
END;
CREATE TRIGGER stock_balance_cost_zero AFTER UPDATE ON stock_balances
WHEN NEW.qty=0 AND NEW.cost<>0 BEGIN SELECT RAISE(ABORT,'cost_without_stock'); END;

CREATE TABLE receipts(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id),
  supplier_id INTEGER REFERENCES suppliers(id), supplier_invoice TEXT, invoice_date TEXT,
  goods_total INTEGER NOT NULL, invoice_discount INTEGER NOT NULL, extra_cost INTEGER NOT NULL,
  total INTEGER NOT NULL, paid INTEGER NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT);
CREATE TABLE receipt_lines(
  id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL REFERENCES receipts(id),
  product_id INTEGER NOT NULL REFERENCES products(id), packaging_id INTEGER REFERENCES packagings(id),
  packs INTEGER, paid_qty INTEGER NOT NULL, free_qty INTEGER NOT NULL, rejected_qty INTEGER NOT NULL,
  gross_g INTEGER, tare_g INTEGER, line_cost INTEGER NOT NULL, line_discount INTEGER NOT NULL,
  landed_cost INTEGER NOT NULL, lot_id INTEGER NOT NULL REFERENCES lots(id), reject_reason TEXT);

CREATE TABLE supplier_entries(id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  op_id INTEGER NOT NULL REFERENCES ops(id), kind TEXT NOT NULL, amount INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TABLE customer_entries(id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id),
  op_id INTEGER NOT NULL REFERENCES ops(id), kind TEXT NOT NULL, amount INTEGER NOT NULL, at TEXT NOT NULL,
  reverses INTEGER UNIQUE REFERENCES customer_entries(id));

CREATE TABLE shifts(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), workstation TEXT NOT NULL,
  opened_at TEXT NOT NULL, opening_float INTEGER NOT NULL CHECK(opening_float>=0),
  closed_at TEXT, counted INTEGER, expected INTEGER, difference INTEGER, close_note TEXT,
  closed_by INTEGER);
CREATE UNIQUE INDEX one_open_shift ON shifts(user_id) WHERE closed_at IS NULL;
CREATE TABLE cash_entries(
  id INTEGER PRIMARY KEY, shift_id INTEGER NOT NULL REFERENCES shifts(id),
  op_id INTEGER NOT NULL REFERENCES ops(id),
  kind TEXT NOT NULL CHECK(kind IN ('float','sale','refund','expense','drop','deposit','debt_collect','supplier_pay','owner_draw')),
  amount INTEGER NOT NULL, note TEXT, at TEXT NOT NULL);

CREATE TABLE sales(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), no TEXT NOT NULL UNIQUE,
  shift_id INTEGER NOT NULL REFERENCES shifts(id), user_id INTEGER NOT NULL REFERENCES users(id),
  customer_id INTEGER REFERENCES customers(id), at TEXT NOT NULL,
  gross INTEGER NOT NULL, line_discounts INTEGER NOT NULL, invoice_discount INTEGER NOT NULL,
  total INTEGER NOT NULL, cash_tendered INTEGER NOT NULL, change_given INTEGER NOT NULL,
  approved_by INTEGER REFERENCES users(id), training INTEGER NOT NULL);
CREATE TABLE sale_lines(
  id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id),
  product_id INTEGER NOT NULL REFERENCES products(id), qty INTEGER NOT NULL CHECK(qty>0),
  unit_price INTEGER NOT NULL, price_basis TEXT NOT NULL, gross INTEGER NOT NULL,
  line_discount INTEGER NOT NULL, invoice_discount_share INTEGER NOT NULL, net INTEGER NOT NULL,
  cost INTEGER NOT NULL, pack_id INTEGER REFERENCES packs(id), scanned TEXT);
CREATE TABLE sale_allocations(
  sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id), lot_id INTEGER NOT NULL REFERENCES lots(id),
  location_id INTEGER NOT NULL REFERENCES locations(id), qty INTEGER NOT NULL, cost INTEGER NOT NULL,
  PRIMARY KEY(sale_line_id, lot_id, location_id));
CREATE TABLE payments(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL REFERENCES ops(id), sale_id INTEGER REFERENCES sales(id),
  method TEXT NOT NULL CHECK(method IN ('cash','card','wallet','transfer','credit')),
  amount INTEGER NOT NULL, reference TEXT, state TEXT NOT NULL CHECK(state IN ('settled','pending')),
  at TEXT NOT NULL);
CREATE TABLE payment_confirmations(payment_id INTEGER PRIMARY KEY REFERENCES payments(id),
  op_id INTEGER NOT NULL REFERENCES ops(id), outcome TEXT NOT NULL CHECK(outcome IN ('confirmed','rejected')),
  user_id INTEGER NOT NULL, at TEXT NOT NULL);

CREATE TABLE packs(
  id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, product_id INTEGER NOT NULL REFERENCES products(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id), net_g INTEGER NOT NULL CHECK(net_g>0),
  tare_g INTEGER NOT NULL CHECK(tare_g>=0), meat_g INTEGER, price INTEGER NOT NULL,
  price_per_kg INTEGER, packed_at TEXT NOT NULL, expires TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('available','sold','returned')),
  sale_line_id INTEGER REFERENCES sale_lines(id), label_version INTEGER NOT NULL DEFAULT 1);

CREATE TABLE returns(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id),
  sale_id INTEGER NOT NULL REFERENCES sales(id), shift_id INTEGER REFERENCES shifts(id),
  reason TEXT NOT NULL CHECK(reason IN ('quality','change_of_mind','wrong_item','other')),
  method TEXT NOT NULL CHECK(method IN ('cash','credit_note','original')),
  amount INTEGER NOT NULL, user_id INTEGER NOT NULL, approved_by INTEGER, at TEXT NOT NULL, note TEXT);
CREATE TABLE return_lines(
  return_id INTEGER NOT NULL REFERENCES returns(id), sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
  qty INTEGER NOT NULL CHECK(qty>0), amount INTEGER NOT NULL, cost INTEGER NOT NULL,
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  disposition TEXT NOT NULL CHECK(disposition IN ('quarantine','restock')),
  PRIMARY KEY(return_id, sale_line_id));

CREATE TABLE recipes(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL UNIQUE REFERENCES products(id),
  created_at TEXT NOT NULL);
CREATE TABLE recipe_versions(
  id INTEGER PRIMARY KEY, recipe_id INTEGER NOT NULL REFERENCES recipes(id), version INTEGER NOT NULL,
  shelf_life_days INTEGER NOT NULL CHECK(shelf_life_days>=0), allergens TEXT NOT NULL,
  target_pack_g INTEGER, loss_tolerance_permille INTEGER NOT NULL, note TEXT,
  approved_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
  UNIQUE(recipe_id, version));
CREATE TABLE recipe_components(
  version_id INTEGER NOT NULL REFERENCES recipe_versions(id), product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK(qty>0), role TEXT NOT NULL CHECK(role IN ('meat','food','packaging')),
  PRIMARY KEY(version_id, product_id));

CREATE TABLE productions(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id),
  kind TEXT NOT NULL CHECK(kind IN ('cutting','recipe')), recipe_version_id INTEGER REFERENCES recipe_versions(id),
  input_cost INTEGER NOT NULL, food_in_g INTEGER NOT NULL, output_g INTEGER NOT NULL,
  natural_loss_g INTEGER NOT NULL, abnormal_loss_g INTEGER NOT NULL, abnormal_loss_cost INTEGER NOT NULL,
  cost_method TEXT NOT NULL, approved_by INTEGER, user_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT);

CREATE TABLE counts(
  id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES locations(id),
  cutoff_entry INTEGER NOT NULL, opened_by INTEGER NOT NULL, opened_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('open','approved','cancelled')),
  approved_by INTEGER, approved_at TEXT, approve_op INTEGER);
CREATE TABLE count_lines(
  id INTEGER PRIMARY KEY, count_id INTEGER NOT NULL REFERENCES counts(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id), counted INTEGER NOT NULL CHECK(counted>=0),
  seen_entry INTEGER NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL);

CREATE TABLE recalls(id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id), reason TEXT NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL);

CREATE TABLE scanner_profiles(
  id INTEGER PRIMARY KEY, workstation TEXT NOT NULL, user_id INTEGER REFERENCES users(id),
  name TEXT NOT NULL, config TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(workstation, user_id));
CREATE TABLE label_templates(id INTEGER PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE outbox(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE seq(name TEXT PRIMARY KEY, value INTEGER NOT NULL);

CREATE INDEX lots_product ON lots(product_id, status, expires);
CREATE INDEX entries_lot ON stock_entries(lot_id, location_id);
CREATE INDEX sale_lines_sale ON sale_lines(sale_id);
CREATE INDEX allocations_lot ON sale_allocations(lot_id);
CREATE INDEX barcodes_product ON barcodes(product_id);
"""

APPEND_ONLY = ["ops", "op_results", "audit", "stock_entries", "receipts", "receipt_lines", "supplier_entries",
               "customer_entries", "cash_entries", "sales", "sale_lines", "sale_allocations",
               "payments", "payment_confirmations", "returns", "return_lines", "recipe_versions",
               "recipe_components", "productions", "count_lines", "recalls", "lot_events",
               "lot_edges", "prices"]

# table -> columns that may change after insert; everything else is frozen
GUARDED = {
    "lots": ["status"],
    "packs": ["status", "sale_line_id"],
    "shifts": ["closed_at", "counted", "expected", "difference", "close_note", "closed_by"],
    "counts": ["status", "approved_by", "approved_at", "approve_op"],
    "users": ["display_name", "role", "pw_hash", "pw_salt", "pw_iter", "active", "failed", "locked_until"],
}


def _immutability():
    sql = []
    for table in APPEND_ONLY:
        for action in ("UPDATE", "DELETE"):
            sql.append(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                       f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;")
    return "\n".join(sql) + "\n"


def _guards(conn):
    sql = []
    for table, mutable in GUARDED.items():
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        frozen = [c for c in cols if c not in mutable]
        cond = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in frozen)
        sql.append(f"CREATE TRIGGER {table}_frozen BEFORE UPDATE ON {table} WHEN {cond} "
                   f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;")
        sql.append(f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                   f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;")
    return "\n".join(sql) + "\n"


def run_script(conn, sql):
    """executescript() commits implicitly; run statements one by one so a
    migration stays inside its transaction."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            conn.execute(buffer)
            buffer = ""
    if buffer.strip():
        raise ValueError("incomplete SQL statement in migration")


def m001(conn):
    run_script(conn, M001)
    run_script(conn, _immutability())
    run_script(conn, _guards(conn))


MIGRATIONS = [(1, "core shop schema", m001)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 2
# Independent audit 30 Oct 2026 (docs/INDEPENDENT_AUDIT_20261030.md).
# Adds tables/columns only; no posted row is changed except the one-time
# backfill of the new packs.location_id from each pack's production entry.
M002 = r"""
CREATE TABLE refund_allocations(
  return_id INTEGER NOT NULL REFERENCES returns(id),
  method TEXT NOT NULL CHECK(method IN ('cash','card','wallet','transfer','credit_cancel','store_credit')),
  amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
  PRIMARY KEY(return_id, method));
CREATE TABLE credit_allocations(
  entry_id INTEGER NOT NULL REFERENCES customer_entries(id), sale_id INTEGER NOT NULL REFERENCES sales(id),
  amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount<>0), PRIMARY KEY(entry_id, sale_id));
CREATE TABLE safe_entries(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL REFERENCES ops(id),
  kind TEXT NOT NULL CHECK(kind IN ('drop','supplier_pay','deposit','withdrawal')),
  amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount<>0), note TEXT, at TEXT NOT NULL);
CREATE TABLE recall_lifts(
  recall_id INTEGER PRIMARY KEY REFERENCES recalls(id), op_id INTEGER NOT NULL REFERENCES ops(id),
  reason TEXT NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TABLE recipe_alternatives(
  version_id INTEGER NOT NULL REFERENCES recipe_versions(id),
  component_product_id INTEGER NOT NULL REFERENCES products(id),
  product_id INTEGER NOT NULL REFERENCES products(id), PRIMARY KEY(version_id, product_id));
ALTER TABLE packs ADD COLUMN location_id INTEGER REFERENCES locations(id);
ALTER TABLE packs ADD COLUMN removed_op INTEGER REFERENCES ops(id);
UPDATE packs SET location_id=(SELECT e.location_id FROM stock_entries e
  WHERE e.lot_id=packs.lot_id AND e.kind='production_out' ORDER BY e.id LIMIT 1);
CREATE TRIGGER packs_removed_once BEFORE UPDATE OF removed_op ON packs
WHEN OLD.removed_op IS NOT NULL BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER barcode_packaging_same_product BEFORE INSERT ON barcodes
WHEN NEW.packaging_id IS NOT NULL AND
  (SELECT product_id FROM packagings WHERE id=NEW.packaging_id) IS NOT NEW.product_id
BEGIN SELECT RAISE(ABORT,'packaging_other_product'); END;
CREATE VIEW payment_states AS
  SELECT p.*, CASE WHEN c.outcome='confirmed' THEN 'settled' WHEN c.outcome='rejected' THEN 'rejected'
    ELSE p.state END AS eff_state
  FROM payments p LEFT JOIN payment_confirmations c ON c.payment_id=p.id;
CREATE INDEX credit_alloc_sale ON credit_allocations(sale_id);
CREATE INDEX packs_lot ON packs(lot_id, status);
"""


def m002(conn):
    import secrets
    run_script(conn, M002)
    for table in ("refund_allocations", "credit_allocations", "safe_entries", "recall_lifts", "recipe_alternatives"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                         f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END")
    # public identity of this data folder (launcher checks it); random, not a path
    conn.execute("INSERT OR IGNORE INTO settings VALUES('instance_id', ?)", ('"%s"' % secrets.token_hex(8),))


MIGRATIONS.append((2, "audit fixes: refunds, credit allocation, safe, recall lifts, packs", m002))
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 3
# Review of 31 Oct 2026 (docs/REVIEW_MARKET_LANGUAGE_20261031.md). Adds only.
# Old collections without an invoice allocation are re-allocated ONLY when the
# replay is provable; anything else becomes a review item that blocks the
# affected money paths until the owner resolves it.
M003 = r"""
CREATE TABLE pack_events(
  id INTEGER PRIMARY KEY, pack_id INTEGER NOT NULL REFERENCES packs(id), op_id INTEGER NOT NULL REFERENCES ops(id),
  kind TEXT NOT NULL CHECK(kind IN ('unpack')), qty INTEGER NOT NULL CHECK(qty>0),
  lot_id INTEGER NOT NULL REFERENCES lots(id), at TEXT NOT NULL);
CREATE TABLE refund_cancellations(
  return_id INTEGER NOT NULL REFERENCES returns(id), kind TEXT NOT NULL CHECK(kind IN ('uncollected')),
  amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0), PRIMARY KEY(return_id, kind));
CREATE TABLE supplier_returns(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id), location_id INTEGER NOT NULL REFERENCES locations(id),
  qty INTEGER NOT NULL CHECK(qty>0), cost INTEGER NOT NULL CHECK(cost>=0),
  reason TEXT NOT NULL CHECK(reason IN ('damaged','wrong_item','weight_dispute','expired','quality','other')),
  user_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT);
CREATE TABLE supplier_credit_notes(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), return_id INTEGER NOT NULL REFERENCES supplier_returns(id),
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id), amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
  reference TEXT NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TABLE migration_reviews(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
  resolved_at TEXT, resolved_by INTEGER, resolution TEXT);
ALTER TABLE products ADD COLUMN meat_permille INTEGER CHECK(meat_permille IS NULL OR (meat_permille BETWEEN 0 AND 1000));
ALTER TABLE packs ADD COLUMN meat_basis TEXT;
CREATE TRIGGER migration_reviews_frozen BEFORE UPDATE ON migration_reviews
WHEN NEW.kind IS NOT OLD.kind OR NEW.ref IS NOT OLD.ref OR NEW.detail IS NOT OLD.detail OR OLD.resolved_at IS NOT NULL
BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER migration_reviews_no_delete BEFORE DELETE ON migration_reviews BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE INDEX supplier_returns_supplier ON supplier_returns(supplier_id);
"""


def _rebuild_collection_allocations(conn):
    """Replay customer accounts that predate credit_allocations.

    Only entries WITHOUT any allocation row are touched, in id order, with the
    rules the program uses today: a collection settles the oldest open sale of
    that customer first; a legacy store-credit return first cancels the debt of
    its own sale; a reversal mirrors the allocations of the collection it
    reverses. A customer whose replay is not consistent gets a review item and
    NO allocation rows."""
    now = conn.execute("SELECT datetime('now')").fetchone()[0]
    customers = [r[0] for r in conn.execute(
        """SELECT DISTINCT e.customer_id FROM customer_entries e WHERE e.kind IN ('payment','return','payment_reversal')
           AND NOT EXISTS(SELECT 1 FROM credit_allocations a WHERE a.entry_id=e.id)""")]
    for cid in customers:
        owed = {}      # sale id -> on-account amount still open (own sales only)
        order = []
        born = {}      # sale id -> id of its on-account entry (a collection settles only older sales)
        for s in conn.execute("""SELECT s.id, s.op_id FROM sales s WHERE s.customer_id=? ORDER BY s.id""", (cid,)):
            debt = conn.execute("""SELECT COALESCE(SUM(p.amount),0) FROM payments p LEFT JOIN payment_confirmations c ON c.payment_id=p.id
                WHERE p.sale_id=? AND ((p.method='credit' AND COALESCE(c.outcome,'')<>'rejected')
                OR (p.method IN ('transfer','wallet') AND c.outcome='rejected'))""", (s[0],)).fetchone()[0]
            allocated = conn.execute("SELECT COALESCE(SUM(amount),0) FROM credit_allocations WHERE sale_id=?", (s[0],)).fetchone()[0]
            if debt:
                owed[s[0]] = debt - allocated
                order.append(s[0])
                entry = conn.execute("SELECT MIN(id) FROM customer_entries WHERE customer_id=? AND op_id=? AND kind='sale'",
                                     (cid, s[1])).fetchone()[0]
                born[s[0]] = entry if entry is not None else 10 ** 18   # unknown age: never settled automatically
        plan, ok, reason = [], True, ""
        mirrored = {}
        for e in conn.execute("""SELECT e.id, e.kind, e.amount, e.op_id, e.reverses FROM customer_entries e WHERE e.customer_id=?
                AND e.kind IN ('payment','return','payment_reversal') AND NOT EXISTS(SELECT 1 FROM credit_allocations a WHERE a.entry_id=e.id)
                ORDER BY e.id""", (cid,)):
            eid, kind, amount, op_id, reverses = e
            if kind == "payment":
                left, pairs = -amount, []
                for sid in order:
                    if born[sid] > eid:
                        continue
                    take = min(left, owed[sid])
                    if take > 0:
                        pairs.append((sid, take)); owed[sid] -= take; left -= take
                    if not left:
                        break
                plan.append((eid, pairs)); mirrored[eid] = pairs
            elif kind == "return":
                sale = conn.execute("SELECT sale_id FROM returns WHERE op_id=?", (op_id,)).fetchone()
                pairs = []
                if sale and sale[0] in owed:
                    take = min(-amount, owed[sale[0]])
                    if take > 0:
                        pairs.append((sale[0], take)); owed[sale[0]] -= take
                plan.append((eid, pairs))
            else:  # payment_reversal
                if reverses is None:
                    ok, reason = False, "reversal %d has no original" % eid
                    break
                pairs = mirrored.get(reverses)
                if pairs is None:   # original already had allocation rows (new program): mirror them
                    pairs = [(r[0], r[1]) for r in conn.execute("SELECT sale_id, amount FROM credit_allocations WHERE entry_id=?", (reverses,))]
                mirror = []
                for sid, amt in pairs:
                    if sid in owed:
                        owed[sid] += amt
                    mirror.append((sid, -amt))
                plan.append((eid, mirror))
        if ok and any(v < 0 for v in owed.values()):
            ok, reason = False, "a sale would be over-collected"
        if not ok:
            conn.execute("INSERT INTO migration_reviews(kind,ref,detail,created_at) VALUES('customer_allocation',?,?,?)",
                         (str(cid), reason, now))
            continue
        for eid, pairs in plan:
            for sid, amt in pairs:
                conn.execute("INSERT INTO credit_allocations VALUES(?,?,?)", (eid, sid, amt))


def m003(conn):
    run_script(conn, M003)
    for table in ("pack_events", "refund_cancellations", "supplier_returns", "supplier_credit_notes"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                         f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END")
    _rebuild_collection_allocations(conn)


MIGRATIONS.append((3, "review 2026-10-31: pack unpacking, uncollected cancellation, supplier returns, legacy collections", m003))
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 4
# The eight open rows of the 40-case matrix (docs/MARKET_COVERAGE_20261031.md):
# supplier invoice before goods (2), dated cost correction after sale (3), gifts
# and samples (5), later supplier rebate (6), carton size change (9), thawing (14),
# buy-X-get-Y with fair partial returns (26), day close with dated corrections (38).
# Adds only; no posted row is changed. Old receipt lines read their carton factor
# from the packaging, which is frozen from this migration on.
M004 = r"""
CREATE TABLE supplier_invoices(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id),
  supplier_id INTEGER NOT NULL REFERENCES suppliers(id), number TEXT NOT NULL, invoice_date TEXT NOT NULL,
  total INTEGER NOT NULL CHECK(typeof(total)='integer' AND total>=0), user_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT,
  closed_op INTEGER REFERENCES ops(id), closed_reason TEXT, UNIQUE(supplier_id, number, invoice_date));
CREATE TABLE supplier_invoice_lines(
  id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES supplier_invoices(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  qty INTEGER NOT NULL CHECK(typeof(qty)='integer' AND qty>0), free_qty INTEGER NOT NULL CHECK(free_qty>=0),
  line_cost INTEGER NOT NULL CHECK(typeof(line_cost)='integer' AND line_cost>=0), UNIQUE(invoice_id, product_id));
ALTER TABLE receipts ADD COLUMN invoice_id INTEGER REFERENCES supplier_invoices(id);
ALTER TABLE receipt_lines ADD COLUMN line_kind TEXT NOT NULL DEFAULT 'purchase' CHECK(line_kind IN ('purchase','gift','sample'));
ALTER TABLE receipt_lines ADD COLUMN factor_num INTEGER;
ALTER TABLE receipt_lines ADD COLUMN factor_den INTEGER;
CREATE TABLE supplier_adjustments(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  receipt_id INTEGER REFERENCES receipts(id), kind TEXT NOT NULL CHECK(kind IN ('invoice_correction','rebate')),
  amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount<>0), reference TEXT NOT NULL,
  user_id INTEGER NOT NULL, at TEXT NOT NULL, note TEXT, UNIQUE(supplier_id, reference));
CREATE TABLE cost_adjustments(
  adjustment_id INTEGER NOT NULL REFERENCES supplier_adjustments(id), receipt_line_id INTEGER NOT NULL REFERENCES receipt_lines(id),
  lot_id INTEGER NOT NULL REFERENCES lots(id), amount INTEGER NOT NULL, to_stock INTEGER NOT NULL, to_cost_of_sales INTEGER NOT NULL,
  CHECK(to_stock + to_cost_of_sales = amount), PRIMARY KEY(adjustment_id, receipt_line_id));
ALTER TABLE packagings ADD COLUMN retired_op INTEGER REFERENCES ops(id);
CREATE TABLE packaging_changes(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), old_id INTEGER NOT NULL REFERENCES packagings(id),
  new_id INTEGER NOT NULL REFERENCES packagings(id), moved_barcodes TEXT NOT NULL, reason TEXT NOT NULL,
  user_id INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TABLE thaws(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), parent_lot INTEGER NOT NULL REFERENCES lots(id),
  child_lot INTEGER NOT NULL UNIQUE REFERENCES lots(id), from_location INTEGER NOT NULL REFERENCES locations(id),
  to_location INTEGER NOT NULL REFERENCES locations(id), qty INTEGER NOT NULL CHECK(qty>0),
  policy_days INTEGER NOT NULL CHECK(policy_days>0), user_id INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TABLE promotions(
  id INTEGER PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), product_id INTEGER NOT NULL REFERENCES products(id),
  buy_qty INTEGER NOT NULL CHECK(typeof(buy_qty)='integer' AND buy_qty>0),
  free_qty INTEGER NOT NULL CHECK(typeof(free_qty)='integer' AND free_qty>0),
  starts TEXT NOT NULL, ends TEXT, user_id INTEGER NOT NULL, at TEXT NOT NULL, ended_op INTEGER REFERENCES ops(id));
ALTER TABLE sale_lines ADD COLUMN promo_discount INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sale_lines ADD COLUMN promotion_id INTEGER REFERENCES promotions(id);
CREATE TABLE day_closes(
  day TEXT PRIMARY KEY, op_id INTEGER NOT NULL UNIQUE REFERENCES ops(id), user_id INTEGER NOT NULL, at TEXT NOT NULL,
  snapshot TEXT NOT NULL, snapshot_hash TEXT NOT NULL);
CREATE TABLE day_corrections(
  id INTEGER PRIMARY KEY, day TEXT NOT NULL REFERENCES day_closes(day), op_id INTEGER NOT NULL REFERENCES ops(id),
  kind TEXT NOT NULL, ref TEXT NOT NULL, reason TEXT NOT NULL, user_id INTEGER NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER packagings_frozen BEFORE UPDATE ON packagings
WHEN NEW.product_id IS NOT OLD.product_id OR NEW.factor_num IS NOT OLD.factor_num OR NEW.factor_den IS NOT OLD.factor_den
  OR NEW.name_ar IS NOT OLD.name_ar OR NEW.name_en IS NOT OLD.name_en OR NEW.created_at IS NOT OLD.created_at
  OR OLD.retired_op IS NOT NULL
BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER packagings_no_delete BEFORE DELETE ON packagings BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER supplier_invoices_frozen BEFORE UPDATE ON supplier_invoices
WHEN NEW.supplier_id IS NOT OLD.supplier_id OR NEW.number IS NOT OLD.number OR NEW.invoice_date IS NOT OLD.invoice_date
  OR NEW.total IS NOT OLD.total OR NEW.op_id IS NOT OLD.op_id OR OLD.closed_op IS NOT NULL
BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER supplier_invoices_no_delete BEFORE DELETE ON supplier_invoices BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER promotions_frozen BEFORE UPDATE ON promotions
WHEN NEW.product_id IS NOT OLD.product_id OR NEW.buy_qty IS NOT OLD.buy_qty OR NEW.free_qty IS NOT OLD.free_qty
  OR NEW.starts IS NOT OLD.starts OR NEW.ends IS NOT OLD.ends OR NEW.op_id IS NOT OLD.op_id OR OLD.ended_op IS NOT NULL
BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER promotions_no_delete BEFORE DELETE ON promotions BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END;
CREATE TRIGGER ops_after_day_close BEFORE INSERT ON ops
WHEN substr(NEW.at,1,10) <= (SELECT MAX(day) FROM day_closes)
BEGIN SELECT RAISE(ABORT,'day_closed'); END;
CREATE INDEX receipts_invoice ON receipts(invoice_id);
CREATE INDEX promotions_product ON promotions(product_id);
CREATE INDEX day_corrections_day ON day_corrections(day);
"""


def m004(conn):
    run_script(conn, M004)
    for table in ("supplier_invoice_lines", "supplier_adjustments", "cost_adjustments", "packaging_changes",
                  "thaws", "day_closes", "day_corrections"):
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                         f"BEGIN SELECT RAISE(ABORT,'posted_is_immutable'); END")


MIGRATIONS.append((4, "matrix rows 2,3,5,6,9,14,26,38: invoices, cost corrections, gifts, rebates, cartons, thaw, promotions, day close", m004))
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 5
# Accounts (gap table row 5): a password set by the owner must be changed at
# first login; a locked screen is locked on the server, so a page reload
# cannot open it. Adds columns only.
M005 = r"""
ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sessions ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;
"""


def m005(conn):
    run_script(conn, M005)


MIGRATIONS.append((5, "accounts: forced password change, server-side screen lock", m005))
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 6
# The in-app guide (af-guide, same engine as the Yousef app): each person's lesson progress.
def m006(conn):
    from . import afguide
    conn.execute(afguide.SQL)


MIGRATIONS.append((6, "in-app guide progress per person", m006))
SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------- migration 7
# Business day (gap table row 15): a shift belongs to the business day it was
# opened on; any other posting to the day of (time minus the shop's day-start
# hour, default 4 a.m. like common POS systems). Old rows keep their calendar
# date (COALESCE in every report). The day-close guard now reads business days.
M007 = r"""
ALTER TABLE ops ADD COLUMN business_day TEXT;
ALTER TABLE shifts ADD COLUMN business_day TEXT;
CREATE INDEX ops_business_day ON ops(business_day);
DROP TRIGGER ops_after_day_close;
CREATE TRIGGER ops_after_day_close BEFORE INSERT ON ops
WHEN COALESCE(NEW.business_day, substr(NEW.at,1,10)) <= (SELECT MAX(day) FROM day_closes)
BEGIN SELECT RAISE(ABORT,'day_closed'); END;
"""


def m007(conn):
    run_script(conn, M007)


MIGRATIONS.append((7, "business day for shifts past midnight", m007))
SCHEMA_VERSION = MIGRATIONS[-1][0]
