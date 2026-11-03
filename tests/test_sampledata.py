"""Sample data: a month of trading made by the real services, and the one-step removal.

The load is built through the shop's own commands with the clock set to the past, so these tests also prove that
the rules hold on a believable month: derived balances equal the entries, every payment equals its sale, no sale is
dated in the future, and nothing sold was past its date."""

import datetime as dt
import pathlib
import tempfile
import unittest

import helpers  # noqa: F401  (fast password hashing for tests)
from server import core, sampledata
from server.app import App
from server.core import ShopError


class SampleDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        root = pathlib.Path(cls.tmp.name)
        cls.app = App(root / "data", "shop", backup_folder=root / "backups")
        cls.addClassCleanup(cls.app.store.close)
        cls.app.setup(None, {}, {"username": "owner", "name": "Owner", "password": "Owner-pass-9271", "shop_name": "Shop"},
                      client="127.0.0.1")
        row = cls.app.store.one("SELECT * FROM users WHERE username='owner'")
        cls.owner = cls.app.auth.public(row)
        cls.owner["workstation"] = "till-1"
        cls.store, cls.shop = cls.app.store, cls.app.shop
        cls.root = root
        cls.before = cls.snapshot(cls.store)
        cls.summary = sampledata.load(cls.shop, cls.owner)

    @staticmethod
    def snapshot(store):
        return {"triggers": sorted(r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")),
                "settings": store.all("SELECT key,value FROM settings WHERE key NOT LIKE '\\_%' ESCAPE '\\' ORDER BY key"),
                "users": [r["username"] for r in store.all("SELECT username FROM users ORDER BY id")],
                "locations": [r["code"] for r in store.all("SELECT code FROM locations ORDER BY id")],
                "audit": store.scalar("SELECT COUNT(*) FROM audit")}

    def test_a_month_of_believable_trading(self):
        s = self.summary
        self.assertGreater(s["sales"], 1500)
        self.assertGreater(s["receipts"], 50)
        self.assertGreater(s["cuts"], 5)
        self.assertGreater(s["counts"], 0)
        gross = self.store.scalar("SELECT SUM(total) FROM sales")
        cost = self.store.scalar("SELECT SUM(cost) FROM sale_lines")
        margin = (gross - cost) / gross
        self.assertTrue(0.12 < margin < 0.35, margin)           # a supermarket with a meat counter, not a gift shop
        self.assertGreater(self.store.scalar("SELECT COUNT(*) FROM day_closes"), 25)
        self.assertGreater(self.store.scalar("SELECT COUNT(*) FROM packs"), 0)

    def test_the_rules_hold_on_the_month(self):
        self.assertTrue(self.shop.reconcile()["ok"])
        self.assertEqual(self.store.db.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM stock_balances WHERE qty<0"), 0)
        future = self.store.scalar("SELECT COUNT(*) FROM ops WHERE at > ?", (core.now_iso(),))
        self.assertEqual(future, 0)
        expired = self.store.scalar("""SELECT COUNT(*) FROM sale_allocations a JOIN sale_lines sl ON sl.id=a.sale_line_id
            JOIN sales s ON s.id=sl.sale_id JOIN lots l ON l.id=a.lot_id WHERE l.expires IS NOT NULL AND l.expires < substr(s.at,1,10)""")
        self.assertEqual(expired, 0)
        for c in self.store.all("SELECT id FROM customers"):
            self.assertTrue(self.shop.account_check(c["id"])["ok"])

    def test_day_snapshots_are_intact(self):
        for row in self.store.all("SELECT day FROM day_closes"):
            self.assertTrue(self.shop.day_status(self.owner, row["day"])["snapshot_ok"], row["day"])

    def test_supplier_aging_agrees_with_the_supplier_balance(self):
        """Regression: rebates and invoice corrections used to be left out of the ageing report."""
        self.assertGreater(self.store.scalar("SELECT COUNT(*) FROM supplier_entries WHERE kind IN ('rebate','cost_correction')"), 0)
        for row in self.shop.aging(self.owner)["suppliers"]:
            self.assertEqual(row["d0_30"] + row["d31_60"] + row["d61_plus"], self.shop.supplier_balance(row["id"]), row["name"])

    def test_sample_staff_cannot_sign_in(self):
        rows = self.store.all("SELECT username, active FROM users WHERE username LIKE 'sample\\_%' ESCAPE '\\'")
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual({r["active"] for r in rows}, {0})
        with self.assertRaises(ShopError):
            self.shop.auth.verify("sample_cashier1", "anything-at-all-123")

    def test_status_and_refusals(self):
        st = sampledata.status(self.store)
        self.assertTrue(st["loaded"] and st["complete"] and not st["can_load"])
        with self.assertRaises(ShopError) as ctx:
            sampledata.load(self.shop, self.owner)
        self.assertEqual(ctx.exception.code, "sample_already_loaded")

    def test_removal_needs_the_owners_own_password(self):
        with self.assertRaises(ShopError) as ctx:
            self.app.sample_clear(self.owner, {}, {"password": "not-the-password"})
        self.assertEqual(ctx.exception.code, "bad_login")
        self.assertTrue(sampledata.status(self.store)["loaded"])        # nothing was removed

    def test_zz_removal_puts_the_shop_back_and_it_can_be_filled_again(self):
        """Runs last (name order): removes everything, checks the shop is as before, loads once more."""
        out = self.app.sample_clear(self.owner, {}, {"password": "Owner-pass-9271"})
        self.assertTrue((pathlib.Path(out["copy"]) / "shop.db").exists())        # a full copy came first
        after = self.snapshot(self.store)
        self.assertEqual(after["triggers"], self.before["triggers"])             # protection of posted rows is back
        self.assertEqual(after["settings"], self.before["settings"])             # policies the sample set are gone again
        self.assertEqual(after["users"], self.before["users"])
        self.assertEqual(after["locations"], self.before["locations"])
        self.assertEqual(after["audit"], self.before["audit"] + 1)               # only the removal itself is recorded
        for table in ("sales", "products", "stock_entries", "lots", "ops", "receipts", "customers", "suppliers", "day_closes", "packs"):
            self.assertEqual(self.store.scalar('SELECT COUNT(*) FROM "%s"' % table), 0, table)
        self.assertTrue(self.shop.reconcile()["ok"])
        self.assertTrue(sampledata.status(self.store)["can_load"])
        sampledata.load(self.shop, self.owner)                                    # and again
        self.assertEqual(self.store.scalar("SELECT MIN(no) FROM sales"), "S-000001")
        self.assertTrue(self.shop.reconcile()["ok"])
        with self.assertRaises(Exception):                                         # posted rows are immutable again
            self.store.db.execute("UPDATE sales SET total=1")


class SampleDataGuards(unittest.TestCase):
    def make(self, mode="shop"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(pathlib.Path(tmp.name) / "data", mode, backup_folder=pathlib.Path(tmp.name) / "b")
        self.addCleanup(app.store.close)
        if mode == "shop":
            app.setup(None, {}, {"username": "owner", "name": "O", "password": "Owner-pass-9271", "shop_name": "S"}, client="127.0.0.1")
            owner = app.auth.public(app.store.one("SELECT * FROM users WHERE username='owner'"))
        else:
            owner = app.auth.public(app.store.one("SELECT * FROM users WHERE username='owner'"))
        owner["workstation"] = "till-1"
        return app, owner

    def test_not_into_a_shop_that_has_data(self):
        app, owner = self.make()
        app.shop.add_product(owner, {"code": "1", "name_ar": "س", "name_en": "x", "kind": "grocery", "measure": "unit", "price": 100})
        with self.assertRaises(ShopError) as ctx:
            sampledata.load(app.shop, owner)
        self.assertEqual(ctx.exception.code, "sample_needs_empty_shop")
        self.assertFalse(sampledata.status(app.store)["can_load"])

    def test_not_in_the_training_copy(self):
        app, owner = self.make("training")
        with self.assertRaises(ShopError) as ctx:
            sampledata.load(app.shop, owner)
        self.assertEqual(ctx.exception.code, "sample_shop_only")

    def test_only_the_owner(self):
        app, owner = self.make()
        manager = dict(owner, role="manager")
        for call in (lambda: app.sample_status(manager), lambda: app.sample_load(manager, {}, {}), lambda: app.sample_clear(manager, {}, {})):
            with self.assertRaises(ShopError):
                call()

    def test_clock_is_put_back(self):
        app, owner = self.make()
        sampledata.load(app.shop, owner)
        self.assertLess(abs((core.now() - dt.datetime.now(core.now().tzinfo)).total_seconds()), 5)


if __name__ == "__main__":
    unittest.main()
