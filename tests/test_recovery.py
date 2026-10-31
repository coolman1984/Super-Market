"""D01/D02/S02: backup while selling, restore into a new folder and keep
selling, pre-upgrade copy, failed migration leaves the database unchanged."""

import pathlib
import threading
import sqlite3
import threading
import unittest

from helpers import ShopFixture, key
from server import backup, schema
from server.core import ShopError
from server.db import Store
from server.services import Shop

NEXT = schema.SCHEMA_VERSION + 1


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
                store.migrate(schema.MIGRATIONS + [(NEXT, "broken", broken)])
        finally:
            store.close()
        conn = sqlite3.connect(str(path / "shop.db"))
        self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='new_feature'").fetchone())
        self.assertEqual(conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], NEXT - 1)
        conn.close()
        self.assertTrue(list((path / "pre-upgrade").glob("schema-%d-*.db" % (NEXT - 1))))

        def good(conn):
            conn.execute("CREATE TABLE new_feature(x)")
        store = Store(path)
        store.migrate(schema.MIGRATIONS + [(NEXT, "good", good)])
        self.assertEqual(store.schema_version(), NEXT)
        self.assertEqual(Shop(store).available(pid), 5)
        store.close()


class ExistingDatabaseUpgrade(ShopFixture, unittest.TestCase):
    """Migration 2 on a database that already holds pilot data (schema 1)."""

    def test_v1_shop_upgrades_with_copy_and_keeps_everything(self):
        import tempfile
        from helpers import PASSWORD
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / "data"
        old = Store.__new__(Store)               # open as the 0.1.0 program did: schema 1 only
        old.folder, old.path, old.mode = path, path / "shop.db", "shop"
        path.mkdir(parents=True)
        old.lock = threading.RLock()
        old.db = sqlite3.connect(str(old.path), isolation_level=None, check_same_thread=False)
        old.db.row_factory = sqlite3.Row
        old.db.execute("PRAGMA foreign_keys=ON")
        old.migrate(schema.MIGRATIONS[:1])
        old._check_mode()
        # pilot data written with plain SQL in the 0.1.0 layout (schema 1 columns only)
        x = old.db.execute
        x("INSERT INTO users(id,username,display_name,role,pw_hash,pw_salt,pw_iter,created_at) VALUES(1,'o','O','owner','h','00',1,'t')")
        x("INSERT INTO ops(id,kind,key,fingerprint,user_id,workstation,at) VALUES(1,'production','k','f',1,'w','t')")
        x("INSERT INTO op_results VALUES(1,'{}')")
        x("INSERT INTO locations(id,code,name_ar,name_en,kind,sellable) VALUES(1,'FR','ث','F','display_fridge',1)")
        x("""INSERT INTO products(id,code,name_ar,name_en,kind,measure,needs_expiry,created_at)
             VALUES(1,'O','ناتج','Out','raw_meat','g',1,'t')""")
        x("""INSERT INTO lots(id,product_id,lot_no,origin,origin_op,expires,status,created_at)
             VALUES(1,1,'P000001','production',1,'2026-11-05','released','t')""")
        x("INSERT INTO stock_entries(op_id,kind,lot_id,location_id,qty,cost,at) VALUES(1,'production_out',1,1,1000,1000,'t')")
        x("""INSERT INTO packs(code,product_id,lot_id,net_g,tare_g,price,price_per_kg,packed_at,expires,status)
             VALUES('2800000000011',1,1,500,0,50,100,'t','2026-11-05','available')""")
        fridge = 1
        before = old.scalar("SELECT COUNT(*) FROM stock_entries")
        old.close()
        upgraded = Store(path)
        try:
            self.assertEqual(upgraded.schema_version(), schema.SCHEMA_VERSION)
            self.assertEqual(upgraded.scalar("SELECT COUNT(*) FROM stock_entries"), before)
            self.assertTrue(list((path / "pre-upgrade").glob("schema-1-*.db")))
            self.assertTrue(Shop(upgraded).reconcile()["ok"])
            self.assertIsNotNone(upgraded.setting("instance_id"))
            self.assertEqual(upgraded.scalar("SELECT location_id FROM packs"), fridge)   # backfilled
        finally:
            upgraded.close()


if __name__ == "__main__":
    unittest.main()
