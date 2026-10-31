"""Cloud copy: change capture, push to the real Worker code (run locally by node), restore into a
new folder, commands, refusals. Skipped when node >= 22.5 is not installed."""

import json
import os
import pathlib
import shutil
import subprocess
import sqlite3
import time
import unittest

from helpers import ROOT, ShopFixture, key
from server import cloudsync, schema
from server.core import ShopError
from server.db import Store
from server.services import Shop

NODE = shutil.which("node")
CLOUD = ROOT / "cloud"


def node_ok():
    if not NODE:
        return False
    out = subprocess.run([NODE, "-e", "require('node:sqlite')"], capture_output=True)
    return out.returncode == 0


@unittest.skipUnless(node_ok(), "node with node:sqlite is needed to run the cloud service locally")
class CloudCopy(ShopFixture, unittest.TestCase):
    ADMIN = "a" * 40

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, PORT="0", MASTER_KEY="m" * 48, ADMIN_KEY=cls.ADMIN)
        cls.proc = subprocess.Popen([NODE, "--no-warnings", str(CLOUD / "dev" / "server.js")], env=env, cwd=str(CLOUD),
                                    stdout=subprocess.PIPE, text=True)
        line = cls.proc.stdout.readline()
        assert line.startswith("READY"), line
        cls.url = "http://127.0.0.1:%s" % line.split()[1]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        cls.proc.stdout.close()

    # ---- helpers
    def new_store(self):
        made = cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/stores", {"name": "test shop"})
        return made["store_id"]

    def device(self, store_id, role="shop"):
        code = cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/pairing", {"store_id": store_id, "role": role})["code"]
        return cloudsync.pair(self.url, store_id, code, "test pc")

    def connect(self):
        self.make_shop()
        self.pid = self.grocery()
        self.receive(self.pid, 500, 50000, expires=None)
        self.open_shift()
        self.sell_one()
        self.store_id = self.new_store()
        self.config = self.device(self.store_id)
        cloudsync.save_config(self.store.folder, self.config)
        with self.store.lock:
            cloudsync.enable(self.store.db, self.store.mode)
        self.sync = cloudsync.CloudSync(self.store.folder)
        self.addCleanup(self.sync.close)

    def sell_one(self):
        return self.shop.sell(self.cash1, key(), {"lines": [{"product_id": self.pid, "qty": 1}]})

    def counts(self, db):
        return {t: db.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0] for t in cloudsync.synced_tables(db)}

    # ---- tests
    def test_a_shop_that_never_connects_records_nothing(self):
        self.make_shop()
        pid = self.grocery()
        self.receive(pid, 5, 100, expires=None)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sync_changes"), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'sync_cap_%'"), 0)

    def test_training_data_is_never_uploaded(self):
        self.make_shop("training")
        with self.assertRaises(ShopError) as ctx:
            cloudsync.enable(self.store.db, "training")
        self.assertEqual(ctx.exception.code, "sync_shop_only")
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'sync_cap_%'"), 0)

    def test_first_copy_then_each_change_arrives_and_counts_match(self):
        self.connect()
        self.assertGreater(self.sync.pending(), 50)
        self.sync.drain()
        self.assertEqual(self.sync.pending(), 0)
        self.assertTrue(self.sync.status()["complete"], self.sync.status())
        self.sell_one()
        self.sell_one()
        self.assertGreater(self.sync.pending(), 0)
        self.sync.drain()
        status = self.sync.status()
        self.assertTrue(status["complete"], status)
        cloud = cloudsync.Cloud(self.config)
        rows = cloud.pull(0, 500)["records"]
        self.assertTrue(any(r["t"] == "sales" for r in rows))

    def test_passwords_sessions_and_local_state_stay_home(self):
        self.connect()
        self.sync.drain()
        cloud = cloudsync.Cloud(self.config)
        seen, after = [], 0
        while True:
            page = cloud.pull(after, 500)
            seen += page["records"]
            after = page["next"]
            if not page["more"]:
                break
        tables = {r["t"] for r in seen}
        self.assertNotIn("sessions", tables)
        self.assertNotIn("stock_balances", tables)
        self.assertNotIn("outbox", tables)
        self.assertNotIn("sync_meta", tables)
        blob = json.dumps(seen)
        self.assertNotIn("pw_hash", blob)
        self.assertNotIn("pw_salt", blob)
        self.assertNotIn("pin_hash", blob)
        self.assertNotIn("pin_salt", blob)
        users = [r for r in seen if r["t"] == "users"]
        self.assertEqual(len(users), 6)
        self.assertFalse([r for r in seen if r["t"] == "settings" and r["d"]["key"] == "last_app_version"])

    def test_deletion_reaches_the_cloud_as_a_tombstone(self):
        self.connect()
        with self.store.lock:
            self.store.db.execute("INSERT INTO suppliers(name,created_at) VALUES('Temp supplier','2026-10-30')")
        self.sync.drain()
        self.assertEqual(self.sync.status()["tables_differ"], {})
        with self.store.lock:
            self.store.db.execute("DELETE FROM suppliers WHERE name='Temp supplier'")
        self.sync.drain()
        self.assertEqual(self.sync.status()["tables_differ"], {})
        gone = [r for r in cloudsync.Cloud(self.config).pull(0, 500)["records"] if r["t"] == "suppliers"]
        self.assertTrue(gone and any(r["d"] is None for r in gone))

    def test_failed_push_keeps_changes_and_retries_without_duplicates(self):
        self.connect()
        self.sync.drain()
        self.sell_one()
        good = self.sync.cloud
        self.sync.cloud = cloudsync.Cloud(dict(self.config, url="http://127.0.0.1:9"))     # nothing listens there
        with self.assertRaises(cloudsync.CloudError) as ctx:
            self.sync.push_once()
        self.assertEqual(ctx.exception.status, 0)
        waiting = self.sync.pending()
        self.assertGreater(waiting, 0)
        self.sync.cloud = good
        self.sync.drain()
        self.assertEqual(self.sync.pending(), 0)
        self.assertTrue(self.sync.status()["complete"])

    def test_wrong_secret_and_revoked_device_are_refused(self):
        self.connect()
        bad = cloudsync.Cloud(dict(self.config, secret="0" * 64))
        with self.assertRaises(cloudsync.CloudError) as ctx:
            bad.summary()
        self.assertEqual(ctx.exception.status, 401)
        cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/revoke", {"store_id": self.store_id, "device_id": self.config["device_id"]})
        with self.assertRaises(cloudsync.CloudError):
            self.sync.push_once()

    def test_restore_on_a_new_pc_rebuilds_the_shop_and_keeps_syncing(self):
        self.connect()
        self.sell_one()
        self.sync.drain()
        self.sync.publish_reports(force=True)          # phone reports sit in the cloud too; a restore must ignore them
        mine = self.counts(self.store.db)
        new_pc = self.device(self.store_id)
        target = self.folder / "new-pc"
        report = cloudsync.restore_from_cloud(cloudsync.Cloud(new_pc), target)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["foreign_key_problems"], 0)
        cloudsync.save_config(target, new_pc)
        restored = Store(target, "shop")
        try:
            self.assertEqual(self.counts(restored.db), mine)
            shop = Shop(restored)
            self.assertEqual(shop.available(self.pid), self.shop.available(self.pid))
            # passwords are not in the cloud: nobody can log in until the owner sets new ones
            with self.assertRaises(ShopError):
                shop.auth.login("owner", "test-pass-123", "till-1")
            shop.auth.recover_owner("owner", "brand-new-pass-1")
            owner = shop.auth.public(restored.one("SELECT * FROM users WHERE username='owner'"))
            owner["workstation"] = "till-1"
            # keeps selling with continuing numbers, and its changes carry newer versions than the cloud's
            before = restored.scalar("SELECT MAX(id) FROM sales")
            cash = shop.auth.public(restored.one("SELECT * FROM users WHERE username='cash1'"))
            cash["workstation"] = "till-1"
            shop.sell(cash, key(), {"lines": [{"product_id": self.pid, "qty": 1}]})
            self.assertEqual(restored.scalar("SELECT MAX(id) FROM sales"), before + 1)
            self.assertGreater(restored.scalar("SELECT MIN(seq) FROM sync_changes"),
                               max(r["v"] for r in cloudsync.Cloud(new_pc).pull(0, 500)["records"] if not r["t"].startswith("rep_")))
        finally:
            restored.close()
        second = cloudsync.CloudSync(target)
        try:
            second.drain()
            self.assertTrue(second.status()["complete"], second.status())
        finally:
            second.close()

    def test_restore_refuses_a_folder_with_something_in_it_and_an_empty_cloud(self):
        self.connect()
        self.sync.drain()
        busy = self.folder / "busy"
        busy.mkdir()
        (busy / "x").write_text("x")
        with self.assertRaises(ShopError) as ctx:
            cloudsync.restore_from_cloud(cloudsync.Cloud(self.config), busy)
        self.assertEqual(ctx.exception.code, "restore_target_not_empty")
        empty = self.device(self.new_store())
        with self.assertRaises(ShopError) as ctx:
            cloudsync.restore_from_cloud(cloudsync.Cloud(empty), self.folder / "from-empty")
        self.assertEqual(ctx.exception.code, "cloud_copy_empty")

    def test_a_phone_may_only_ping_and_the_shop_decides_everything_else(self):
        self.connect()
        mobile = cloudsync.Cloud(self.device(self.store_id, "mobile"))
        a = mobile.submit("cmd-000001", "ping", {"n": 1})
        again = mobile.submit("cmd-000001", "ping", {"n": 1})          # same key: not run twice
        self.assertEqual(a["id"], again["id"])
        for kind in ("sale.create", "note", "nothing_like_this", "stock.adjust"):      # phones are reports-only
            with self.assertRaises(cloudsync.CloudError) as ctx:
                mobile.submit("cmd-" + kind.replace(".", "-")[:12].ljust(7, "x"), kind, {})
            self.assertEqual(ctx.exception.status, 403, kind)
        self.assertEqual(self.sync.process_commands(), 1)
        self.assertEqual(self.sync.process_commands(), 0)
        self.assertEqual(mobile.command(a["id"])["status"], "applied")
        # a command that did get into the queue but is not a known kind is still refused by the shop PC
        shop_side = cloudsync.Cloud(self.config)
        b = shop_side.submit("cmd-000002", "nothing_like_this", {})
        def refuse(payload):
            raise ShopError("no_permission")
        self.sync.handlers["forbidden"] = refuse
        c = shop_side.submit("cmd-000003", "forbidden", {})
        self.assertEqual(self.sync.process_commands(), 2)
        self.assertEqual(shop_side.command(b["id"])["status"], "rejected")
        rejected = shop_side.command(c["id"])
        self.assertEqual((rejected["status"], rejected["result"]["code"]), ("rejected", "no_permission"))
        with self.assertRaises(cloudsync.CloudError) as ctx:        # a phone cannot write rows, read raw rows or the queue
            mobile.push([{"t": "sales", "r": 1, "v": 1, "d": {}}])
        self.assertEqual(ctx.exception.status, 403)
        for call in (lambda: mobile.pull(), lambda: mobile.summary(), lambda: mobile.commands()):
            with self.assertRaises(cloudsync.CloudError) as ctx:
                call()
            self.assertEqual(ctx.exception.status, 403)

    def test_customers_with_names_phones_and_debts_are_in_the_cloud_copy(self):
        self.connect()
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "Hassan Ali", phone="01012345678", credit_limit=50000)
        self.shop.sell(self.manager, key(), {"lines": [{"product_id": self.pid, "qty": 1}], "customer_id": c,
                                             "payments": [{"method": "credit", "amount": 1000}]})
        self.sync.drain()
        got = {(r["t"], r["r"]): r["d"] for r in cloudsync.Cloud(self.config).pull(0, 500)["records"]}
        self.assertEqual(got[("customers", c)]["name"], "Hassan Ali")
        self.assertEqual(got[("customers", c)]["phone"], "01012345678")
        owed = [d for (t, _), d in got.items() if t == "customer_entries" and d["customer_id"] == c]
        self.assertEqual(sum(d["amount"] for d in owed), 1000)
        self.assertTrue(self.sync.status()["complete"])

    def test_phones_read_the_shops_own_report_figures(self):
        self.connect()
        self.open_shift(self.manager, 0)
        c = self.shop.add_party(self.owner, "customer", "Hassan Ali", phone="01012345678", credit_limit=50000)
        self.shop.sell(self.manager, key(), {"lines": [{"product_id": self.pid, "qty": 2}], "customer_id": c,
                                             "payments": [{"method": "credit", "amount": 2000}]})
        self.sync.drain()
        self.assertGreater(self.sync.publish_reports(force=True), 0)
        self.assertEqual(self.sync.publish_reports(force=True), 0, "unchanged figures are not sent again")
        phone = cloudsync.Cloud(self.device(self.store_id, "mobile"))
        today = self.store.current_business_day()
        day = phone.report("day", date=today)
        self.assertEqual(day, {k: v for k, v in self.shop.day_report(self.owner, today).items() if k not in ("near_expiry", "held")} | {"published_at": day["published_at"]})
        self.assertGreaterEqual(len(phone.report("days")["days"]), 14)
        debts = phone.report("debts")
        self.assertEqual(debts["total"], 2000)
        self.assertEqual([(d["name"], d["phone"], d["balance"]) for d in debts["customers"]], [("Hassan Ali", "01012345678", 2000)])
        stock = phone.report("stock")
        row = [p for p in stock["products"] if p["product_id"] == self.pid][0]
        self.assertEqual(row["qty"], self.store.scalar("SELECT SUM(b.qty) FROM stock_balances b JOIN lots l ON l.id=b.lot_id WHERE l.product_id=?", (self.pid,)))
        # a debt paid off disappears from the report on the next publication
        self.shop.customer_payment(self.manager, key(), c, 2000)
        self.sync.drain()
        self.sync.publish_reports(force=True)
        self.assertEqual(phone.report("debts")["total"], 0)
        # day-close summaries come from the synced closing records
        self.assertEqual(phone.report("day-closes"), {"closes": []})
        for shift, who in ((self.store.scalar("SELECT id FROM shifts WHERE user_id=?", (u["id"],)), u) for u in (self.cash1, self.manager)):
            self.shop.close_shift(who, key(), self.shop.shift_expected(shift))
        closed = self.shop.close_day(self.owner, key(), today)
        self.sync.drain()
        closes = phone.report("day-closes")["closes"]
        self.assertEqual([(c["day"], c["snapshot_hash"], c["snapshot"]["net_sales"]) for c in closes],
                         [(today, closed["snapshot_hash"], closed["net_sales"])])
        # restoring on another PC is not confused by the report rows
        self.assertTrue(self.sync.status()["complete"])

    def test_leaving_stops_recording_and_joining_again_resends_everything(self):
        self.connect()
        self.sync.drain()
        with self.store.lock:
            cloudsync.disable(self.store.db)
        self.sell_one()
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sync_changes"), 0)
        with self.store.lock:
            cloudsync.enable(self.store.db, "shop")
        self.sync.drain()
        self.assertTrue(self.sync.status()["complete"])

    def test_a_new_layout_adds_triggers_for_its_new_tables_at_start(self):
        self.connect()
        with self.store.lock:
            self.store.db.execute("CREATE TABLE later_table(id INTEGER PRIMARY KEY, x TEXT)")
        cloudsync.ensure_capture(self.store.db)
        self.store.db.execute("INSERT INTO later_table(x) VALUES('a')")
        self.assertTrue(self.store.db.execute("SELECT 1 FROM sync_changes WHERE t='later_table'").fetchone())


