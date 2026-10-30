"""Shop service contracts mapped to docs/06-ACCEPTANCE.md identifiers."""

import concurrent.futures
import sqlite3
import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import core
from server.core import Conflict, Forbidden, ShopError, allocate, weighed_amount
from server.db import Store


class Money(unittest.TestCase):
    def test_weighed_amount_half_up_and_no_float(self):
        self.assertEqual(weighed_amount(40000, 750), 30000)
        self.assertEqual(weighed_amount(33333, 1), 33)    # 33.333 -> 33
        self.assertEqual(weighed_amount(500, 1), 1)       # 0.5 -> 1 (half up)
        with self.assertRaises(ShopError):
            weighed_amount(40000, 1.5)

    def test_allocate_keeps_every_piastre(self):
        for total, weights in ((100, [1, 1, 1]), (24000, [24, 2]), (7, [0, 0]), (-10, [1, 2])):
            parts = allocate(total, weights)
            self.assertEqual(sum(parts), total)
        self.assertEqual(allocate(100, [1, 1, 1]), [34, 33, 33])


class Grocery(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_reference_grocery_cycle(self):
        """06 reference: 2 cartons x 12 = 24 units for 240 EGP; sell 3 at 15."""
        pid = self.grocery(carton=ean("1622123456789"))
        pk = self.store.scalar("SELECT id FROM packagings WHERE product_id=?", (pid,))
        r = self.shop.receive(self.owner, key(), {"lines": [{"product_id": pid, "packaging_id": pk, "packs": 2,
                                                             "line_cost": 24000, "location_id": self.shelf}]})
        self.assertEqual(r["lines"][0]["qty"], 24)
        self.open_shift()
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 3}], "cash_tendered": 5000})
        self.assertEqual((sale["total"], sale["change"]), (4500, 500))
        detail = self.shop.sale_detail(self.owner, sale["sale"])
        self.assertEqual(detail["cost_total"], 3000)
        self.assertEqual(self.shop.available(pid), 21)
        self.assertTrue(self.shop.reconcile()["ok"])
        # carton barcode scan proposes 12 units, item barcode 1
        self.assertEqual(self.shop.resolve_scan(self.cash1, ean("1622123456789") + "\r")["qty"], 12)
        self.assertEqual(self.shop.resolve_scan(self.cash1, ean("622123456789"))["qty"], 1)

    def test_free_goods_spread_cost_without_losing_piastres(self):
        pid = self.grocery()
        r = self.receive(pid, 24, 24000, free_qty=2, expires=None)
        self.assertEqual((r["lines"][0]["qty"], r["lines"][0]["cost"]), (26, 24000))
        self.open_shift()
        total_cost = 0
        for _ in range(26):
            s = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1}]})
            total_cost += self.shop.sale_detail(self.owner, s["sale"])["cost_total"]
        self.assertEqual(total_cost, 24000)

    def test_r01_partial_receipt_discounts_extra_cost_and_supplier_debt(self):
        pid = self.grocery()
        sup = self.shop.add_party(self.owner, "supplier", "مورد")
        r = self.shop.receive(self.owner, key(), {
            "supplier_id": sup, "supplier_invoice": "INV-1", "invoice_date": "2026-10-29",
            "invoice_discount": 1000, "extra_cost": 500,
            "lines": [{"product_id": pid, "qty": 10, "rejected_qty": 2, "line_cost": 8000, "line_discount": 400,
                       "location_id": self.shelf, "reject_reason": "damaged"}]})
        self.assertEqual(r["total"], 8000 - 400 - 1000 + 500)
        self.assertEqual(self.shop.available(pid), 8)
        self.assertEqual(self.shop.supplier_balance(sup), 7100)
        with self.assertRaises(ShopError) as dup:
            self.shop.receive(self.owner, key(), {"supplier_id": sup, "supplier_invoice": "INV-1", "invoice_date": "2026-10-29",
                                                  "lines": [{"product_id": pid, "qty": 1, "line_cost": 1, "location_id": self.shelf}]})
        self.assertEqual(dup.exception.code, "duplicate_supplier_invoice")

    def test_r03_s02_same_key_replays_other_payload_conflicts(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        self.open_shift()
        k = key()
        first = self.shop.sell(self.cash1, k, {"lines": [{"product_id": pid, "qty": 2}]})
        again = self.shop.sell(self.cash1, k, {"lines": [{"product_id": pid, "qty": 2}]})
        self.assertEqual(first["sale"], again["sale"])
        self.assertTrue(again["replayed"])
        self.assertEqual(self.shop.available(pid), 8)
        with self.assertRaises(Conflict):
            self.shop.sell(self.cash1, k, {"lines": [{"product_id": pid, "qty": 3}]})
        self.assertEqual(self.shop.available(pid), 8)

    def test_u03_bad_quantities_rejected_without_effect(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        self.open_shift()
        before = self.store.scalar("SELECT COUNT(*) FROM stock_entries")
        for qty in (0, -1, 1.5, "2", True, None):
            with self.subTest(qty=qty), self.assertRaises(ShopError):
                self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": qty}]})
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM stock_entries"), before)

    def test_s01_two_cashiers_last_unit(self):
        pid = self.grocery()
        self.receive(pid, 1, 100, expires=None)
        self.open_shift(self.cash1)
        self.open_shift(self.cash2)

        def sell(actor):
            try:
                return self.shop.sell(actor, key(), {"lines": [{"product_id": pid, "qty": 1}]})["sale"]
            except ShopError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(sell, [self.cash1, self.cash2]))
        self.assertEqual(sorted(map(str, results)).count("insufficient_stock"), 1)
        self.assertEqual(self.shop.available(pid), 0)

    def test_s04_mixed_payment_change_and_drawer(self):
        """06 reference: 250.75 bill, confirmed transfer 100 + cash 200 -> change 49.25."""
        pid = self.grocery(price=25075)
        self.receive(pid, 5, 1000, expires=None)
        self.open_shift(amount=50000)
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1}],
                                                 "payments": [{"method": "transfer", "amount": 10000, "reference": "TX1"}],
                                                 "cash_tendered": 20000})
        self.assertEqual((sale["cash_due"], sale["change"]), (15075, 4925))
        self.assertEqual(sale["payments"][0]["state"], "pending")
        summary = self.shop.shift_summary(self.manager, self.store.scalar("SELECT id FROM shifts"))
        self.assertEqual(summary["expected"], 50000 + 15075)

    def test_m01_drawer_reference(self):
        pid = self.grocery(price=20000)
        self.receive(pid, 50, 1000, expires=None)
        cust = self.shop.add_party(self.owner, "customer", "عميل", credit_limit=100000)
        self.open_shift(self.manager, 50000)
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": pid, "qty": 10}]})
        credit = self.shop.sell(self.manager, key(), {"lines": [{"product_id": pid, "qty": 1}], "customer_id": cust,
                                                     "payments": [{"method": "credit", "amount": 20000}]})
        self.assertEqual(credit["cash_due"], 0)
        self.shop.customer_payment(self.manager, key(), cust, 10000)
        line = self.shop.sale_detail(self.manager, sale["sale"])["lines"][0]
        self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "change_of_mind",
                                                    "lines": [{"sale_line_id": line["id"], "qty": 1, "restock": True}]})
        self.shop.cash_move(self.manager, key(), "expense", 5000, "ice")
        self.shop.cash_move(self.manager, key(), "drop", 100000)
        # 500 + 2000 + 100 - 200 (one returned unit) - 50 - 1000 = 1350 EGP
        closed = self.shop.close_shift(self.manager, key(), 133000, "short")
        self.assertEqual((closed["expected"], closed["difference"]), (135000, -2000))

    def test_s06_line_discount_split_and_partial_returns_never_over_refund(self):
        a = self.grocery("A", price=1000, barcodes=[ean("622123456790")])
        b = self.grocery("B", price=333, barcodes=[ean("622123456791")])
        self.receive(a, 10, 100, expires=None)
        self.receive(b, 10, 100, expires=None)
        self.open_shift(self.manager)
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": a, "qty": 3}, {"product_id": b, "qty": 3}],
                                                   "invoice_discount": 100})
        self.assertEqual(sale["total"], 3000 + 999 - 100)
        detail = self.shop.sale_detail(self.manager, sale["sale"])
        refunded = 0
        for line in detail["lines"]:
            for _ in range(3):
                r = self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "wrong_item",
                                                                "lines": [{"sale_line_id": line["id"], "qty": 1}]})
                refunded += r["amount"]
        self.assertEqual(refunded, sale["total"])
        with self.assertRaises(ShopError):
            self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "wrong_item",
                                                        "lines": [{"sale_line_id": detail["lines"][0]["id"], "qty": 1}]})

    def test_discount_limits_need_manager_approval(self):
        pid = self.grocery(price=10000)
        self.receive(pid, 10, 100, expires=None)
        self.open_shift()
        with self.assertRaises(Forbidden):
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1, "line_discount": 2000}]})
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1, "line_discount": 2000}]},
                              approver=self.approval())
        self.assertEqual(sale["total"], 8000)
        with self.assertRaises(ShopError):
            self.approval_bad = self.shop.verify_approval({"username": "manager", "password": "wrong"})

    def test_a01_cashier_cannot_see_cost_or_reports(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        with self.assertRaises(Forbidden):
            self.shop.day_report(self.cash1)
        self.assertNotIn("cost", self.shop.stock_report(self.cash1)[0])
        with self.assertRaises(Forbidden):
            self.shop.set_price(self.cash1, key(), pid, 1)

    def test_a02_posted_rows_are_immutable(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        for sql in ("UPDATE stock_entries SET qty=1", "DELETE FROM stock_entries", "UPDATE ops SET key='x'",
                    "UPDATE lots SET expires='2099-01-01'", "DELETE FROM lots"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                self.store.db.execute(sql)

    def test_no_negative_stock_even_with_direct_sql(self):
        pid = self.grocery()
        self.receive(pid, 1, 100, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        op = self.store.scalar("SELECT id FROM ops")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("INSERT INTO stock_entries(op_id,kind,lot_id,location_id,qty,cost,at) VALUES(?,?,?,?,?,?,?)",
                                  (op, "x", lot, self.shelf, -2, -100, "t"))

    def test_c01_c02_c03_c04_blind_count(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        other = self.grocery("G2", barcodes=[ean("622123456792")])
        self.receive(other, 5, 500, expires=None)
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        sheet = self.shop.count_sheet(self.counter, count)
        self.assertNotIn("qty", sheet["lots"][0])
        self.open_shift()
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 2}]})  # after cutoff
        self.shop.record_count(self.counter, count, lot, 7)  # shelf shows 7 after the sale: one missing
        with self.assertRaises(Forbidden):
            self.shop.approve_count(self.counter, key(), count)
        result = self.shop.approve_count(self.manager, key(), count)
        self.assertEqual([d["diff"] for d in result["differences"]], [-1])
        self.assertEqual(self.shop.available(pid), 7)       # no phantom diff from the later sale
        self.assertEqual(self.shop.available(other), 5)     # uncounted is not zero
        with self.assertRaises(ShopError):
            self.shop.approve_count(self.manager, key(), count)
        # C04: a manager who counted cannot approve his own count; the owner only with a reason
        c2 = self.shop.open_count(self.manager, self.shelf)["count"]
        self.shop.record_count(self.manager, c2, lot, 7)
        with self.assertRaises(Forbidden):
            self.shop.approve_count(self.manager, key(), c2)
        c3_loc = self.fridge
        self.receive(pid, 3, 300, self.fridge, None)
        c3 = self.shop.open_count(self.owner, c3_loc)["count"]
        self.shop.record_count(self.owner, c3, self.store.scalar("SELECT MAX(id) FROM lots"), 3)
        with self.assertRaises(Forbidden):
            self.shop.approve_count(self.owner, key(), c3)
        self.assertEqual(self.shop.approve_count(self.owner, key(), c3, "alone in the shop")["differences"], [])

    def test_count_with_sales_between_opening_and_counting_books_nothing(self):
        """Review finding: sale after opening, real shelf counted later -> no phantom loss."""
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        self.open_shift()
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 2}]})
        self.shop.record_count(self.counter, count, lot, 8)
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1}]})   # after counting
        self.assertEqual(self.shop.approve_count(self.manager, key(), count)["differences"], [])
        self.assertEqual(self.shop.available(pid), 7)

    def test_counter_cannot_read_quantities(self):
        pid = self.grocery()
        self.receive(pid, 10, 1000, expires=None)
        with self.assertRaises(Forbidden):
            self.shop.stock_report(self.counter)
        self.assertIsNone(self.shop.search_products("", 10, self.counter)[0]["available"])

    def test_refund_follows_payment_method(self):
        pid = self.grocery(price=3000)
        self.receive(pid, 10, 100, expires=None)
        cust = self.shop.add_party(self.owner, "customer", "C", credit_limit=5000)
        self.open_shift(self.manager)
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": pid, "qty": 1}], "customer_id": cust,
                                                   "payments": [{"method": "credit", "amount": 3000}]})
        line = self.shop.sale_detail(self.manager, sale["sale"])["lines"][0]
        with self.assertRaises(ShopError) as err:
            self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "cash",
                                                        "lines": [{"sale_line_id": line["id"], "qty": 1}]})
        self.assertEqual(err.exception.code, "refund_exceeds_paid_by_method")
        self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "credit_note",
                                                    "lines": [{"sale_line_id": line["id"], "qty": 1}]})
        self.assertEqual(self.shop.customer_balance(cust), 0)
        wallet = self.shop.sell(self.manager, key(), {"lines": [{"product_id": pid, "qty": 1}],
                                                     "payments": [{"method": "wallet", "amount": 3000}]})
        wl = self.shop.sale_detail(self.manager, wallet["sale"])["lines"][0]
        with self.assertRaises(ShopError) as err:
            self.shop.return_sale(self.manager, key(), {"sale_id": wallet["sale"], "reason": "quality", "method": "original",
                                                        "lines": [{"sale_line_id": wl["id"], "qty": 1}]})
        self.assertEqual(err.exception.code, "sale_has_unconfirmed_payment")

    def test_split_credit_payments_respect_the_limit(self):
        pid = self.grocery(price=3000)
        self.receive(pid, 10, 100, expires=None)
        cust = self.shop.add_party(self.owner, "customer", "C", credit_limit=2000)
        self.open_shift(self.manager)
        with self.assertRaises(ShopError):
            self.shop.sell(self.manager, key(), {"lines": [{"product_id": pid, "qty": 1}], "customer_id": cust,
                                                 "payments": [{"method": "credit", "amount": 1500}, {"method": "credit", "amount": 1500}]})

    def test_settings_are_typed(self):
        for change in ({"cashier_discount_permille": "50"}, {"near_expiry_days": -1}, {"pack_prefix": "62"},
                       {"block_below_cost": 1}, {"receipt_width_mm": 70}):
            with self.subTest(change=change), self.assertRaises(ShopError):
                self.shop.update_settings(self.owner, change)


