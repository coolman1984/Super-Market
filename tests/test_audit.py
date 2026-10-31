"""Acceptance tests for the independent audit of 30 Oct 2026
(docs/INDEPENDENT_AUDIT_20261030.md). Each test expects the CORRECT
behaviour; every one failed on c47924b before its fix.
docs/audit/reproduce_review.py still demonstrates the old faults on that commit.
"""

import sqlite3
import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import barcode as bc
from server.core import Conflict, Forbidden, ShopError
from server.scanner import ScannerHub


class Money(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def line_of(self, sale):
        return self.shop.sale_detail(self.owner, sale["sale"])["lines"][0]["id"]

    def test_r01_one_item_paid_half_cash_half_card_is_refunded_by_both(self):
        p = self.grocery(price=10000)
        self.receive(p, 1, 5000, expires=None)
        self.open_shift()
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 1}],
                                                 "payments": [{"method": "card", "amount": 5000}], "cash_tendered": 5000})
        before = self.shop.shift_expected(self.store.scalar("SELECT id FROM shifts"))
        r = self.shop.return_sale(self.cash1, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "original",
                                                      "lines": [{"sale_line_id": self.line_of(sale), "qty": 1}]})
        self.assertEqual(r["amount"], 10000)
        self.assertEqual(sorted((a["method"], a["amount"]) for a in r["refunds"]), [("card", 5000), ("cash", 5000)])
        self.assertEqual(self.shop.shift_expected(self.store.scalar("SELECT id FROM shifts")), before - 5000)

    def test_r02_collected_credit_is_refunded_in_cash_unpaid_credit_is_cancelled(self):
        p = self.grocery(price=10000)
        self.receive(p, 2, 5000, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=20000)
        credit = [{"method": "credit", "amount": 10000}]
        paid = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c, "payments": credit})
        unpaid = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c, "payments": credit})
        self.shop.customer_payment(self.manager, key(), c, 10000)            # FIFO -> first sale
        shift = self.store.scalar("SELECT id FROM shifts")
        r = self.shop.return_sale(self.manager, key(), {"sale_id": paid["sale"], "reason": "quality", "method": "original",
                                                        "lines": [{"sale_line_id": self.line_of(paid), "qty": 1}]})
        self.assertEqual([(a["method"], a["amount"]) for a in r["refunds"]], [("cash", 10000)])
        self.assertEqual(self.shop.shift_expected(shift), 0)
        self.assertEqual(self.shop.customer_balance(c), 10000)               # the unpaid sale only
        r2 = self.shop.return_sale(self.manager, key(), {"sale_id": unpaid["sale"], "reason": "quality", "method": "original",
                                                         "lines": [{"sale_line_id": self.line_of(unpaid), "qty": 1}]})
        self.assertEqual([(a["method"], a["amount"]) for a in r2["refunds"]], [("credit_cancel", 10000)])
        self.assertEqual(self.shop.customer_balance(c), 0)

    def test_r02_store_credit_only_when_chosen(self):
        p = self.grocery(price=10000)
        self.receive(p, 1, 5000, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=20000)
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c,
                                                   "payments": [{"method": "credit", "amount": 10000}]})
        self.shop.customer_payment(self.manager, key(), c, 10000)
        r = self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "credit_note",
                                                        "lines": [{"sale_line_id": self.line_of(sale), "qty": 1}]})
        self.assertEqual([(a["method"], a["amount"]) for a in r["refunds"]], [("store_credit", 10000)])
        self.assertEqual(self.shop.customer_balance(c), -10000)

    def test_r06_cash_out_never_exceeds_drawer(self):
        self.open_shift(self.manager, 0)
        with self.assertRaises(ShopError) as err:
            self.shop.cash_move(self.manager, key(), "expense", 10000, "no cash")
        self.assertEqual(err.exception.code, "drawer_insufficient")
        self.shop.cash_move(self.manager, key(), "deposit", 5000, "change from owner")
        self.shop.cash_move(self.manager, key(), "expense", 5000, "ice")
        self.assertEqual(self.shop.shift_expected(self.store.scalar("SELECT id FROM shifts")), 0)
        sup = self.shop.add_party(self.owner, "supplier", "S")
        with self.assertRaises(ShopError):
            self.shop.supplier_payment(self.manager, key(), sup, 100)

    def test_r07_confirmed_transfer_is_settled_everywhere_rejected_becomes_debt(self):
        p = self.grocery(price=1000)
        self.receive(p, 2, 100, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=0)
        sale = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}],
                                                   "payments": [{"method": "transfer", "amount": 1000, "reference": "T1"}]})
        pay = self.store.scalar("SELECT id FROM payments WHERE sale_id=?", (sale["sale"],))
        self.shop.confirm_payment(self.manager, key(), pay, "confirmed")
        self.assertEqual(self.shop.sale_detail(self.manager, sale["sale"])["payments"][0]["state"], "settled")
        self.assertEqual([m["state"] for m in self.shop.day_report(self.manager)["by_method"]], ["settled"])
        bad = self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c,
                                                  "payments": [{"method": "wallet", "amount": 1000, "reference": "W1"}]})
        pay2 = self.store.scalar("SELECT id FROM payments WHERE sale_id=?", (bad["sale"],))
        self.shop.confirm_payment(self.manager, key(), pay2, "rejected")
        self.assertEqual(self.shop.sale_detail(self.manager, bad["sale"])["payments"][0]["state"], "rejected")
        self.assertEqual(self.shop.customer_balance(c), 1000)               # follow-up: the customer owes it

    def test_r10_replay_belongs_to_its_creator(self):
        p = self.grocery()
        self.receive(p, 5, 500, expires=None)
        self.open_shift(self.cash1)
        self.open_shift(self.cash2)
        k, payload = key(), {"lines": [{"product_id": p, "qty": 1}]}
        self.shop.sell(self.cash1, k, payload)
        with self.assertRaises(Conflict):
            self.shop.sell(self.cash2, k, payload)
        self.assertTrue(self.shop.sell(self.cash1, k, payload)["replayed"])

    def test_r11_accountant_pays_supplier_from_the_safe(self):
        self.store.tx(lambda: self.shop.auth.create_user("acct", "Accountant", "accountant", PASSWORD))
        acct = self.actor("acct")
        sup = self.shop.add_party(self.owner, "supplier", "S")
        with self.assertRaises(Forbidden):
            self.shop.open_shift(acct, key(), 0)                             # no shop powers given
        with self.assertRaises(ShopError) as err:
            self.shop.supplier_payment(acct, key(), sup, 100, source="safe")
        self.assertEqual(err.exception.code, "safe_insufficient")
        self.open_shift(self.manager, 20000)
        self.shop.cash_move(self.manager, key(), "drop", 15000)              # drawer -> safe
        r = self.shop.supplier_payment(acct, key(), sup, 10000, source="safe")
        self.assertEqual(self.shop.safe_balance(), 5000)
        self.assertEqual(r["balance"], -10000)

    def test_customer_collection_reversal_is_reachable_and_audited(self):
        p = self.grocery(price=10000)
        self.receive(p, 1, 5000, expires=None)
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "C", credit_limit=20000)
        self.shop.sell(self.manager, key(), {"lines": [{"product_id": p, "qty": 1}], "customer_id": c,
                                             "payments": [{"method": "credit", "amount": 10000}]})
        entry = self.shop.customer_payment(self.manager, key(), c, 4000)["entry"]
        self.shop.reverse_customer_payment(self.manager, key(), entry, "bounced")
        self.assertEqual(self.shop.customer_balance(c), 10000)
        self.assertEqual(self.shop.customer_statement(self.manager, c)["lines"][-1]["kind"], "payment_reversal")