class Layout(ShopFixture, unittest.TestCase):
    def test_migration_8_only_adds_two_empty_tables(self):
        self.make_shop()
        self.assertEqual(self.store.schema_version(), schema.SCHEMA_VERSION)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sync_changes"), 0)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM sync_meta"), 0)

    def test_signature_changes_with_every_part(self):
        # that this rule is the Worker's rule is proven by every request in CloudCopy above
        sig = cloudsync.sign("s3cret", "POST", "/v1/push", 1700000000, "n" * 32, b'{"a":1}', "st", "dev")
        self.assertEqual(len(sig), 64)
        self.assertEqual(sig, cloudsync.sign("s3cret", "POST", "/v1/push", 1700000000, "n" * 32, b'{"a":1}', "st", "dev"))
        self.assertNotEqual(sig, cloudsync.sign("s3cret", "POST", "/v1/push", 1700000001, "n" * 32, b'{"a":1}', "st", "dev"))

    def test_every_call_names_itself_because_cloudflare_blocks_the_default_agent(self):
        from unittest import mock
        seen = {}

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        def fake(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return Reply()
        with mock.patch("urllib.request.urlopen", fake):
            cloudsync.Cloud({"url": "https://x.example", "store_id": "s", "device_id": "d", "secret": "k"}).summary()
        self.assertTrue(seen["user-agent"].startswith("SuperMarket-"))


if __name__ == "__main__":
    unittest.main()
