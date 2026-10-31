"""ISSUE-001: a debt collected by InstaPay must not become drawer cash.

4000 pounds is 400000 piastres. Opening float 500 pounds is 50000 piastres.
Booking the transfer as cash made the shift expect 4500 and show a shortage
of 4000 when the cashier counted the real 500.
"""

import unittest

from helpers import ShopFixture, key
from server.core import ShopError


POUNDS = 100          # piastres in one pound
OWED = 4000 * POUNDS  # the customer debt in the field report
FLOAT = 500 * POUNDS  # the only cash in the drawer


class DebtCollectionMethod(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def _owed(self, name="طارق", amount=OWED):
        product = self.grocery(price=amount)
        self.receive(product, 1, 1000, expires=None)
        customer = self.shop.add_party(self.owner, "customer", name, credit_limit=amount)
        self.open_shift(self.manager, 0)
        sale = self.shop.sell(self.manager, key(), {
            "lines": [{"product_id": product, "qty": 1}], "customer_id": customer,
            "payments": [{"method": "credit", "amount": amount}]})
        self.open_shift(self.cash1, FLOAT)
        shift = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.cash1["id"],))
        return customer, sale, shift

    def test_transfer_of_4000_does_not_short_the_cashier(self):
        """Collect 4000 by transfer, count the real 500: difference is 0, not -4000."""
        customer, sale, shift = self._owed()
        done = self.shop.customer_payment(self.cash1, key(), customer, OWED, "transfer", "INSTA-4000")
        self.assertEqual(done["state"], "pending")
        self.assertEqual(self.shop.customer_balance(customer), 0)
        self.assertIsNone(self.store.one("SELECT 1 FROM cash_entries WHERE kind='debt_collect'"))
        row = self.store.one("SELECT method, amount, state, reference, shift_id FROM payments WHERE sale_id IS NULL")
        self.assertEqual((row["method"], row["amount"], row["state"], row["reference"], row["shift_id"]),
                         ("transfer", OWED, "pending", "INSTA-4000", shift))
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)
        pending = self.shop.pending_payments(self.manager)
        self.assertEqual([(p["method"], p["amount"], p["no"], p["customer_name"]) for p in pending],
                         [("transfer", OWED, None, "طارق")])
        shown = self.shop.shift_summary(self.cash1)
        self.assertEqual(shown["noncash"], [{"method": "transfer", "state": "pending", "amount": OWED}])
        self.assertNotIn("expected", shown)
        line = self.shop.sale_detail(self.manager, sale["sale"])["lines"][0]["id"]
        with self.assertRaises(ShopError) as blocked:
            self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "original",
                                                        "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual(blocked.exception.code, "sale_has_unconfirmed_payment")
        with self.assertRaises(ShopError) as waiting:
            self.shop.reverse_customer_payment(self.manager, key(), done["entry"], "لم يصل")
        self.assertEqual(waiting.exception.code, "collection_still_pending")
        closed = self.shop.close_shift(self.cash1, key(), FLOAT)
        self.assertEqual((closed["expected"], closed["counted"], closed["difference"]), (FLOAT, FLOAT, 0))

    def test_wallet_and_card_also_stay_out_of_the_drawer(self):
        product = self.grocery(price=10000)
        self.receive(product, 2, 100, expires=None)
        self.open_shift(self.manager, 0)
        self.open_shift(self.cash1, FLOAT)
        shift = self.store.scalar("SELECT id FROM shifts WHERE user_id=?", (self.cash1["id"],))
        for name, method in (("نورا", "wallet"), ("سامي", "card")):
            customer = self.shop.add_party(self.owner, "customer", name, credit_limit=10000)
            self.shop.sell(self.manager, key(), {"lines": [{"product_id": product, "qty": 1}], "customer_id": customer,
                                                 "payments": [{"method": "credit", "amount": 10000}]})
            done = self.shop.customer_payment(self.cash1, key(), customer, 10000, method, "REF-" + method)
            self.assertEqual(done["state"], "pending")
            self.assertEqual(self.shop.customer_balance(customer), 0)
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)
        self.assertIsNone(self.store.one("SELECT 1 FROM cash_entries WHERE kind='debt_collect'"))
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM payments WHERE sale_id IS NULL AND state='pending'"), 2)

    def test_cash_collection_still_enters_the_drawer(self):
        """The same 4000 in cash does raise expected cash. Counting only 500 is a real shortage."""
        customer, _sale, shift = self._owed()
        self.shop.customer_payment(self.cash1, key(), customer, OWED, "cash")
        self.assertEqual(self.store.scalar("SELECT SUM(amount) FROM cash_entries WHERE kind='debt_collect'"), OWED)
        closed = self.shop.close_shift(self.cash1, key(), FLOAT)
        self.assertEqual((closed["expected"], closed["difference"]), (FLOAT + OWED, -OWED))

    def test_rejected_transfer_restores_the_debt_without_touching_the_drawer(self):
        customer, _sale, shift = self._owed()
        done = self.shop.customer_payment(self.cash1, key(), customer, OWED, "transfer")
        result = self.shop.confirm_payment(self.manager, key(), done["payment"], "rejected")
        self.assertEqual(result["follow_up"], "customer_debt")
        self.assertEqual(self.shop.customer_balance(customer), OWED)
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)
        self.assertIsNone(self.store.one("SELECT 1 FROM cash_entries WHERE kind='debt_collect'"))
        closed = self.shop.close_shift(self.cash1, key(), FLOAT)
        self.assertEqual(closed["difference"], 0)
        report = self.shop.day_report(self.manager)
        self.assertEqual([(r["no"], r["customer_name"], r["method"], r["amount"]) for r in report["rejected_payments"]],
                         [(None, "طارق", "transfer", OWED)])

    def test_confirmed_transfer_refund_does_not_leave_the_drawer(self):
        customer, sale, shift = self._owed()
        done = self.shop.customer_payment(self.cash1, key(), customer, OWED, "transfer")
        self.shop.confirm_payment(self.manager, key(), done["payment"], "confirmed")
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)
        self.assertEqual(self.shop.customer_balance(customer), 0)
        line = self.shop.sale_detail(self.manager, sale["sale"])["lines"][0]["id"]
        refund = self.shop.return_sale(self.manager, key(), {"sale_id": sale["sale"], "reason": "quality", "method": "original",
                                                            "lines": [{"sale_line_id": line, "qty": 1}]})
        self.assertEqual([(a["method"], a["amount"]) for a in refund["refunds"]], [("transfer", OWED)])
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)
        with self.assertRaises(ShopError) as err:
            self.shop.reverse_customer_payment(self.manager, key(), done["entry"], "بعد الرد")
        self.assertEqual(err.exception.code, "collection_already_refunded")

    def test_confirmed_transfer_can_be_reversed_without_a_drawer_movement(self):
        customer, _sale, shift = self._owed()
        done = self.shop.customer_payment(self.cash1, key(), customer, OWED, "transfer")
        self.shop.confirm_payment(self.manager, key(), done["payment"], "confirmed")
        self.shop.reverse_customer_payment(self.manager, key(), done["entry"], "وصل بالخطأ")
        self.assertEqual(self.shop.customer_balance(customer), OWED)
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)

    def test_unknown_method_is_refused(self):
        customer, _sale, shift = self._owed()
        before = self.shop.customer_balance(customer)
        with self.assertRaises(ShopError) as err:
            self.shop.customer_payment(self.cash1, key(), customer, OWED, "instapay")
        self.assertEqual(err.exception.code, "bad_payment")
        self.assertEqual(self.shop.customer_balance(customer), before)
        self.assertEqual(self.shop.shift_expected(shift), FLOAT)


if __name__ == "__main__":
    unittest.main()
