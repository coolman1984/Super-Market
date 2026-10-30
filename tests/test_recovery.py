"""D01/D02/S02: backup while selling, restore into a new folder and keep
selling, pre-upgrade copy, failed migration leaves the database unchanged."""

import pathlib
import sqlite3
import threading
import unittest

from helpers import ShopFixture, key
from server import backup, schema
from server.core import ShopError
from server.db import Store
from server.services import Shop


class Recovery(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_d01_backup_during_sales_then_restore_elsewhere(self):
        pid = self.grocery()
        self.receive(pid, 500, 50000, expires=None)
        self.open_shift()
        stop = threading.Event()

        def sell():
            while not stop.is_set():
                self.shop.sell(self.cash1, key(), {"lines": [{"product_id": pid, "qty": 1}]})
        worker = threading.Thread(target=sell)
        worker.start()
        try:
            made = backup.create_backup(self.store, self.folder / "backups")
        finally:
            stop.set()
            worker.join()
        self.assertEqual(made["integrity"], "ok")
        manifest = backup.verify_backup(made["folder"])
        self.assertEqual(manifest["schema"], schema.SCHEMA_VERSION)
        target = self.folder / "replacement-pc"
        backup.restore_backup(made["folder"], target)
        restored = Store(target)
        try:
            shop = Shop(restored)
            self.assertTrue(shop.reconcile()["ok"])
            sold = restored.scalar("SELECT COUNT(*) FROM sales")
            self.assertEqual(shop.available(pid), 500 - sold)
            cash = self.actor("cash1")
            shop.sell(cash, key(), {"lines": [{"product_id": pid, "qty": 1}]})   # shift carried over
        finally:
            restored.close()
        with self.assertRaises(ShopError):
            backup.restore_backup(made["folder"], target)        # never overwrite

    def test_tampered_backup_is_refused(self):
        made = backup.create_backup(self.store, self.folder / "backups")
        with open(pathlib.Path(made["folder"]) / "shop.db", "ab") as fh:
            fh.write(b"x")
        with self.assertRaises(ShopError):
            backup.verify_backup(made["folder"])

    def test_d02_upgrade_copies_first_and_failed_migration_rolls_back(self):
        pid = self.grocery()
        self.receive(pid, 5, 500, expires=None)
        path = self.store.folder
        self.store.close()

        def broken(conn):
            conn.execute("CREATE TABLE new_feature(x)")
            raise RuntimeError("power cut in the middle")
        store = Store(path)
        try:
            with self.assertRaises(RuntimeError):
                store.migrate(schema.MIGRATIONS + [(2, "broken", broken)])
        finally:
            store.close()
        conn = sqlite3.connect(str(path / "shop.db"))
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='new_feature'").fetchone())
        self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], 1)
        conn.close()
        self.assertTrue(list((path / "pre-upgrade").glob("schema-1-*.db")))

        def good(conn):
            conn.execute("CREATE TABLE new_feature(x)")
        store = Store(path)
        store.migrate(schema.MIGRATIONS + [(2, "good", good)])
        self.assertEqual(store.schema_version(), 2)
        self.assertEqual(Shop(store).available(pid), 5)
        store.close()


if __name__ == "__main__":
    unittest.main()
