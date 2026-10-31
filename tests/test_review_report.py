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


class LoginThrottle(ShopFixture, unittest.TestCase):
    """H2 + L6 - strangers cannot lock the owner out; PBKDF2 rounds are upgraded."""

    def setUp(self):
        self.make_shop()
        from server import auth
        self.auth = self.shop.auth
        self.clock = [1000.0]
        self._old = auth._mono
        auth._mono = lambda: self.clock[0]
        self.addCleanup(setattr, auth, "_mono", self._old)

    def bad(self, ip, n=1, name="owner"):
        for _ in range(n):
            try:
                self.auth.verify(name, "wrong-wrong", client=ip)
            except ShopError as e:
                last = e
        return last

    def test_hundred_bad_logins_from_b_never_block_a(self):
        last = self.bad("10.0.0.9", 100)
        self.assertIn(last.code, ("account_locked", "too_many_attempts"))
        self.assertEqual(last.status, 429)
        user = self.auth.verify("owner", PASSWORD, client="10.0.0.5")
        self.assertEqual(user["username"], "owner")

    def test_five_bad_from_same_address_back_off_and_double(self):
        self.bad("10.0.0.9", 5)
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertEqual(c.exception.code, "account_locked")
        self.assertEqual(c.exception.detail["retry_after"], 61)
        self.clock[0] += 61
        self.bad("10.0.0.9", 5)                      # second lock is longer
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertGreater(c.exception.detail["retry_after"], 100)

    def test_one_address_guessing_many_names_is_refused_as_an_address(self):
        for i in range(25):
            self.bad("10.0.0.9", 1, name="nobody%d" % i)
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertEqual(c.exception.code, "too_many_attempts")

    def test_right_password_on_the_shop_computer_clears_the_waits_for_that_name(self):
        self.bad("10.0.0.9", 5)
        self.auth.verify("owner", PASSWORD, client="127.0.0.1")
        self.assertEqual(self.auth.verify("owner", PASSWORD, client="10.0.0.9")["username"], "owner")

    def test_shop_computer_wait_is_short_and_flat(self):
        self.bad("127.0.0.1", 5)
        with self.assertRaises(ShopError):
            self.auth.verify("owner", PASSWORD, client="127.0.0.1")
        self.clock[0] += 31
        self.assertEqual(self.auth.verify("owner", PASSWORD, client="127.0.0.1")["username"], "owner")

    def test_old_hash_is_upgraded_after_login_and_new_hashes_use_the_current_rounds(self):
        from server import auth
        user = self.store.one("SELECT * FROM users WHERE username='cash1'")
        auth.PBKDF2_ITER, saved = 5000, auth.PBKDF2_ITER
        try:
            self.assertLess(user["pw_iter"], 5000)
            self.auth.verify("cash1", PASSWORD)
            after = self.store.one("SELECT * FROM users WHERE username='cash1'")
            self.assertEqual(after["pw_iter"], 5000)
            self.assertEqual(self.auth.verify("cash1", PASSWORD)["id"], user["id"])   # new hash verifies
        finally:
            auth.PBKDF2_ITER = saved
        self.assertGreaterEqual(auth.__dict__["PBKDF2_ITER"], 2000)

    def test_production_rounds_are_at_least_owasp(self):
        import importlib, server.auth as a
        src = open(a.__file__, encoding="utf-8").read()
        self.assertIn("PBKDF2_ITER = 600_000", src)