class Food(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_l01_fefo_and_l02_blocked_lots(self):
        pid = self.meat()
        self.receive(pid, 1000, 40000, self.fridge, "2026-11-03")
        self.receive(pid, 1000, 40000, self.fridge, "2026-11-01")
        core.set_clock(core.now().replace(day=28))
        self.receive(pid, 1000, 40000, self.fridge, "2026-10-29")   # expired by today
        core.set_clock(core.now().replace(day=30))
        with self.assertRaises(ShopError):
            self.receive(pid, 1000, 1, self.fridge, None)            # L03 expiry required
        self.open_shift()
        s = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1500}]})
        allocs = self.store.all("SELECT l.expires, a.qty FROM sale_allocations a JOIN lots l ON l.id=a.lot_id ORDER BY l.expires")
        self.assertEqual([(a["expires"], a["qty"]) for a in allocs], [("2026-11-01", 1000), ("2026-11-03", 500)])
        self.assertEqual(s["total"], 60000)
        held = self.receive(pid, 500, 100, self.fridge, "2026-11-09", hold=True)["lines"][0]
        self.assertEqual(held["status"], "held")
        self.assertEqual(self.shop.available(pid), 500)

    def test_l04_expiry_boundary_uses_shop_clock(self):
        pid = self.meat()
        self.receive(pid, 1000, 100, self.fridge, "2026-10-30")
        self.open_shift()
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 100}]})
        core.set_clock(core.now().replace(hour=23, minute=59))
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 100}]})
        core.set_clock(core.now().replace(day=31, hour=0, minute=0))
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 100}]})

    def tagine_setup(self):
        meat = self.meat("BEEF", 30000)
        marinade = self.shop.add_product(self.owner, {"code": "MAR", "name_ar": "تتبيلة", "name_en": "Marinade",
                                                      "kind": "ingredient", "measure": "g", "allergens": "mustard"})
        tray = self.shop.add_product(self.owner, {"code": "TRAY", "name_ar": "صينية", "name_en": "Tray",
                                                  "kind": "packaging", "measure": "unit"})
        tagine = self.meat("TAG", 36000, kind="prepared")
        self.receive(meat, 2000, 60000, self.prep, "2026-11-02")
        self.receive(marinade, 500, 5000, self.prep, "2026-11-05")
        self.receive(tray, 5, 2500, self.prep, None)
        version = self.shop.save_recipe(self.owner, tagine, [
            {"product_id": meat, "qty": 400, "role": "meat"}, {"product_id": marinade, "qty": 100, "role": "food"},
            {"product_id": tray, "qty": 1, "role": "packaging"}], 2, "", 20, 500)["recipe_version"]
        lots = {r["product_id"]: r["id"] for r in self.store.all("SELECT id, product_id FROM lots")}
        return meat, marinade, tray, tagine, version, lots

    def test_p04_p07_tagine_reference_no_double_deduction(self):
        meat, marinade, tray, tagine, version, lots = self.tagine_setup()
        result = self.shop.produce(self.butcher, key(), {
            "kind": "recipe", "recipe_version_id": version,
            "inputs": [{"lot_id": lots[meat], "location_id": self.prep, "qty": 2000},
                       {"lot_id": lots[marinade], "location_id": self.prep, "qty": 500},
                       {"lot_id": lots[tray], "location_id": self.prep, "qty": 5}],
            "outputs": [{"product_id": tagine, "qty": 2500, "location_id": self.fridge}],
            "packs": [{"net_g": 500, "tare_g": 40, "meat_g": 400}] * 5})
        out = result["outputs"][0]
        self.assertEqual(out["expires"], "2026-11-01")      # min(meat 11-02, today+2)
        self.assertIn("mustard", out["allergens"])
        self.assertEqual(self.store.scalar("SELECT cost FROM stock_balances WHERE lot_id=?", (out["lot"],)), 67500)
        self.open_shift()
        meat_before = self.store.scalar("SELECT COALESCE(SUM(qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id WHERE l.product_id=?", (meat,))
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"pack_code": out["packs"][0]["code"]}]})
        self.assertEqual(sale["total"], 18000)
        self.assertEqual(self.shop.sale_detail(self.owner, sale["sale"])["cost_total"], 13500)
        meat_after = self.store.scalar("SELECT COALESCE(SUM(qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id WHERE l.product_id=?", (meat,))
        self.assertEqual(meat_before, meat_after)
        # B04: same unique pack scanned again is refused, in the same cart or later
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {"lines": [{"pack_code": out["packs"][0]["code"]}]})
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {"lines": [{"pack_code": out["packs"][1]["code"]},
                                                         {"pack_code": out["packs"][1]["code"]}]})
        # loose selling cannot eat stock reserved by labelled packs
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": tagine, "qty": 1}]})

    def test_p05_p06_expiry_bounds_and_atomic_rollback(self):
        meat, marinade, tray, tagine, version, lots = self.tagine_setup()
        before = list(self.store.db.iterdump())
        bad = {"kind": "recipe", "recipe_version_id": version,
               "inputs": [{"lot_id": lots[meat], "location_id": self.prep, "qty": 2000},
                          {"lot_id": lots[marinade], "location_id": self.prep, "qty": 500}],
               "outputs": [{"product_id": tagine, "qty": 2500, "location_id": self.fridge, "expires": "2026-11-04"}]}
        with self.assertRaises(ShopError):
            self.shop.produce(self.butcher, key(), bad)
        bad["outputs"][0]["expires"] = "2026-11-01"
        bad["outputs"][0]["qty"] = 2400                       # 100 g unexplained
        with self.assertRaises(ShopError):
            self.shop.produce(self.butcher, key(), bad)
        self.assertEqual(list(self.store.db.iterdump()), before)

    def test_p01_p02_p08_cutting_mass_cost_and_variance_approval(self):
        """06 reference: 10 kg raw for 3000 EGP -> 7 kg cuts, 2 kg mince, 0.5 kg bone, 0.5 kg loss."""
        raw = self.meat("RAW")
        cuts, mince, bone = self.meat("CUT"), self.meat("MIN"), self.meat("BON", 5000)
        self.receive(raw, 10000, 300000, self.prep, "2026-11-03")
        lot = self.store.scalar("SELECT id FROM lots")
        payload = {"kind": "cutting", "inputs": [{"lot_id": lot, "location_id": self.prep, "qty": 10000}],
                   "outputs": [{"product_id": cuts, "qty": 7000, "location_id": self.fridge},
                               {"product_id": mince, "qty": 2000, "location_id": self.fridge},
                               {"product_id": bone, "qty": 500, "location_id": self.fridge}],
                   "natural_loss_g": 500}
        with self.assertRaises(Forbidden):                    # no approved tolerance yet
            self.shop.produce(self.butcher, key(), payload)
        self.shop.update_settings(self.owner, {"cutting_loss_tolerance_permille": 60})
        result = self.shop.produce(self.butcher, key(), payload)
        costs = self.store.all("SELECT b.cost FROM stock_balances b JOIN lots l ON l.id=b.lot_id WHERE l.origin='production'")
        self.assertEqual(sum(c["cost"] for c in costs), 300000)
        self.assertEqual(len(result["outputs"]), 3)
        self.assertNotIn("cost", result["outputs"][0])        # butcher does not see cost
        # abnormal loss needs approval and is costed separately
        self.receive(raw, 1000, 30000, self.prep, "2026-11-03")
        lot2 = self.store.scalar("SELECT MAX(id) FROM lots WHERE product_id=?", (raw,))
        p2 = {"kind": "cutting", "inputs": [{"lot_id": lot2, "location_id": self.prep, "qty": 1000}],
              "outputs": [{"product_id": cuts, "qty": 800, "location_id": self.fridge}], "abnormal_loss_g": 200}
        with self.assertRaises(Forbidden):
            self.shop.produce(self.butcher, key(), p2)
        r2 = self.shop.produce(self.butcher, key(), p2, approver=self.approval())
        self.assertIsNotNone(r2["approved_by"])
        self.assertEqual(self.store.scalar("SELECT abnormal_loss_cost FROM productions ORDER BY id DESC LIMIT 1"), 6000)

    def test_t01_meat_return_goes_to_quarantine_with_original_cost(self):
        pid = self.meat()
        self.receive(pid, 1000, 30000, self.fridge)
        self.open_shift()
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1000}]})
        line = self.shop.sale_detail(self.cash1, sale["sale"])["lines"][0]
        r = self.shop.return_sale(self.cash1, key(), {"sale_id": sale["sale"], "reason": "quality",
                                                      "lines": [{"sale_line_id": line["id"], "qty": 400, "restock": True}]})
        self.assertEqual(r["lines"][0]["disposition"], "quarantine")
        self.assertEqual(r["amount"], 16000)
        self.assertEqual(self.shop.available(pid), 0)
        back = self.store.one("SELECT * FROM stock_balances WHERE location_id=?", (self.quarantine,))
        self.assertEqual((back["qty"], back["cost"]), (400, 12000))

    def test_q01_recall_isolates_descendants_and_finds_invoices(self):
        meat, marinade, tray, tagine, version, lots = self.tagine_setup()
        result = self.shop.produce(self.butcher, key(), {
            "kind": "recipe", "recipe_version_id": version,
            "inputs": [{"lot_id": lots[meat], "location_id": self.prep, "qty": 1000},
                       {"lot_id": lots[marinade], "location_id": self.prep, "qty": 250}],
            "outputs": [{"product_id": tagine, "qty": 1250, "location_id": self.fridge}]})
        self.open_shift()
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": tagine, "qty": 500}]})
        recall = self.shop.recall(self.manager, key(), lots[meat], "supplier notice")
        self.assertIn(result["outputs"][0]["lot"], recall["lots"])
        self.assertEqual([s["no"] for s in recall["sales"]], [sale["no"]])
        with self.assertRaises(ShopError):
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": tagine, "qty": 100}]})
        with self.assertRaises(Forbidden):
            self.shop.set_lot_status(self.cash1, key(), lots[meat], "released", "ok")
        receiver = self.store.tx(lambda: self.shop.auth.create_user("recv", "R", "receiver", "test-pass-123"))
        with self.assertRaises(Forbidden):  # a recall cannot be softened into a plain hold
            self.shop.set_lot_status(self.actor("recv"), key(), lots[meat], "held", "x")

    def test_prepared_product_cannot_come_from_cutting(self):
        raw = self.meat("RAW")
        tagine = self.meat("TAG", kind="prepared")
        self.receive(raw, 1000, 100, self.prep, "2026-11-10")
        lot = self.store.scalar("SELECT id FROM lots")
        with self.assertRaises(ShopError) as err:
            self.shop.produce(self.butcher, key(), {"kind": "cutting", "inputs": [{"lot_id": lot, "location_id": self.prep, "qty": 1000}],
                                                    "outputs": [{"product_id": tagine, "qty": 1000, "location_id": self.fridge}]})
        self.assertEqual(err.exception.code, "prepared_needs_recipe")


class Isolation(unittest.TestCase):
    def test_a04_training_and_shop_folders_never_mix(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            Store(tmp, "training").close()
            with self.assertRaises(ShopError):
                Store(tmp, "shop")

    def test_d02_newer_database_refused(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            s = Store(tmp)
            s.db.execute("INSERT INTO schema_migrations VALUES(99,'future','x')")
            s.close()
            with self.assertRaises(ShopError):
                Store(tmp)


if __name__ == "__main__":
    unittest.main()