class Food(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_r03_recall_survives_returns_and_cannot_be_released_by_status(self):
        p = self.grocery(price=1000)
        self.receive(p, 1, 500, expires=None)
        self.open_shift()
        lot = self.store.scalar("SELECT id FROM lots")
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 1}]})
        line = self.shop.sale_detail(self.owner, sale["sale"])["lines"][0]["id"]
        self.shop.recall(self.owner, key(), lot, "Safety recall")
        r = self.shop.return_sale(self.cash1, key(), {"sale_id": sale["sale"], "reason": "wrong_item", "method": "original",
                                                      "lines": [{"sale_line_id": line, "qty": 1, "restock": True}]})
        self.assertEqual(r["lines"][0]["disposition"], "quarantine")
        self.assertEqual(self.shop.available(p), 0)
        returned = self.store.scalar("SELECT id FROM lots WHERE origin='return'")
        with self.assertRaises(ShopError) as err:
            self.shop.set_lot_status(self.owner, key(), returned, "released", "looks fine")
        self.assertEqual(err.exception.code, "lot_under_recall")
        recall_id = self.store.scalar("SELECT id FROM recalls")
        self.shop.lift_recall(self.owner, key(), recall_id, "supplier cleared batch, letter 12")
        self.shop.set_lot_status(self.owner, key(), returned, "released", "inspected after lift")
        self.assertEqual(self.shop.available(p), 0)                       # still in the quarantine place
        self.shop.transfer(self.owner, key(), returned, self.quarantine, self.shelf, 1)
        self.assertEqual(self.shop.available(p), 1)

    def recipe_setup(self, tolerance=50):
        meat = self.meat("BEEF", 30000)
        sauce = self.shop.add_product(self.owner, {"code": "SAUCE", "name_ar": "تتبيلة", "name_en": "Sauce",
                                                   "kind": "ingredient", "measure": "g"})
        tray = self.shop.add_product(self.owner, {"code": "TRAY", "name_ar": "صينية", "name_en": "Tray",
                                                  "kind": "packaging", "measure": "unit"})
        out = self.meat("TAG", 36000, kind="prepared")
        self.receive(meat, 5000, 150000, self.prep, "2026-11-05")
        self.receive(sauce, 2000, 2000, self.prep, "2026-11-05")
        self.receive(tray, 10, 500, self.prep, None)
        v = self.shop.save_recipe(self.owner, out, [{"product_id": meat, "qty": 800, "role": "meat"},
                                                    {"product_id": sauce, "qty": 200, "role": "food"},
                                                    {"product_id": tray, "qty": 1, "role": "packaging"}],
                                  2, "", tolerance, 1000)["recipe_version"]
        lots = {r["product_id"]: r["id"] for r in self.store.all("SELECT id, product_id FROM lots")}
        return meat, sauce, tray, out, v, lots

    def test_r04_recipe_components_quantities_meat_claim_and_trays(self):
        meat, sauce, tray, out, v, lots = self.recipe_setup()
        bad = {"kind": "recipe", "recipe_version_id": v,
               "inputs": [{"lot_id": lots[sauce], "location_id": self.prep, "qty": 1000}],
               "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
               "packs": [{"net_g": 1000, "tare_g": 10, "meat_g": 900}]}
        with self.assertRaises(ShopError) as err:
            self.shop.produce(self.butcher, key(), bad)
        self.assertEqual(err.exception.code, "recipe_component_missing")
        good = {"kind": "recipe", "recipe_version_id": v,
                "inputs": [{"lot_id": lots[meat], "location_id": self.prep, "qty": 1600},
                           {"lot_id": lots[sauce], "location_id": self.prep, "qty": 400}],
                "outputs": [{"product_id": out, "qty": 2000, "location_id": self.fridge}],
                "packs": [{"net_g": 1000, "tare_g": 10}, {"net_g": 1000, "tare_g": 10}]}
        with self.assertRaises(ShopError) as err:                       # 2 packs need 2 trays
            self.shop.produce(self.butcher, key(), good)
        self.assertEqual(err.exception.code, "recipe_component_missing")
        good["inputs"].append({"lot_id": lots[tray], "location_id": self.prep, "qty": 2})
        good["packs"][0]["meat_g"] = 900                                 # claim > 800 g actually used
        with self.assertRaises(ShopError) as err:
            self.shop.produce(self.butcher, key(), good)
        self.assertEqual(err.exception.code, "meat_claim_exceeds_input")
        del good["packs"][0]["meat_g"]
        r = self.shop.produce(self.butcher, key(), good)
        self.assertEqual([p["meat_g"] for p in r["outputs"][0]["packs"]], [800, 800])   # derived from real meat
        skew = dict(good, inputs=[{"lot_id": lots[meat], "location_id": self.prep, "qty": 1000},
                                  {"lot_id": lots[sauce], "location_id": self.prep, "qty": 1000},
                                  {"lot_id": lots[tray], "location_id": self.prep, "qty": 2}])
        skew.pop("packs")
        with self.assertRaises(Forbidden):                               # 1000 vs 1600 g meat: variance needs approval
            self.shop.produce(self.butcher, key(), skew)

    def test_r04_approved_alternative_counts_as_its_component(self):
        meat, sauce, tray, out, v, lots = self.recipe_setup()
        lamb = self.meat("LAMB", 30000)
        self.receive(lamb, 1000, 30000, self.prep, "2026-11-05")
        v2 = self.shop.save_recipe(self.owner, out, [{"product_id": meat, "qty": 800, "role": "meat", "alternatives": [lamb]},
                                                     {"product_id": sauce, "qty": 200, "role": "food"},
                                                     {"product_id": tray, "qty": 1, "role": "packaging"}],
                                   2, "", 50, 1000)["recipe_version"]
        lamb_lot = self.store.scalar("SELECT id FROM lots WHERE product_id=?", (lamb,))
        r = self.shop.produce(self.butcher, key(), {"kind": "recipe", "recipe_version_id": v2,
                                                    "inputs": [{"lot_id": lamb_lot, "location_id": self.prep, "qty": 800},
                                                               {"lot_id": lots[sauce], "location_id": self.prep, "qty": 200},
                                                               {"lot_id": lots[tray], "location_id": self.prep, "qty": 1}],
                                                    "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                    "packs": [{"net_g": 1000, "tare_g": 10}]})
        self.assertEqual(r["outputs"][0]["packs"][0]["meat_g"], 800)

    def test_r05_scanned_lot_and_expiry_bind_the_sale(self):
        p = self.meat()
        gtin = ean("0622123456789")
        self.shop.add_barcode(self.owner, gtin, p)
        self.receive(p, 1000, 10000, self.fridge, expires="2026-11-05", supplier_lot="FRESH")
        self.receive(p, 1000, 10000, self.fridge, expires="2026-11-02", supplier_lot="OLDER")
        self.open_shift()
        expired = "(01)" + gtin + "(17)261029(10)RECALLED"
        with self.assertRaises(ShopError) as err:
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 500, "scanned": expired}]})
        self.assertEqual(err.exception.code, "scanned_label_expired")
        unknown = "(01)" + gtin + "(17)261110(10)OTHER"
        with self.assertRaises(ShopError) as err:
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 500, "scanned": unknown}]})
        self.assertEqual(err.exception.code, "scanned_lot_not_in_stock")
        fresh = "(01)" + gtin + "(17)261105(10)FRESH"                    # not the FEFO lot, but the one in hand
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 500, "scanned": fresh}]})
        self.assertEqual(self.store.scalar("""SELECT l.supplier_lot FROM sale_allocations a JOIN lots l ON l.id=a.lot_id"""), "FRESH")
        self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 100}]})   # no lot on label -> FEFO
        self.assertEqual(self.store.scalar("""SELECT l.supplier_lot FROM sale_allocations a JOIN lots l ON l.id=a.lot_id
            ORDER BY a.rowid DESC LIMIT 1"""), "OLDER")


