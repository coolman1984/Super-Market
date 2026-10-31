"""Acceptance tests for the second review (docs/REVIEW_MARKET_LANGUAGE_20261031.md).
Each expects the CORRECT behaviour; E01-E06 failed on 3129b77 / fa9f8ef.
docs/audit/market_edges_20261031.py still shows the old faults on that commit."""

import pathlib
import sqlite3
import tempfile
import threading
import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import schema
from server.core import Conflict, Forbidden, ShopError
from server.db import Store
from server.services import Shop


class Chain(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def credit_sale(self, price=10000, limit=20000):
        p = self.grocery(price=price)
        self.receive(p, 3, 1500, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=limit)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c,
                                                 "payments": [{"method": "credit", "amount": price}]})
        line = self.shop.sale_detail(self.owner, s["sale"])["lines"][0]["id"]
        return p, c, s, line

    # ---- E01
    def test_e01_collection_used_by_a_refund_cannot_be_reversed(self):
        p, c, s, line = self.credit_sale()
        entry = self.shop.customer_payment(self.manager, key(), c, 10000)["entry"]
        self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "quality", "method": "original",
                                                    "lines": [{"sale_line_id": line, "qty": 1}]})
        self.shop.cash_move(self.manager, key(), "deposit", 10000, "funding")
        shift = self.store.scalar("SELECT id FROM shifts")
        before = self.shop.shift_expected(shift)
        with self.assertRaises(ShopError) as err:
            self.shop.reverse_customer_payment(self.manager, key(), entry, "wrong")
        self.assertEqual(err.exception.code, "collection_already_refunded")
        self.assertEqual(self.shop.customer_balance(c), 0)               # no phantom debt on a returned sale
        self.assertEqual(self.shop.shift_expected(shift), before)         # no second cash-out

    def test_e01_untouched_collection_still_reverses_and_partial_use_blocks_only_that_part(self):
        p, c, s, line = self.credit_sale()
        e1 = self.shop.customer_payment(self.manager, key(), c, 4000)["entry"]
        e2 = self.shop.customer_payment(self.manager, key(), c, 6000)["entry"]
        self.shop.reverse_customer_payment(self.manager, key(), e1, "bounced")   # nothing refunded yet
        self.assertEqual(self.shop.customer_balance(c), 4000)

    # ---- E02
    def packed(self):
        p = self.meat()
        r = self.receive(p, 1000, 10000, self.fridge)
        out, out2 = self.meat("OUT"), self.meat("OUT2")
        made = self.shop.produce(self.owner, key(), {"kind": "cutting",
                                                     "inputs": [{"lot_id": r["lines"][0]["lot"], "location_id": self.fridge, "qty": 1000}],
                                                     "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                     "packs": [{"net_g": 500}, {"net_g": 500}]})
        return out, out2, made["outputs"][0]

    def test_e02_production_cannot_consume_stock_promised_to_packs(self):
        out, out2, made = self.packed()
        before = list(self.store.db.iterdump())
        with self.assertRaises(ShopError) as err:
            self.shop.produce(self.owner, key(), {"kind": "cutting",
                                                  "inputs": [{"lot_id": made["lot"], "location_id": self.fridge, "qty": 1000}],
                                                  "outputs": [{"product_id": out2, "qty": 1000, "location_id": self.fridge}]})
        self.assertEqual(err.exception.code, "stock_reserved_by_packs")
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_e02_explicit_unpack_links_packs_to_the_raw_quantity(self):
        out, out2, made = self.packed()
        codes = [p["code"] for p in made["packs"]]
        r = self.shop.produce(self.owner, key(), {"kind": "cutting", "unpack": codes,
                                                  "inputs": [{"lot_id": made["lot"], "location_id": self.fridge, "qty": 1000}],
                                                  "outputs": [{"product_id": out2, "qty": 1000, "location_id": self.fridge}]})
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM packs WHERE lot_id=? AND removed_op IS NULL", (made["lot"],)), 0)
        events = self.store.all("SELECT kind, qty FROM pack_events ORDER BY id")
        self.assertEqual([(e["kind"], e["qty"]) for e in events], [("unpack", 500), ("unpack", 500)])
        self.assertTrue(self.shop.reconcile()["ok"])
        self.open_shift()
        with self.assertRaises(ShopError):                                  # an unpacked label can never sell
            self.shop.sell(self.cash1, key(), {"lines": [{"pack_code": codes[0]}]})
        with self.assertRaises(ShopError):                                  # an unknown label cannot free anything
            self.shop.produce(self.owner, key(), {"kind": "cutting", "unpack": ["2800000000000"],
                                                  "inputs": [{"lot_id": r["outputs"][0]["lot"], "location_id": self.fridge, "qty": 10}],
                                                  "outputs": [{"product_id": out, "qty": 10, "location_id": self.fridge}]})

    # ---- E03
    def test_e03_meat_role_needs_an_approved_meat_product(self):
        sauce = self.meat("SAUCE", kind="ingredient")
        beef = self.meat("BEEF")
        out = self.meat("TRAY", kind="prepared")
        with self.assertRaises(ShopError) as err:
            self.shop.save_recipe(self.owner, out, [{"product_id": sauce, "qty": 1000, "role": "meat"}], 1, "", 0)
        self.assertEqual(err.exception.code, "meat_role_needs_meat_product")
        with self.assertRaises(ShopError):                                  # a non-meat substitute of a meat component
            self.shop.save_recipe(self.owner, out, [{"product_id": beef, "qty": 800, "role": "meat", "alternatives": [sauce]}], 1, "", 0)

    def test_e03_composite_with_documented_meat_share_counts_only_that_share(self):
        mince = self.shop.add_product(self.owner, {"code": "MM", "name_ar": "مفروم متبل", "name_en": "Seasoned mince",
                                                   "kind": "prepared", "measure": "g", "meat_permille": 700, "price": 1000})
        out = self.meat("TAG", kind="prepared")
        self.receive(mince, 1000, 10000, self.fridge)
        lot = self.store.scalar("SELECT id FROM lots")
        v = self.shop.save_recipe(self.owner, out, [{"product_id": mince, "qty": 1000, "role": "food"}], 1, "", 0, target_pack_g=1000)
        r = self.shop.produce(self.butcher, key(), {"kind": "recipe", "recipe_version_id": v["recipe_version"],
                                                    "inputs": [{"lot_id": lot, "location_id": self.fridge, "qty": 1000}],
                                                    "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                    "packs": [{"net_g": 1000}]})
        pack = r["outputs"][0]["packs"][0]
        self.assertEqual((pack["meat_g"], pack["meat_basis"]), (700, "estimated"))       # documented share, labelled as such
        with self.assertRaises(ShopError):
            self.shop.add_product(self.owner, {"code": "X", "name_ar": "س", "name_en": "x", "kind": "grocery", "measure": "unit",
                                               "meat_permille": 2000})

    # ---- E04
    def test_e04_quote_fingerprint_covers_every_line(self):
        a = self.grocery("A", price=10000, barcodes=[])
        b = self.grocery("B", price=20000, barcodes=[])
        self.receive(a, 1, 1000, expires=None)
        self.receive(b, 1, 1000, expires=None)
        self.open_shift()
        cart = {"lines": [{"product_id": a, "qty": 1}, {"product_id": b, "qty": 1}]}
        quote = self.shop.quote(self.cash1, cart)
        self.shop.set_price(self.owner, key(), a, 20000)
        self.shop.set_price(self.owner, key(), b, 10000)                    # same total 30000, different lines
        with self.assertRaises(Conflict) as err:
            self.shop.sell(self.cash1, key(), dict(cart, expected_total=quote["total"], quote_hash=quote["quote_hash"]))
        self.assertEqual(err.exception.code, "price_changed")
        self.assertEqual({l["unit_price"] for l in err.exception.detail["lines"]}, {10000, 20000})
        fresh = self.shop.quote(self.cash1, cart)
        sale = self.shop.sell(self.cash1, key(), dict(cart, expected_total=fresh["total"], quote_hash=fresh["quote_hash"]))
        self.assertEqual(sale["total"], 30000)

    def test_e04_quote_hash_covers_discounts_and_quantities(self):
        a = self.grocery("A", price=10000, barcodes=[])
        self.receive(a, 5, 1000, expires=None)
        self.open_shift(self.manager)
        cart = {"lines": [{"product_id": a, "qty": 1}]}
        q = self.shop.quote(self.manager, cart)
        other = self.shop.quote(self.manager, {"lines": [{"product_id": a, "qty": 1, "line_discount": 100}]})
        self.assertNotEqual(q["quote_hash"], other["quote_hash"])
        with self.assertRaises(Conflict):
            self.shop.sell(self.manager, key(), {"lines": [{"product_id": a, "qty": 2}], "quote_hash": q["quote_hash"]})

    # ---- E05
    def test_e05_goods_return_when_an_anonymous_transfer_was_rejected(self):
        p = self.grocery(price=1000)
        self.receive(p, 2, 100, expires=None)
        self.open_shift(self.manager, 0)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}],
                                                 "payments": [{"method": "transfer", "amount": 1000}]})
        self.shop.confirm_payment(self.manager, key(), self.store.scalar("SELECT id FROM payments"), "rejected")
        line = self.shop.sale_detail(self.manager, s["sale"])["lines"][0]["id"]
        shift = self.store.scalar("SELECT id FROM shifts")
        r = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "original",
                                                        "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual(r["refunds"], [])                                  # no money paid: none received
        self.assertEqual(r["cancelled_uncollected"], 1000)
        self.assertEqual(r["lines"][0]["disposition"], "quarantine")        # the goods really came back
        self.assertEqual(self.shop.shift_expected(shift), 0)
        self.assertEqual(r["reference_receipt"], s["no"])
        self.assertTrue(self.shop.reconcile()["ok"])

    def test_e05_mixed_cash_and_rejected_transfer_partial_returns(self):
        p = self.grocery(price=1000)
        self.receive(p, 2, 100, expires=None)
        self.open_shift(self.manager, 0)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 2}], "cash_tendered": 800,
                                                 "payments": [{"method": "transfer", "amount": 1200}]})
        self.shop.confirm_payment(self.manager, key(), self.store.scalar("SELECT id FROM payments WHERE method='transfer'"), "rejected")
        line = self.shop.sale_detail(self.manager, s["sale"])["lines"][0]["id"]
        shift = self.store.scalar("SELECT id FROM shifts")
        r1 = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "original",
                                                         "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual((r1["cancelled_uncollected"], r1["refunds"]), (1000, []))
        r2 = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "original",
                                                         "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual((r2["cancelled_uncollected"], r2["refunds"]), (200, [{"method": "cash", "amount": 800}]))
        self.assertEqual(self.shop.shift_expected(shift), 0)

    def test_e05_cancellation_only_return_is_not_counted_as_a_refund_by_method(self):
        """Review finding: a return that only cancels the rejected transfer wrote no allocation row and was
        taken for an old cash refund, so the 800 actually received could not be refunded afterwards."""
        p = self.grocery(price=1000)
        self.receive(p, 2, 100, expires=None)
        self.open_shift(self.manager, 0)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 2}], "cash_tendered": 800,
                                                 "payments": [{"method": "transfer", "amount": 1200}]})
        self.shop.confirm_payment(self.manager, key(), self.store.scalar("SELECT id FROM payments WHERE method='transfer'"), "rejected")
        line = self.shop.sale_detail(self.manager, s["sale"])["lines"][0]["id"]
        r1 = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "cash",
                                                         "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual(r1["refunds"], [])
        r2 = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "cash",
                                                         "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual(r2["refunds"], [{"method": "cash", "amount": 800}])

    def test_supplier_statement_hides_stock_cost_from_a_user_without_see_cost(self):
        sup = self.shop.add_party(self.owner, "supplier", "S")
        p = self.grocery()
        r = self.shop.receive(self.owner, key(), {"supplier_id": sup, "supplier_invoice": "I1", "lines": [
            {"product_id": p, "qty": 10, "line_cost": 10000, "location_id": self.shelf}]})
        self.shop.supplier_return(self.owner, key(), sup, r["lines"][0]["lot"], self.shelf, 2, "damaged")
        self.store.tx(lambda: self.shop.auth.create_user("rcv", "R", "receiver", PASSWORD))
        receiver = self.actor("rcv")
        st = self.shop.supplier_statement(receiver, sup)
        self.assertNotIn("cost", st["open_returns"][0])
        self.assertIn("cost", self.shop.supplier_statement(self.owner, sup)["open_returns"][0])

    # ---- E06 + upgrade
    def test_unallocated_cash_collection_blocks_nothing_it_cannot_prove(self):
        p, c, s, line = self.credit_sale()
        op = self.store.scalar("SELECT MAX(id) FROM ops")
        self.store.tx(lambda: self.store.db.execute(
            "INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,'payment',-10000,'2026-10-30')", (c, op)))
        self.assertEqual(self.shop.customer_balance(c), 0)
        with self.assertRaises(ShopError) as err:                           # the sale still shows 10000 open: refuse to guess
            self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "quality", "method": "cash",
                                                        "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertIn(err.exception.code, ("collections_unallocated", "refund_exceeds_paid_by_method"))
        self.assertEqual(self.shop.account_check(c)["ok"], False)


