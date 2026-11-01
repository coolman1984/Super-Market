"""The whole operating chain and the 40-case market matrix
(docs/MARKET_COVERAGE_20261031.md). Each test names the matrix row it proves."""

import pathlib
import tempfile
import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import backup
from server.core import Conflict, Forbidden, ShopError
from server.db import Store
from server.services import Shop


class FullChain(ShopFixture, unittest.TestCase):
    """buy -> receive -> prepare -> sell -> collect -> return -> reverse -> count -> close -> backup -> restore."""

    def setUp(self):
        self.make_shop()

    def test_one_business_day_end_to_end(self):
        sup = self.shop.add_party(self.owner, "supplier", "Supplier")
        cust = self.shop.add_party(self.owner, "customer", "Account customer", credit_limit=100000)
        juice = self.grocery(price=3500)
        beef = self.meat("BEEF", 30000)
        marinade = self.shop.add_product(self.owner, {"code": "MAR", "name_ar": "تتبيلة", "name_en": "Marinade", "kind": "ingredient",
                                                      "measure": "g", "allergens": "celery"})
        tray = self.shop.add_product(self.owner, {"code": "TRAY", "name_ar": "صينية", "name_en": "Tray", "kind": "packaging", "measure": "unit"})
        tagine = self.meat("TAG", 36000, kind="prepared")
        self.open_shift(self.manager, 50000)
        # receive (partly on credit, partly paid from the drawer)
        r = self.shop.receive(self.manager, key(), {"supplier_id": sup, "supplier_invoice": "A-1", "invoice_date": "2026-10-30", "paid_now": 20000,
            "lines": [{"product_id": juice, "qty": 24, "line_cost": 24000, "location_id": self.shelf, "expires": "2027-01-01"},
                      {"product_id": beef, "gross_g": 5400, "tare_g": 400, "line_cost": 150000, "location_id": self.prep, "expires": "2026-11-05"},
                      {"product_id": marinade, "qty": 1000, "line_cost": 10000, "location_id": self.prep, "expires": "2026-11-06"},
                      {"product_id": tray, "qty": 10, "line_cost": 5000, "location_id": self.prep}]})
        lots = {l["lot_no"]: l["lot"] for l in r["lines"]}
        beef_lot, mar_lot, tray_lot = r["lines"][1]["lot"], r["lines"][2]["lot"], r["lines"][3]["lot"]
        self.assertEqual(self.shop.supplier_balance(sup), 189000 - 20000)
        # prepare: 5 tagines of 400 g meat + 100 g marinade
        v = self.shop.save_recipe(self.owner, tagine, [{"product_id": beef, "qty": 400, "role": "meat"},
                                                       {"product_id": marinade, "qty": 100, "role": "food"},
                                                       {"product_id": tray, "qty": 1, "role": "packaging"}], 2, "celery", 50, 500)["recipe_version"]
        made = self.shop.produce(self.butcher, key(), {"kind": "recipe", "recipe_version_id": v,
            "inputs": [{"lot_id": beef_lot, "location_id": self.prep, "qty": 2000}, {"lot_id": mar_lot, "location_id": self.prep, "qty": 500},
                       {"lot_id": tray_lot, "location_id": self.prep, "qty": 5}],
            "outputs": [{"product_id": tagine, "qty": 2500, "location_id": self.fridge}],
            "packs": [{"net_g": 500, "tare_g": 40}] * 5})
        packs = made["outputs"][0]["packs"]
        self.assertTrue(all(p["meat_g"] == 400 and p["meat_basis"] == "estimated" for p in packs))
        # sell: groceries by scan, one tagine, credit sale, then collection
        self.shop.sell(self.manager, key(), {"lines": [{"product_id": juice, "qty": 3}], "cash_tendered": 20000})
        tag_sale = self.shop.sell(self.manager, key(), {"lines": [{"pack_code": packs[0]["code"]}], "cash_tendered": 20000})
        credit = self.shop.sell(self.manager, key(), {"lines": [{"pack_code": packs[1]["code"]}], "customer_id": cust,
                                                      "payments": [{"method": "credit", "amount": 18000}]})
        entry = self.shop.customer_payment(self.manager, key(), cust, 18000)["entry"]
        # return the credit-sold tagine: collected money goes back in cash, quarantine for the goods
        line = self.shop.sale_detail(self.manager, credit["sale"])["lines"][0]["id"]
        ret = self.shop.return_sale(self.manager, key(), {"sale_id": credit["sale"], "reason": "quality", "method": "original",
                                                          "lines": [{"sale_line_id": line, "qty": 500}]})
        self.assertEqual(ret["refunds"], [{"method": "cash", "amount": 18000}])
        self.assertEqual(ret["lines"][0]["disposition"], "quarantine")
        with self.assertRaises(ShopError) as err:                                   # the same collection cannot be reversed afterwards
            self.shop.reverse_customer_payment(self.manager, key(), entry, "x")
        self.assertEqual(err.exception.code, "collection_already_refunded")
        self.assertEqual(self.shop.customer_balance(cust), 0)
        # count the shelf blind, then close the shift
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        for row in self.shop.count_sheet(self.counter, count)["lots"]:
            self.shop.record_count(self.counter, count, row["lot_id"], 21)            # 24 - 3 sold
        self.assertEqual(self.shop.approve_count(self.manager, key(), count)["differences"], [])
        expected = self.shop.shift_expected(self.store.scalar("SELECT id FROM shifts"))
        closed = self.shop.close_shift(self.manager, key(), expected, None)
        self.assertEqual(closed["difference"], 0)
        self.assertTrue(self.shop.reconcile()["ok"])
        # backup and restore elsewhere: the same numbers
        done = backup.create_backup(self.store, self.folder / "bk")
        target = self.folder / "restored"
        backup.restore_backup(done["folder"], target)
        restored = Store(target)
        try:
            shop2 = Shop(restored)
            self.assertTrue(shop2.reconcile()["ok"])
            self.assertEqual(shop2.customer_balance(cust), 0)
            self.assertEqual(shop2.supplier_balance(sup), 169000)
            self.assertEqual(restored.scalar("SELECT COUNT(*) FROM sales"), 3)
        finally:
            restored.close()


