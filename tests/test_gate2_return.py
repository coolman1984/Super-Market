"""Gate 2 return: a price of zero or a fantasy price is refused, a sale line of
250 kg is refused, and an existing shop is copied with the SQLite backup API
before a migration or a newer program opens it."""

import json
import sqlite3
import unittest

from helpers import ShopFixture, key
from server.core import MAX_PRICE, ShopError
from server.db import Store
from server.version import VERSION


class PriceAndQuantityGuards(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_zero_is_a_real_price_and_absurd_values_are_refused(self):
        pid = self.grocery()
        for bad in (-1, MAX_PRICE + 1, 10 ** 11, True, 20.5, "abc"):
            with self.subTest(price=bad):
                with self.assertRaises(ShopError) as err:
                    self.shop.set_price(self.owner, key(), pid, bad)
                self.assertEqual(err.exception.code, "bad_price")
                self.assertEqual(self.shop.current_price(pid), 1500)
        self.assertEqual(self.shop.set_price(self.owner, key(), pid, 0)["price"], 0)
        self.assertEqual(self.shop.set_price(self.owner, key(), pid, 1)["price"], 1)
        self.assertEqual(self.shop.set_price(self.owner, key(), pid, MAX_PRICE)["price"], MAX_PRICE)
        free = self.shop.add_product(self.owner, {"code": "FREE", "name_ar": "مجاني", "name_en": "Free",
                                                  "kind": "grocery", "measure": "unit", "price": 0})
        self.assertEqual(self.shop.current_price(free), 0)

    def test_a_sale_of_250kg_is_refused(self):
        meat = self.meat()
        self.open_shift()
        with self.assertRaises(ShopError) as err:
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": meat, "qty": 250_000}]})
        self.assertEqual(err.exception.code, "qty_impossible")
        with self.assertRaises(ShopError) as err:
            self.shop.quote(self.cash1, {"lines": [{"product_id": meat, "qty": 250_000}]})
        self.assertEqual(err.exception.code, "qty_impossible")
        unit = self.grocery(code="BIG")
        with self.assertRaises(ShopError) as err:
            self.shop.sell(self.cash1, key(), {"lines": [{"product_id": unit, "qty": 10_001}]})
        self.assertEqual(err.exception.code, "qty_impossible")
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sales"), 0)


class PreUpgradeBackup(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_a_new_shop_is_not_copied_and_the_same_version_is_not_copied_again(self):
        folder = self.store.folder
        self.assertFalse((folder / "pre-upgrade").exists())
        self.store.close()
        self.store.close = lambda: None
        again = Store(folder)
        self.addCleanup(again.close)
        self.assertFalse((folder / "pre-upgrade").exists())

    def test_a_version_change_copies_with_the_backup_api_before_the_new_open(self):
        folder = self.store.folder
        pid = self.grocery()
        self.receive(pid, 4, 400, expires=None)
        self.store.db.execute("UPDATE settings SET value=? WHERE key='opened_with_version'",
                              (json.dumps("0.0.1-old"),))
        self.store.close()
        self.store.close = lambda: None
        again = Store(folder)
        self.addCleanup(again.close)
        copies = sorted((folder / "pre-upgrade").glob("schema-*.db"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(list((folder / "pre-upgrade").glob("*.partial")), [])
        conn = sqlite3.connect(str(copies[0]))
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(json.loads(conn.execute(
                "SELECT value FROM settings WHERE key='opened_with_version'").fetchone()[0]), "0.0.1-old")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM products").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertEqual(json.loads(again.db.execute(
            "SELECT value FROM settings WHERE key='opened_with_version'").fetchone()[0]), VERSION)
        third = Store(folder)
        self.addCleanup(third.close)
        self.assertEqual(len(list((folder / "pre-upgrade").glob("schema-*.db"))), 1)


if __name__ == "__main__":
    unittest.main()