class Goods(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def packed_lot(self):
        p = self.meat()
        r = self.receive(p, 1000, 10000, self.fridge)
        out = self.meat("OUT")
        result = self.shop.produce(self.owner, key(), {"kind": "cutting",
                                                       "inputs": [{"lot_id": r["lines"][0]["lot"], "location_id": self.fridge, "qty": 1000}],
                                                       "outputs": [{"product_id": out, "qty": 1000, "location_id": self.fridge}],
                                                       "packs": [{"net_g": 500}, {"net_g": 500}]})
        return out, result["outputs"][0]

    def test_r12_pack_reserved_stock_needs_the_pack(self):
        out, made = self.packed_lot()
        with self.assertRaises(ShopError) as err:
            self.shop.waste(self.owner, key(), made["lot"], self.fridge, 500, "damaged")
        self.assertEqual(err.exception.code, "stock_reserved_by_packs")
        self.shop.waste_pack(self.owner, key(), made["packs"][0]["code"], "damaged")
        self.assertEqual(self.store.scalar("SELECT qty FROM stock_balances WHERE lot_id=?", (made["lot"],)), 500)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM packs WHERE lot_id=? AND status='available' AND removed_op IS NULL",
                                           (made["lot"],)), 1)
        self.assertTrue(self.shop.reconcile()["ok"])
        with self.assertRaises(ShopError):                                # a wasted pack is not for sale
            self.open_shift()
            self.shop.sell(self.cash1, key(), {"lines": [{"pack_code": made["packs"][0]["code"]}]})

    def test_r12_reconcile_detects_packs_without_stock(self):
        out, made = self.packed_lot()
        op = self.store.scalar("SELECT MAX(id) FROM ops")
        self.store.db.execute("INSERT INTO stock_entries(op_id,kind,lot_id,location_id,qty,cost,at) VALUES(?,?,?,?,?,?,?)",
                              (op, "tamper", made["lot"], self.fridge, -500, -5000, "t"))
        report = self.shop.reconcile()
        self.assertFalse(report["ok"])
        self.assertTrue(report["packs_over_stock"])

    def test_r14_price_change_after_scan_needs_acceptance(self):
        p = self.grocery(price=10000)
        self.receive(p, 2, 5000, expires=None)
        self.open_shift()
        quote = self.shop.quote(self.cash1, {"lines": [{"product_id": p, "qty": 1}]})
        self.assertEqual(quote["total"], 10000)
        self.shop.set_price(self.owner, key(), p, 15000)
        with self.assertRaises(Conflict) as err:
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 1}], "expected_total": quote["total"],
                                               "cash_tendered": 20000})
        self.assertEqual(err.exception.code, "price_changed")
        self.assertEqual(err.exception.detail["total"], 15000)
        sale = self.shop.sell(self.cash1, key(), {"lines": [{"product_id": p, "qty": 1}], "expected_total": 15000,
                                                  "cash_tendered": 20000})
        self.assertEqual(sale["change"], 5000)

    def test_r15_packaging_must_belong_to_the_product(self):
        a = self.grocery("A", barcodes=[])
        b = self.grocery("B", barcodes=[], carton=ean("1622123456789"))
        pk = self.store.scalar("SELECT id FROM packagings WHERE product_id=?", (b,))
        with self.assertRaises(ShopError) as err:
            self.shop.add_barcode(self.owner, ean("622123456789"), a, pk)
        self.assertEqual(err.exception.code, "packaging_other_product")
        with self.assertRaises(sqlite3.IntegrityError):                   # database guard too (migration 2)
            self.store.db.execute("INSERT INTO barcodes VALUES(?,?,?,?)", (ean("622123456796"), a, pk, "t"))