def v1_database(path):
    """A database exactly as the 0.1.0 program left it: schema 1 only, with an
    on-account sale, a collection WITHOUT an invoice allocation, a legacy
    store-credit return, a supplier invoice, a safe-less drawer and a pack."""
    old = Store.__new__(Store)
    old.folder, old.path, old.mode = path, path / "shop.db", "shop"
    path.mkdir(parents=True)
    old.lock = threading.RLock()
    old.db = sqlite3.connect(str(old.path), isolation_level=None, check_same_thread=False)
    old.db.row_factory = sqlite3.Row
    old.db.execute("PRAGMA foreign_keys=ON")
    old.migrate(schema.MIGRATIONS[:1])
    old._check_mode()
    x = old.db.execute
    x("INSERT INTO users(id,username,display_name,role,pw_hash,pw_salt,pw_iter,created_at) VALUES(1,'m','M','manager','h','00',1,'t')")
    x("INSERT INTO locations(id,code,name_ar,name_en,kind,sellable) VALUES(1,'SH','ر','S','shelf',1),(2,'QA','ح','Q','quarantine',0)")
    x("INSERT INTO products(id,code,name_ar,name_en,kind,measure,needs_expiry,created_at) VALUES(1,'G','س','G','grocery','unit',0,'t')")
    x("INSERT INTO customers(id,name,credit_limit,created_at) VALUES(1,'C',100000,'t')")
    x("INSERT INTO shifts(id,user_id,workstation,opened_at,opening_float) VALUES(1,1,'w','2026-10-01',0)")
    seq = {"n": 0}

    def op(kind):
        seq["n"] += 1
        x("INSERT INTO ops(id,kind,key,fingerprint,user_id,workstation,at) VALUES(?,?,?,?,1,'w','2026-10-01')",
          (seq["n"], kind, "k%d" % seq["n"], "f"))
        x("INSERT INTO op_results VALUES(?,'{}')", (seq["n"],))
        return seq["n"]
    o = op("receipt")
    x("INSERT INTO lots(id,product_id,lot_no,origin,origin_op,status,created_at) VALUES(1,1,'L1','receipt',?,'released','t')", (o,))
    x("INSERT INTO stock_entries(op_id,kind,lot_id,location_id,qty,cost,at) VALUES(?,'receipt',1,1,10,1000,'t')", (o,))
    for n in (1, 2):                                       # two on-account sales of 10000 and 6000
        o = op("sale")
        price = 10000 if n == 1 else 6000
        x("INSERT INTO sales(id,op_id,no,shift_id,user_id,customer_id,at,gross,line_discounts,invoice_discount,total,cash_tendered,change_given,training) "
          "VALUES(?,?,?,1,1,1,?,?,0,0,?,0,0,0)", (n, o, "S-%d" % n, "2026-10-0%d" % n, price, price))
        x("INSERT INTO sale_lines(id,sale_id,product_id,qty,unit_price,price_basis,gross,line_discount,invoice_discount_share,net,cost) "
          "VALUES(?,?,1,1,?,'per_unit',?,0,0,?,100)", (n, n, price, price, price))
        x("INSERT INTO payments(op_id,sale_id,method,amount,state,at) VALUES(?,?,'credit',?,'settled','t')", (o, n, price))
        x("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(1,?,'sale',?,'t')", (o, price))
    o = op("customer_payment")                              # legacy collection: 12000, no allocation rows
    x("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(1,?,'payment',-12000,'t')", (o,))
    x("INSERT INTO cash_entries(shift_id,op_id,kind,amount,at) VALUES(1,?,'debt_collect',12000,'t')", (o,))
    old.close()