class Matrix(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    # row 1 (partial): the second delivery of the same invoice is accepted only when confirmed
    def test_row01_second_delivery_of_the_same_invoice_needs_confirmation(self):
        sup = self.shop.add_party(self.owner, "supplier", "S")
        p = self.grocery()
        line = {"product_id": p, "qty": 5, "line_cost": 500, "location_id": self.shelf}
        base = {"supplier_id": sup, "supplier_invoice": "INV-9", "invoice_date": "2026-10-30", "lines": [line]}
        self.shop.receive(self.owner, key(), base)
        with self.assertRaises(ShopError) as err:
            self.shop.receive(self.owner, key(), base)
        self.assertEqual(err.exception.code, "duplicate_supplier_invoice")
        self.shop.receive(self.owner, key(), dict(base, confirm_duplicate=True))          # the remainder of the shipment
        self.assertEqual(self.shop.available(p), 10)

    # row 8
    def test_row08_partial_supplier_return_and_a_different_credit_note(self):
        sup = self.shop.add_party(self.owner, "supplier", "S")
        p = self.grocery()
        r = self.shop.receive(self.owner, key(), {"supplier_id": sup, "supplier_invoice": "I1", "lines": [
            {"product_id": p, "qty": 10, "line_cost": 10000, "location_id": self.shelf}]})
        lot = r["lines"][0]["lot"]
        stranger = self.shop.add_party(self.owner, "supplier", "Other")
        with self.assertRaises(ShopError) as err:
            self.shop.supplier_return(self.owner, key(), stranger, lot, self.shelf, 2, "damaged")
        self.assertEqual(err.exception.code, "lot_not_from_supplier")
        ret = self.shop.supplier_return(self.owner, key(), sup, lot, self.shelf, 4, "damaged", "leaking")
        self.assertEqual((ret["cost"], self.shop.available(p), self.shop.supplier_balance(sup)), (4000, 6, 10000))   # debt unchanged yet
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM stock_entries WHERE kind='waste'"), 0)             # not waste
        note = self.shop.supplier_credit_note(self.owner, key(), ret["supplier_return"], 3500, "CN-77")
        self.assertEqual((note["balance"], note["difference_from_cost"]), (6500, -500))
        with self.assertRaises(ShopError):
            self.shop.supplier_credit_note(self.owner, key(), ret["supplier_return"], 100, "CN-77")
        statement = self.shop.supplier_statement(self.owner, sup)
        self.assertEqual([x["kind"] for x in statement["lines"]], ["invoice", "credit_note"])
        self.assertEqual(statement["open_returns"][0]["credited"], 3500)
        self.assertTrue(statement["open_returns"][0]["open"])             # a part-credit does not complete the return
        self.shop.supplier_credit_note(self.owner, key(), ret["supplier_return"], 500, "CN-78")   # the rest arrives later
        self.assertFalse(self.shop.supplier_statement(self.owner, sup)["open_returns"][0]["open"])

    # row 10: the same barcode for another product or packaging is refused, never merged silently
    def test_row10_the_same_barcode_is_refused_not_merged(self):
        self.grocery("G1", barcodes=(ean("622123456789"),))
        with self.assertRaises(ShopError) as err:
            self.grocery("G2", barcodes=(ean("622123456789"),))
        self.assertEqual(err.exception.code, "barcode_taken")
        self.assertIsNone(self.shop.store.scalar("SELECT 1 FROM products WHERE code='G2'"))      # nothing half-saved

    # row 17: a substitute's allergens reach the product
    def test_row17_substitute_allergens_reach_the_lot_and_label(self):
        beef = self.meat("BEEF")
        lamb = self.meat("LAMB", allergens="sesame")
        out = self.meat("TAG", kind="prepared")
        self.receive(lamb, 1000, 10000, self.prep, "2026-11-05")
        self.store.tx(lambda: None)
        v = self.shop.save_recipe(self.owner, out, [{"product_id": beef, "qty": 1000, "role": "meat", "alternatives": [lamb]}], 2, "", 50, 1000)["recipe_version"]
        lot = self.store.scalar("SELECT id FROM lots")
        r = self.shop.produce(self.butcher, key(), {"kind": "recipe", "recipe_version_id": v,
                                                    "inputs": [{"lot_id": lot, "location_id": self.prep, "qty": 1000}],
                                                    "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                    "packs": [{"net_g": 1000}]})
        self.assertIn("sesame", r["outputs"][0]["allergens"])
        self.assertEqual(self.shop.pack_label(self.butcher, r["outputs"][0]["packs"][0]["code"])["allergens"], "sesame")

    # row 19 + row 20
    def test_row19_20_packs_of_different_weights_and_label_reprint_creates_nothing(self):
        p = self.meat("RAW")
        r = self.receive(p, 2000, 20000, self.fridge)
        out = self.meat("OUT")
        made = self.shop.produce(self.owner, key(), {"kind": "cutting", "inputs": [{"lot_id": r["lines"][0]["lot"], "location_id": self.fridge, "qty": 2000}],
                                                     "outputs": [{"product_id": out, "qty": 2000, "location_id": self.fridge}],
                                                     "packs": [{"net_g": 450}, {"net_g": 550}, {"net_g": 1000}]})
        packs = made["outputs"][0]["packs"]
        self.assertEqual(sum(x["net_g"] for x in packs), 2000)
        self.assertEqual(sorted(x["price"] for x in packs), [18000, 22000, 40000])   # 40000 per kg, per pack weight
        before = (self.store.scalar("SELECT COUNT(*) FROM packs"), self.store.scalar("SELECT COUNT(*) FROM stock_entries"))
        label = self.shop.pack_label(self.owner, packs[0]["code"])
        self.assertTrue(label["reprint"])
        self.assertEqual((self.store.scalar("SELECT COUNT(*) FROM packs"), self.store.scalar("SELECT COUNT(*) FROM stock_entries")), before)

    # rows 27, 28
    def test_row27_28_two_collections_overpayment_and_collection_after_partial_return(self):
        p = self.grocery(price=10000)
        self.receive(p, 5, 1000, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=100000)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 2}], "customer_id": c,
                                                 "payments": [{"method": "credit", "amount": 20000}]})
        self.shop.customer_payment(self.manager, key(), c, 4000)
        self.shop.customer_payment(self.manager, key(), c, 6000)
        with self.assertRaises(ShopError) as err:
            self.shop.customer_payment(self.manager, key(), c, 10001)
        self.assertEqual(err.exception.code, "payment_exceeds_debt")
        line = self.shop.sale_detail(self.manager, s["sale"])["lines"][0]["id"]
        r = self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "quality", "method": "original",
                                                        "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual([x["method"] for x in r["refunds"]], ["credit_cancel"] if self.shop.customer_balance(c) == 0 else ["credit_cancel"])
        self.assertEqual(self.shop.customer_balance(c), 0)                               # 20000 - 10000 collected - 10000 cancelled
        self.assertEqual(self.shop._sale_outstanding(s["sale"]), 0)
        self.assertEqual(self.shop.customer_statement(self.manager, c)["balance"], 0)

    # row 33
    def test_row33_quality_defect_is_always_accepted_change_of_mind_on_meat_needs_approval(self):
        p = self.meat()
        self.receive(p, 2000, 20000, self.fridge)
        self.open_shift(self.cash1)
        s = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 1000}]})
        line = self.shop.sale_detail(self.cash1, s["sale"])["lines"][0]["id"]
        with self.assertRaises(Forbidden):
            self.shop.return_sale(self.cash1, key(), {"sale_id": s["sale"], "reason": "change_of_mind", "method": "original",
                                                      "lines": [{"sale_line_id": line, "qty": 500}]})
        ok = self.shop.return_sale(self.cash1, key(), {"sale_id": s["sale"], "reason": "quality", "method": "original",
                                                       "lines": [{"sale_line_id": line, "qty": 500}]})
        self.assertEqual(ok["lines"][0]["disposition"], "quarantine")                     # a refund never puts meat back on sale

    # row 35
    def test_row35_a_missing_pack_is_written_off_as_a_pack_before_the_count_balances(self):
        p = self.meat()
        r = self.receive(p, 1000, 10000, self.fridge)
        out = self.meat("OUT")
        made = self.shop.produce(self.owner, key(), {"kind": "cutting", "inputs": [{"lot_id": r["lines"][0]["lot"], "location_id": self.fridge, "qty": 1000}],
                                                     "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                     "packs": [{"net_g": 500}, {"net_g": 500}]})
        lot = made["outputs"][0]["lot"]
        count = self.shop.open_count(self.counter, self.fridge)["count"]
        self.shop.record_count(self.counter, count, lot, 500)                            # one pack is gone
        with self.assertRaises(ShopError) as err:
            self.shop.approve_count(self.manager, key(), count)
        self.assertEqual(err.exception.code, "count_affects_packs")
        self.shop.cancel_count(self.manager, count, "a pack is missing; writing it off first")
        self.shop.waste_pack(self.owner, key(), made["outputs"][0]["packs"][0]["code"], "unknown")
        count2 = self.shop.open_count(self.counter, self.fridge)["count"]
        self.shop.record_count(self.counter, count2, lot, 500)
        self.assertEqual(self.shop.approve_count(self.manager, key(), count2)["differences"], [])
        self.assertTrue(self.shop.reconcile()["ok"])

    # reports used by the matrix (aging, period)
    def test_aging_and_period_report(self):
        p = self.grocery(price=10000)
        self.receive(p, 5, 1000, expires=None)
        sup = self.shop.add_party(self.owner, "supplier", "S")
        self.shop.receive(self.owner, key(), {"supplier_id": sup, "supplier_invoice": "I", "lines": [
            {"product_id": p, "qty": 1, "line_cost": 5000, "location_id": self.shelf}]})
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=100000)
        self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c,
                                             "payments": [{"method": "credit", "amount": 10000}]})
        aging = self.shop.aging(self.owner)
        self.assertEqual(aging["customers"][0]["d0_30"], 10000)
        self.assertEqual(aging["suppliers"][0]["d0_30"], 5000)
        period = self.shop.period_report(self.owner, "2026-10-01", "2026-10-31")
        self.assertEqual((period["totals"]["sales"], period["totals"]["sales_total"]), (1, 10000))
        with self.assertRaises(ShopError):
            self.shop.period_report(self.owner, "2026-10-31", "2026-10-01")


if __name__ == "__main__":
    unittest.main()
