"""The eight rows of the 40-case matrix that were not implemented on 31 Oct 2026
(docs/MARKET_COVERAGE_20261031.md): 2, 3, 5, 6, 9, 14, 26, 38. Each test names
its row and expects the correct behaviour, not merely 'no error'."""

import datetime as dt
import sqlite3
import unittest

from helpers import CAIRO, ShopFixture, ean, key
from server import core
from server.core import Forbidden, ShopError


class OpenRows(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        self.sup = self.shop.add_party(self.owner, "supplier", "S")

    def code(self, fn, *a, **k):
        with self.assertRaises(ShopError) as err:
            fn(*a, **k)
        return err.exception.code

    def advance(self, days, hour=10):
        core.set_clock(dt.datetime(2026, 10, 30, hour, 0, tzinfo=CAIRO) + dt.timedelta(days=days))

    # ---------------- rows 1-2: invoice before the goods
    def test_row02_invoice_before_goods_adds_no_stock_and_no_debt_then_deliveries_take_what_is_left(self):
        p = self.grocery()
        inv = self.shop.register_supplier_invoice(self.owner, key(), {"supplier_id": self.sup, "number": "F-1",
            "invoice_date": "2026-10-29", "lines": [{"product_id": p, "qty": 10, "free_qty": 2, "line_cost": 10000}]})
        self.assertEqual((inv["stock_added"], self.shop.available(p), self.shop.supplier_balance(self.sup)), (0, 0, 0))
        base = {"invoice_id": inv["invoice"], "lines": [{"product_id": p, "qty": 6, "free_qty": 1, "location_id": self.shelf,
                                                         "line_cost": 1}]}           # a typed cost is ignored: the invoice prices it
        self.shop.receive(self.owner, key(), base)
        self.assertEqual((self.shop.available(p), self.shop.supplier_balance(self.sup)), (7, 6000))
        self.assertEqual(self.code(self.shop.receive, self.owner, key(), dict(base, lines=[dict(base["lines"][0], qty=5, free_qty=0)])),
                         "exceeds_invoice")
        self.shop.receive(self.owner, key(), dict(base, lines=[dict(base["lines"][0], qty=4, free_qty=1)]))
        self.assertEqual((self.shop.available(p), self.shop.supplier_balance(self.sup)), (12, 10000))   # exact at the end
        self.assertEqual(self.shop.supplier_invoices(self.owner), [])                                      # complete: no longer open
        self.assertEqual(self.code(self.shop.register_supplier_invoice, self.owner, key(), {"supplier_id": self.sup, "number": "F-1",
            "invoice_date": "2026-10-29", "lines": [{"product_id": p, "qty": 1, "line_cost": 1}]}), "duplicate_supplier_invoice")

    def test_row02_a_product_not_on_the_invoice_and_a_closed_invoice_are_refused(self):
        p, q = self.grocery(), self.grocery("G2", barcodes=(ean("622123456780"),))
        inv = self.shop.register_supplier_invoice(self.owner, key(), {"supplier_id": self.sup, "number": "F-2",
            "invoice_date": "2026-10-29", "lines": [{"product_id": p, "qty": 10, "line_cost": 5000}]})["invoice"]
        self.assertEqual(self.code(self.shop.receive, self.owner, key(), {"invoice_id": inv, "lines": [
            {"product_id": q, "qty": 1, "location_id": self.shelf}]}), "product_not_on_invoice")
        self.shop.receive(self.owner, key(), {"invoice_id": inv, "lines": [{"product_id": p, "qty": 8, "location_id": self.shelf}]})
        closed = self.shop.close_supplier_invoice(self.owner, key(), inv, "supplier will not send the rest")
        self.assertEqual(closed["not_delivered"], [{"product": p, "qty": 2}])
        self.assertEqual(self.shop.supplier_balance(self.sup), 4000)        # nothing owed for what never came
        self.assertEqual(self.code(self.shop.receive, self.owner, key(), {"invoice_id": inv, "lines": [
            {"product_id": p, "qty": 1, "location_id": self.shelf}]}), "supplier_invoice_closed")

    # ---------------- row 3: final invoice after receipt and sale
    def test_row03_cost_correction_after_partial_sale_splits_between_stock_and_cost_of_sales_without_rewriting(self):
        p = self.grocery(price=2000)
        r = self.shop.receive(self.owner, key(), {"supplier_id": self.sup, "supplier_invoice": "I-3", "lines": [
            {"product_id": p, "qty": 10, "line_cost": 10000, "location_id": self.shelf}]})
        self.open_shift(self.manager, 0)
        q = self.shop.quote(self.manager, {"lines": [{"product_id": p, "qty": 4}]})
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 4}], "quote_hash": q["quote_hash"]})
        old_line_cost = self.store.scalar("SELECT cost FROM sale_lines WHERE sale_id=?", (sale["sale"],))
        adj = self.shop.supplier_adjustment(self.owner, key(), {"kind": "invoice_correction", "supplier_id": self.sup,
                                                               "receipt_id": r["receipt"], "amount": 1000, "reference": "FINAL-3"})
        self.assertEqual((adj["to_stock"], adj["to_cost_of_sales"]), (600, 400))            # 6 left of 10
        self.assertEqual(self.store.scalar("SELECT cost FROM stock_balances WHERE lot_id=?", (r["lines"][0]["lot"],)), 6000 + 600)
        self.assertEqual(self.store.scalar("SELECT cost FROM sale_lines WHERE sale_id=?", (sale["sale"],)), old_line_cost)  # the past stays
        self.assertEqual(self.shop.supplier_balance(self.sup), 11000)
        rep = self.shop.day_report(self.owner)
        self.assertEqual((rep["cost_adjustments"], rep["cost_of_sales"]), (400, 4000 + 400))
        self.assertTrue(self.shop.reconcile()["ok"])
        self.assertEqual(self.code(self.shop.supplier_adjustment, self.owner, key(), {"kind": "invoice_correction",
            "supplier_id": self.sup, "receipt_id": r["receipt"], "amount": 5, "reference": "FINAL-3"}), "duplicate_credit_note")

    def test_row03_a_lower_final_price_never_makes_stock_cost_negative(self):
        p = self.grocery()
        r = self.shop.receive(self.owner, key(), {"supplier_id": self.sup, "lines": [
            {"product_id": p, "qty": 10, "line_cost": 1000, "location_id": self.shelf}]})
        adj = self.shop.supplier_adjustment(self.owner, key(), {"kind": "invoice_correction", "supplier_id": self.sup,
                                                               "receipt_id": r["receipt"], "amount": -1500, "reference": "CN-3"})
        self.assertEqual(adj["to_stock"], -1000)                                       # down to zero, not below
        self.assertEqual(adj["to_cost_of_sales"], 0)       # nothing was sold
        self.assertEqual(adj['to_variance'], -500)       # excess rebate is explicit, never negative inventory
        self.assertEqual(self.store.scalar("SELECT cost FROM stock_balances WHERE lot_id=?", (r["lines"][0]["lot"],)), 0)

    # ---------------- row 5: gift and sample
    def test_row05_gift_enters_at_zero_without_debt_and_a_sample_is_held_not_sold(self):
        p, q = self.grocery(), self.grocery("G2", barcodes=(ean("622123456780"),))
        r = self.shop.receive(self.owner, key(), {"supplier_id": self.sup, "lines": [
            {"product_id": p, "qty": 6, "line_kind": "gift", "location_id": self.shelf},
            {"product_id": q, "qty": 2, "line_kind": "sample", "location_id": self.shelf}]})
        self.assertEqual(self.shop.supplier_balance(self.sup), 0)
        self.assertEqual([l["status"] for l in r["lines"]], ["released", "held"])
        self.assertEqual((self.shop.available(p), self.shop.available(q)), (6, 0))
        self.assertEqual(self.store.scalar("SELECT SUM(cost) FROM stock_balances"), 0)
        self.assertEqual(self.code(self.shop.receive, self.owner, key(), {"supplier_id": self.sup, "lines": [
            {"product_id": p, "qty": 1, "line_kind": "gift", "line_cost": 100, "location_id": self.shelf}]}), "gift_has_no_cost")
        self.assertEqual(self.shop.day_report(self.owner)["gift_lines"], 2)

    # ---------------- row 6: later rebate
    def test_row06_rebate_on_a_receipt_lowers_stock_cost_and_debt_rebate_for_a_period_is_shown_apart(self):
        p = self.grocery()
        r = self.shop.receive(self.owner, key(), {"supplier_id": self.sup, "lines": [
            {"product_id": p, "qty": 10, "line_cost": 10000, "location_id": self.shelf}]})
        a = self.shop.supplier_adjustment(self.owner, key(), {"kind": "rebate", "supplier_id": self.sup,
                                                             "receipt_id": r["receipt"], "amount": 500, "reference": "VR-1"})
        self.assertEqual((a["amount"], a["to_stock"], a["to_cost_of_sales"]), (-500, -500, 0))
        b = self.shop.supplier_adjustment(self.owner, key(), {"kind": "rebate", "supplier_id": self.sup,
                                                             "amount": 300, "reference": "VR-Q3"})
        self.assertEqual(b["unassigned"], -300)
        self.assertEqual(self.shop.supplier_balance(self.sup), 10000 - 800)
        self.assertEqual(self.shop.day_report(self.owner)["supplier_rebates"], 300)
        self.assertEqual(self.code(self.shop.supplier_adjustment, self.owner, key(), {"kind": "rebate", "supplier_id": self.sup,
                                                                                      "amount": 1, "reference": ""}), "reference_required")
        self.assertRaises(Forbidden, self.shop.supplier_adjustment, self.cash1, key(), {"kind": "rebate", "supplier_id": self.sup,
                                                                                         "amount": 1, "reference": "X"})

    # ---------------- row 9: carton size changes
    def test_row09_carton_size_change_keeps_old_receipts_and_moves_the_barcode(self):
        carton = ean("622999000001")
        p = self.grocery(carton=carton)
        pk = self.store.scalar("SELECT id FROM packagings WHERE product_id=?", (p,))
        self.shop.receive(self.owner, key(), {"lines": [{"product_id": p, "packaging_id": pk, "packs": 2, "line_cost": 2400,
                                                         "location_id": self.shelf}]})
        change = self.shop.replace_packaging(self.owner, key(), pk, 10, 1, "supplier now packs 10")
        self.assertEqual(change["moved_barcodes"], [carton])
        self.assertEqual(self.code(self.shop.receive, self.owner, key(), {"lines": [{"product_id": p, "packaging_id": pk, "packs": 1,
                                                                                      "line_cost": 1, "location_id": self.shelf}]}), "packaging_retired")
        self.shop.receive(self.owner, key(), {"lines": [{"product_id": p, "packaging_id": change["new"], "packs": 1, "line_cost": 1000,
                                                         "location_id": self.shelf}]})
        self.assertEqual(self.shop.available(p), 24 + 10)
        self.assertEqual(self.shop.resolve_scan(self.cash1, carton)["qty"], 10)
        self.assertEqual([r[0] for r in self.store.db.execute(
            "SELECT COALESCE(rl.factor_num, k.factor_num) FROM receipt_lines rl JOIN packagings k ON k.id=rl.packaging_id ORDER BY rl.id")],
            [12, 10])                                                                        # each receipt keeps its own carton
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("UPDATE packagings SET factor_num=11 WHERE id=?", (change["new"],))

    # ---------------- row 14: thaw then pack
    def thaw_setup(self):
        freezer = self.shop.add_location(self.owner, "FZ", "مجمد", "Freezer", "freezer", 0)
        m = self.meat()
        r = self.receive(m, 5000, 200000, location=freezer, expires="2027-03-01")
        return freezer, m, r["lines"][0]["lot"]

    def test_row14_thaw_needs_an_approved_policy_and_never_restarts_or_extends_shelf_life(self):
        freezer, m, lot = self.thaw_setup()
        self.assertEqual(self.code(self.shop.thaw, self.owner, key(), lot, freezer, self.prep, 2000), "thaw_policy_missing")
        self.shop.update_settings(self.owner, {"thaw_shelf_life_days": 2})
        t = self.shop.thaw(self.owner, key(), lot, freezer, self.prep, 2000)
        self.assertEqual((t["expires"], t["original_expires"]), ("2026-11-01", "2027-03-01"))
        self.assertEqual(self.code(self.shop.transfer, self.owner, key(), t["lot"], self.prep, freezer, 500), "no_refreeze")
        self.assertEqual(self.code(self.shop.thaw, self.owner, key(), lot, freezer, freezer, 1), "no_refreeze")
        self.assertTrue(self.shop.reconcile()["ok"])

    def test_row14_thaw_cannot_extend_an_original_expiry_that_is_earlier(self):
        freezer = self.shop.add_location(self.owner, "FZ", "مجمد", "Freezer", "freezer", 0)
        m = self.meat()
        lot = self.receive(m, 1000, 40000, location=freezer, expires="2026-10-31")["lines"][0]["lot"]
        self.shop.update_settings(self.owner, {"thaw_shelf_life_days": 3})
        self.assertEqual(self.shop.thaw(self.owner, key(), lot, freezer, self.prep, 1000)["expires"], "2026-10-31")

    def test_row14_cuts_made_from_thawed_meat_keep_the_earlier_date_and_cannot_be_frozen(self):
        freezer, m, lot = self.thaw_setup()
        self.shop.update_settings(self.owner, {"thaw_shelf_life_days": 2, "cutting_loss_tolerance_permille": 100})
        t = self.shop.thaw(self.owner, key(), lot, freezer, self.prep, 2000)
        cut = self.meat("M2")
        payload = {"kind": "cutting", "inputs": [{"lot_id": t["lot"], "location_id": self.prep, "qty": 2000}],
                   "outputs": [{"product_id": cut, "qty": 1900, "location_id": freezer, "expires": "2026-11-01"}], "natural_loss_g": 100}
        self.assertEqual(self.code(self.shop.produce, self.butcher, key(), payload), "no_refreeze")
        payload["outputs"][0]["location_id"] = self.fridge
        out = self.shop.produce(self.butcher, key(), payload)
        child = out["outputs"][0]["lot"] if "outputs" in out else self.store.scalar("SELECT MAX(id) FROM lots")
        self.assertLessEqual(self.store.scalar("SELECT expires FROM lots WHERE id=?", (child,)), "2026-11-01")
        self.assertTrue(self.shop._thawed(child))

    # ---------------- row 26: buy 2 get 1, then partial return
    def promo_sale(self, qty=3):
        p = self.grocery(price=1000)
        self.receive(p, 10, 3000, expires=None)
        self.shop.add_promotion(self.manager, key(), p, 2, 1, "2026-10-01")
        self.open_shift(self.manager, 10000)
        cart = {"lines": [{"product_id": p, "qty": qty}]}
        q = self.shop.quote(self.manager, cart)
        s = self.shop.sell(self.manager, key(), dict(cart, quote_hash=q["quote_hash"]))
        return p, s

    def test_row26_server_applies_the_offer_and_returning_the_free_piece_refunds_nothing(self):
        p, s = self.promo_sale(3)
        self.assertEqual((s["total"], s["promo"]), (2000, 1000))
        line = self.shop.sale_detail(self.owner, s["sale"])["lines"][0]["id"]
        ret = lambda n: self.shop.return_sale(self.manager, key(), {"sale_id": s["sale"], "reason": "wrong_item", "method": "cash",
                                                                   "lines": [{"sale_line_id": line, "qty": n}]})
        self.assertEqual(ret(1)["amount"], 0)          # keeps 2, which cost 2000 without the offer
        self.assertEqual(ret(1)["amount"], 1000)
        self.assertEqual(ret(1)["amount"], 1000)       # all back: exactly what was paid
        self.assertTrue(self.shop.reconcile()["ok"])

    def test_row26_offer_counts_whole_groups_only_and_ends_cleanly(self):
        p, s = self.promo_sale(5)                       # 5 = one group of 3 + 2 paid
        self.assertEqual((s["total"], s["promo"]), (4000, 1000))
        offer = self.store.scalar("SELECT id FROM promotions")
        self.assertEqual(self.code(self.shop.add_promotion, self.manager, key(), p, 3, 1, "2026-10-15"), "promotion_overlaps")
        self.shop.end_promotion(self.manager, key(), offer)
        self.assertEqual(self.shop.quote(self.manager, {"lines": [{"product_id": p, "qty": 3}]})["promo"], 0)
        self.assertRaises(Forbidden, self.shop.add_promotion, self.cash1, key(), p, 1, 1, "2026-10-30")

    # ---------------- row 38: day close and dated correction
    def test_row38_closed_day_takes_no_posting_and_later_corrections_are_listed_with_permission(self):
        p = self.grocery(price=1000)
        self.receive(p, 10, 3000, expires=None)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=10000)
        self.open_shift(self.manager, 5000)
        s = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 2}], "customer_id": c,
                                                 "payments": [{"method": "credit", "amount": 2000}]},
                           approver=self.approval())
        pay = self.shop.customer_payment(self.manager, key(), c, 2000)
        self.assertEqual(self.code(self.shop.close_day, self.owner, key(), "2026-10-30"), "shift_open_on_day")
        shift = self.store.scalar("SELECT id FROM shifts")
        self.shop.close_shift(self.manager, key(), self.shop.shift_expected(shift))
        closed = self.shop.close_day(self.owner, key(), "2026-10-30")
        # work after the close belongs to the NEXT business day; the closed day takes nothing (gap row 15)
        late = self.shop.receive(self.owner, key(), {"lines": [{"product_id": p, "qty": 1, "line_cost": 1, "location_id": self.shelf}]})
        self.assertEqual(self.store.scalar("SELECT business_day FROM ops WHERE id=?", (late["op"],)), "2026-10-31")
        self.advance(1)
        status = self.shop.day_status(self.owner, "2026-10-30")
        self.assertTrue(status["snapshot_ok"])
        self.assertEqual(status["snapshot_hash"], closed["snapshot_hash"])
        self.open_shift(self.cash1, 5000)
        entry = self.store.scalar("SELECT id FROM customer_entries WHERE kind='payment'")
        # a correction of a closed day's money needs close_day, whoever may otherwise post it
        sale_op = self.store.scalar("SELECT op_id FROM sales")
        self.assertRaises(Forbidden, self.store.tx, lambda: self.shop._note_correction(1, sale_op, "x", "y", "z", self.cash1, True))
        self.open_shift(self.manager, 5000)
        self.shop.reverse_customer_payment(self.manager, key(), entry, "wrong customer")   # manager holds close_day
        status = self.shop.day_status(self.owner, "2026-10-30")
        self.assertEqual([(x["kind"], x["reason"]) for x in status["corrections"]], [("payment_reversal", "wrong customer")])
        self.assertEqual(status["snapshot_hash"], closed["snapshot_hash"])                  # the closed day itself is untouched
        self.advance(-1, 23)                                                                # clock set back to the closed day
        self.assertEqual(self.code(self.shop.cash_move, self.manager, key(), "expense", 100, "x"), "day_closed")


if __name__ == "__main__":
    unittest.main()
