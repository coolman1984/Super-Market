"""Acceptance tests for docs/REVIEW_REPORT.md (review of 31 Oct 2026). One class
per issue family; each test states the CORRECT behaviour."""

import pathlib
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


class BackupSafety(ShopFixture, unittest.TestCase):
    """H5, M6, M7, L4 - a copy outside the shop PC, ordered by sequence, pruned, never silent."""

    def setUp(self):
        import pathlib
        import tempfile
        self.make_shop()
        self.local = self.folder / "backups-shop"
        usb = tempfile.TemporaryDirectory()
        self.addCleanup(usb.cleanup)
        self.usb = pathlib.Path(usb.name)
        self.p = self.grocery(price=1000)
        self.receive(self.p, 5, 500, expires=None)      # real data: the banner only matters then

    def make(self, label="daily"):
        from server import backup
        return backup.create_backup(self.store, self.local, label)

    def test_h5_second_place_gets_a_verified_copy(self):
        from server import backup
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        first = self.make()
        out = backup.sync_external(self.store, self.local, self.folder)
        self.assertEqual(out["copied"], first["folder"])
        copied = self.usb / backup.EXTERNAL_DIR / pathlib.Path(first["folder"]).name
        self.assertEqual(backup.verify_backup(copied)["sha256"], first["sha256"])
        st = backup.status(self.store, self.local)
        self.assertTrue(st["configured"] and st["external_days"] == 0 and not st["external_error"])

    def test_h5_missing_drive_is_reported_never_created_and_never_stops_selling(self):
        from server import backup
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        self.make()
        import shutil
        shutil.rmtree(self.usb)
        out = backup.sync_external(self.store, self.local, self.folder)
        self.assertEqual(out["error"], "backup2_unusable")
        self.assertFalse(self.usb.exists())                       # the unplugged drive's folder is not recreated on the shop disk
        st = backup.status(self.store, self.local)
        self.assertEqual(st["external_error"], "backup2_unusable")

    def test_h5_banner_data_no_copy_and_days_without_copy(self):
        from server import backup
        self.make()
        st = backup.status(self.store, self.local)
        self.assertTrue(st["has_data"])
        self.assertFalse(st["configured"])
        self.assertIsNone(st["external_days"])                    # the banner says "no external copy"
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        backup.sync_external(self.store, self.local, self.folder)
        core.set_clock(core.now() + __import__("datetime").timedelta(days=4))
        self.assertEqual(backup.status(self.store, self.local)["external_days"], 4)

    def test_h5_bad_places_are_refused_and_only_the_owner_sets_it(self):
        for bad in ("relative/path", str(self.folder / "does-not-exist"), str(self.folder / "data")):
            with self.assertRaises(ShopError) as c:
                self.shop.update_settings(self.owner, {"backup_folder_2": bad})
            self.assertEqual(c.exception.code, "backup2_unusable")
        with self.assertRaises(Forbidden):
            self.shop.update_settings(self.manager, {"backup_folder_2": str(self.usb)})
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        self.shop.update_settings(self.owner, {"backup_folder_2": ""})            # switched off

    def test_h5_damaged_copy_on_the_stick_is_replaced_not_trusted(self):
        from server import backup
        first = self.make()
        root = self.usb
        target = pathlib.Path(backup.copy_external(first["folder"], root, self.folder))
        (target / "shop.db").write_bytes(b"broken")
        again = pathlib.Path(backup.copy_external(first["folder"], root, self.folder))
        self.assertEqual(backup.verify_backup(again)["sha256"], first["sha256"])

    def test_m6_newest_is_decided_by_sequence_not_by_the_clock(self):
        from server import backup
        a = self.make()
        core.set_clock(core.now() - __import__("datetime").timedelta(days=400))       # the PC clock jumps far back
        b = self.make()
        self.assertEqual([x["folder"] for x in backup.list_backups(self.local)], [b["folder"], a["folder"]])
        self.assertGreater(b["seq"], a["seq"])

    def test_m7_before_copies_are_pruned_and_manual_copies_never(self):
        from server import backup
        for n in range(8):
            self.make("before-1.%d" % n)
        manual = [self.make("manual") for _ in range(3)]
        removed = backup.prune(self.local, {"daily": 30, "before": 5})
        self.assertEqual(len(removed), 3)
        labels = [b["label"] for b in backup.list_backups(self.local)]
        self.assertEqual(sum(1 for x in labels if x.startswith("before-")), 5)
        self.assertEqual(labels.count("manual"), 3)
        self.assertNotIn("before-1.0", labels)                      # the oldest went, the newest stayed
        self.assertIn("before-1.7", labels)

    def test_m7_low_disk_is_flagged(self):
        from unittest import mock
        from server import backup
        with mock.patch.object(backup, "disk_free", return_value=100 * 1024 * 1024):
            st = backup.status(self.store, self.local)
        self.assertTrue(st["low_disk"])
        self.assertEqual(st["free_mb"], 100)

    def test_l4_two_pre_upgrade_copies_in_one_second_do_not_collide(self):
        self.store._pre_upgrade_copy(3)
        self.store._pre_upgrade_copy(3)
        self.assertEqual(len(list((self.store.folder / "pre-upgrade").glob("schema-3-*.db"))), 2)
