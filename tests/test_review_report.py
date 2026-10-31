"""Acceptance tests for docs/REVIEW_REPORT.md (review of 31 Oct 2026). One class
per issue family; each test states the CORRECT behaviour."""

import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import core
from server.core import Forbidden, ShopError


class RefundGuard(ShopFixture, unittest.TestCase):
    """H1 - a cashier cannot pay cash out against any sale alone."""

    def setUp(self):
        self.make_shop()
        self.p = self.grocery(price=10000)
        self.receive(self.p, 10, 5000, expires=None)
        self.open_shift(self.cash1, 100000)
        self.open_shift(self.cash2, 100000)
        self.open_shift(self.manager, 100000)

    def sale(self, actor, qty=1, price=None):
        return self.shop.sell(actor, key(), {"lines": [{"product_id": self.p, "qty": qty}],
                                             "cash_tendered": 10000 * qty})

    def line(self, sale):
        return self.shop.sale_detail(self.owner, sale["sale"])["lines"][0]["id"]

    def refund(self, actor, sale, reason="quality", qty=1, approver=None):
        return self.shop.return_sale(actor, key(), {"sale_id": sale["sale"], "reason": reason, "method": "original",
                                                    "lines": [{"sale_line_id": self.line(sale), "qty": qty}]}, approver)

    def test_own_small_recent_quality_refund_needs_no_approval(self):
        s = self.sale(self.cash1)
        self.assertEqual(self.refund(self.cash1, s)["amount"], 10000)

    def test_another_cashiers_sale_needs_a_manager(self):
        s = self.sale(self.cash2)
        with self.assertRaises(Forbidden) as c:
            self.refund(self.cash1, s)
        self.assertEqual(c.exception.code, "approval_required")
        r = self.refund(self.cash1, s, approver=self.approval())
        self.assertEqual(r["amount"], 10000)
        row = self.store.one("SELECT approved_by FROM returns WHERE id=?", (r["return"],))
        self.assertEqual(row["approved_by"], self.manager["id"])
        audit = self.store.one("SELECT detail FROM audit WHERE action='return.approval'")
        self.assertIn("other_cashier", audit["detail"])
        self.assertTrue(self.shop.reconcile()["ok"])

    def test_cash_for_change_of_mind_needs_a_manager(self):
        s = self.sale(self.cash1)
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, reason="change_of_mind")
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, reason="other")
        self.refund(self.cash1, s, reason="change_of_mind", approver=self.approval())

    def test_amount_over_the_limit_needs_a_manager(self):
        s = self.sale(self.cash1, qty=3)
        self.shop.update_settings(self.owner, {"refund_approval_over": 20000})
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, qty=3)
        self.refund(self.cash1, s, qty=2)                  # exactly at the limit is allowed

    def test_old_sale_needs_a_manager(self):
        s = self.sale(self.cash1)
        shift = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.cash1["id"],))
        self.shop.close_shift(self.cash1, key(), self.shop.shift_expected(shift))
        core.set_clock(core.now() + __import__('datetime').timedelta(days=9))
        self.open_shift(self.cash1, 100000)
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s)

    def test_wrong_approver_password_is_refused(self):
        s = self.sale(self.cash2)
        with self.assertRaises(ShopError):
            self.shop.verify_approval({"username": "manager", "password": "nope-nope"}, self.cash1)

    def test_manager_and_owner_are_never_held_up(self):
        s = self.sale(self.cash2)
        self.assertEqual(self.refund(self.manager, s, reason="other")["amount"], 10000)

    def test_settings_are_validated(self):
        with self.assertRaises(ShopError):
            self.shop.update_settings(self.owner, {"refund_approval_over": -1})
        with self.assertRaises(ShopError):
            self.shop.update_settings(self.owner, {"refund_days_without_approval": "7"})