class UpgradeFromV1(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "data"
        v1_database(self.path)

    def test_old_collections_are_allocated_when_provable_and_accounts_agree(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.schema_version(), schema.SCHEMA_VERSION)
        self.assertTrue(list((self.path / "pre-upgrade").glob("schema-1-*.db")))
        alloc = {r["sale_id"]: r["amount"] for r in store.all("SELECT sale_id, SUM(amount) amount FROM credit_allocations GROUP BY sale_id")}
        self.assertEqual(alloc, {1: 10000, 2: 2000})                          # oldest sale first, remainder on the next
        shop = Shop(store)
        self.assertEqual(shop.customer_balance(1), 4000)                       # 16000 - 12000
        self.assertEqual(shop._sale_outstanding(1), 0)
        self.assertEqual(shop._sale_outstanding(2), 4000)
        self.assertEqual(shop.account_check(1)["ok"], True)
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM migration_reviews"), 0)
        self.assertEqual(store.scalar("SELECT COALESCE(SUM(amount),0) FROM cash_entries"), 12000)   # drawer untouched
        self.assertTrue(shop.reconcile()["ok"])

    def test_upgrade_is_idempotent_and_changes_no_old_row(self):
        store = Store(self.path)
        n = store.scalar("SELECT COUNT(*) FROM customer_entries")
        a = store.scalar("SELECT COUNT(*) FROM credit_allocations")
        store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual((again.scalar("SELECT COUNT(*) FROM customer_entries"), again.scalar("SELECT COUNT(*) FROM credit_allocations")), (n, a))

    def test_a_collection_that_cannot_be_proven_becomes_a_review_item_and_blocks_cash_refunds(self):
        # a collection BEFORE any sale: nothing provable to settle
        raw = sqlite3.connect(str(self.path / "shop.db"), isolation_level=None)
        raw.execute("DROP TRIGGER IF EXISTS customer_entries_no_update")
        raw.execute("INSERT INTO ops(id,kind,key,fingerprint,user_id,workstation,at) VALUES(90,'x','k90','f',1,'w','t')")
        raw.execute("INSERT INTO op_results VALUES(90,'{}')")
        raw.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at,reverses) VALUES(1,90,'payment_reversal',-5000,'t',NULL)")
        raw.close()
        store = Store(self.path)
        self.addCleanup(store.close)
        reviews = store.all("SELECT kind, ref FROM migration_reviews")
        self.assertEqual([(r["kind"], r["ref"]) for r in reviews], [("customer_allocation", "1")])
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM credit_allocations"), 0)     # nothing guessed
        shop = Shop(store)
        self.assertFalse(shop.account_check(1)["ok"])
        with self.assertRaises(ShopError) as err:
            shop._require_account_clear(1)
        self.assertEqual(err.exception.code, "account_needs_review")

    def test_upgrade_failure_in_the_middle_keeps_the_original(self):
        store = Store.__new__(Store)
        raw = sqlite3.connect(str(self.path / "shop.db"))
        before = raw.execute("SELECT COUNT(*) FROM customer_entries").fetchone()[0]
        raw.close()

        def broken(conn):
            conn.execute("CREATE TABLE half(x)")
            raise RuntimeError("power cut")
        s = Store(self.path)           # runs migrations 2 and 3 normally first
        with self.assertRaises(RuntimeError):
            s.migrate(schema.MIGRATIONS + [(schema.SCHEMA_VERSION + 1, "broken", broken)])
        self.assertIsNone(s.scalar("SELECT 1 FROM sqlite_master WHERE name='half'"))
        self.assertEqual(s.scalar("SELECT COUNT(*) FROM customer_entries"), before)
        s.close()


if __name__ == "__main__":
    unittest.main()
