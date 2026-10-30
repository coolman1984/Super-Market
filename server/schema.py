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