class Scanner(unittest.TestCase):
    def test_r09_events_go_only_to_the_bound_workstation_and_active_window(self):
        hub = ScannerHub()
        hub.bindings = {"dev-a": "till-1", "dev-b": "till-2"}
        hub.push("dev-a", "111")
        hub.push("dev-b", "222")
        w1 = hub.claim("till-1", "window-1")
        events, _ = hub.take("till-1", "window-1", 0, 0)[:2]
        self.assertEqual([e["text"] for e in events], ["111"])
        hub.claim("till-1", "window-2")                                    # second window takes over
        with self.assertRaises(ShopError) as err:
            hub.take("till-1", "window-1", 0, 0)
        self.assertEqual(err.exception.code, "scanner_taken_by_other_window")
        self.assertEqual([e["text"] for e in hub.take("till-2", hub.claim("till-2", "w"), 0, 0)[0]], ["222"])
        self.assertTrue(w1)

    def test_r13_restart_epoch_and_overflow_are_reported(self):
        old = ScannerHub()
        old.push("d", "a")
        new = ScannerHub()
        new.push("d", "after-restart")
        new.bindings = {"d": "t"}
        new.claim("t", "w")
        events, last, info = new.take("t", "w", 5, 0, epoch=old.epoch)
        self.assertEqual([e["text"] for e in events], ["after-restart"])
        self.assertTrue(info["restarted"])
        small = ScannerHub(size=3)
        small.bindings = {"d": "t"}
        small.claim("t", "w")
        for i in range(6):
            small.push("d", str(i))
        events, last, info = small.take("t", "w", 1, 0, epoch=small.epoch)
        self.assertEqual(info["missed"], 2)                               # ids 2 and 3 were dropped
        self.assertEqual([e["text"] for e in events], ["3", "4", "5"])


class WindowsSerial(unittest.TestCase):
    def test_s01_dcb_flags_and_structure(self):
        from server.scanner import dcb_flags, win_structs
        self.assertEqual(dcb_flags("none") & 0x2, 0)
        self.assertEqual(dcb_flags("even") & 0x2, 0x2)                    # fParity on when parity is used
        self.assertEqual(dcb_flags("none") & 0x1, 0x1)                    # fBinary always
        dcb, timeouts = win_structs()
        import ctypes
        self.assertEqual(ctypes.sizeof(dcb), 28)
        self.assertEqual(ctypes.sizeof(timeouts), 20)


if __name__ == "__main__":
    unittest.main()
