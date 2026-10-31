"""Cloud backup end to end: the real Worker code (cloud/src/worker.js) runs on a local
Node server with stand-ins for D1 and R2, and the shop talks to it like to Cloudflare.
Owner order 31 Oct 2026: a copy every hour and one at night; one setup, then automatic;
each PC its own space."""

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import helpers  # noqa: F401
from helpers import CAIRO, ROOT, ShopFixture
from server import cloud, core
from server.db import Store

NODE = shutil.which("node")
MASTER = "master-key-for-tests-0123456789abcdef-0123"


@unittest.skipUnless(NODE, "Node.js is needed to run the cloud Worker locally")
class CloudBackup(ShopFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.proc = subprocess.Popen([NODE, "--no-warnings", str(ROOT / "cloud" / "dev" / "server.js")],
                                    env={**os.environ, "MASTER_KEY": MASTER, "PORT": "0", "DEV_EACH_CALL_NEW_IP": "1"},
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        line = cls.proc.stdout.readline()
        if not line.startswith("READY"):
            cls.proc.kill()
            raise RuntimeError("cloud dev server did not start: " + line + cls.proc.stderr.read())
        cls.url = "http://127.0.0.1:%s" % line.split()[1]

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()

    def setUp(self):
        self.make_shop()
        self.home = self.folder / "home"
        self.home.mkdir()

    def pc(self, home=None, store=None):
        return cloud.Cloud(home or self.home, store or self.store, self.url, label="Test shop")

    def at(self, hour, day=30):
        core.set_clock(dt.datetime(2026, 10, day, hour, 0, tzinfo=CAIRO))

    def test_first_start_connects_by_itself_and_later_starts_reuse_the_same_space(self):
        c = self.pc()
        self.assertFalse(c.connected())
        self.assertTrue(c.connect())
        pc_id = c.data["pc"]
        again = self.pc()                       # the program started again on the same PC
        self.assertTrue(again.connected())
        self.assertFalse(again.connect(), "no second setup")
        self.assertEqual(again.data["pc"], pc_id)

    def test_a_lost_answer_is_asked_again_as_the_same_pc(self):
        c = self.pc()
        c.connect()
        first = c.data["pc"]
        c.data.pop("pc")
        c.data.pop("secret")                    # the answer never arrived; the install id was kept
        c._save()
        self.assertTrue(self.pc().connect())
        self.assertEqual(json.loads((self.home / "cloud.json").read_text())["pc"], first)

    def test_each_pc_has_its_own_space(self):
        a = self.pc()
        other_home = self.folder / "other"
        other_home.mkdir()
        other_store = Store(self.folder / "other-data", "shop")
        self.addCleanup(other_store.close)
        b = self.pc(other_home, other_store)
        self.at(3)
        a.tick()
        b.tick()
        self.assertNotEqual(a.data["pc"], b.data["pc"])
        self.assertEqual(len(a.client().copies()), 1)
        self.assertEqual(len(b.client().copies()), 1)
        self.assertNotEqual(a.client().copies()[0]["dbSha256"], b.client().copies()[0]["dbSha256"])

    def test_a_copied_data_folder_gets_its_own_space_not_the_old_pcs(self):
        a = self.pc()
        a.connect()
        with mock.patch.object(cloud.socket, "gethostname", return_value="NEW-PC"):
            b = self.pc()
            self.assertFalse(b.connected())
            b.connect()
            self.assertNotEqual(b.data["pc"], a.data["pc"])

    def test_hourly_while_something_changed_and_one_nightly_copy_a_day(self):
        c = self.pc()
        self.at(1)                                   # 1 a.m.: not yet night-copy time
        self.assertEqual(c.due(), "hourly")          # first copy ever
        c.tick()
        self.assertEqual([x["kind"] for x in c.client().copies()], ["hourly"])
        self.assertIsNone(c.due(), "less than an hour later: nothing")
        c.data["hourly_at"] = time.time() - 3700
        self.assertIsNone(c.due(), "an hour later but nothing changed: nothing")
        self.receive(self.grocery(), 5, 500, expires=None)
        self.assertEqual(c.due(), "hourly")
        self.at(2)
        self.assertEqual(c.due(), "nightly")
        c.tick()
        self.assertIsNone(c.due())
        kinds = sorted(x["kind"] for x in c.client().copies())
        self.assertEqual(kinds, ["hourly", "nightly"])
        self.at(23)
        self.assertNotEqual(c.due(), "nightly", "one nightly copy per day")
        self.at(2, day=31)
        self.assertEqual(c.due(), "nightly")

    def test_no_internet_keeps_the_copy_and_sends_it_when_the_internet_is_back(self):
        c = self.pc()
        c.connect()
        c.url = c.data["url"] = "http://127.0.0.1:%d" % self.closed_port()
        c._save()
        self.at(3)
        st = c.tick()
        self.assertEqual(st["error"], "offline")
        self.assertEqual(st["queued"], 1, "the copy waits on this PC")
        c.url = c.data["url"] = self.url
        c._save()
        st = c.tick(force=True)
        self.assertIsNone(st["error"])
        self.assertEqual(st["queued"], 0)
        names = [x["name"] for x in c.client().copies()]
        self.assertTrue(any(n.startswith("nightly-") for n in names))

    def test_long_offline_keeps_only_the_newest_hourly_and_two_weeks_of_nightly(self):
        c = self.pc()
        for day in range(1, 20):
            self.at(3, day=day)
            c.snapshot("nightly")
            self.at(4, day=day)
            c.snapshot("hourly")
        q = c.queued()
        self.assertEqual(len([m for m in q if m["kind"] == "hourly"]), 1)
        self.assertEqual(len([m for m in q if m["kind"] == "nightly"]), cloud.KEEP_QUEUED_NIGHTLY)

    def test_the_status_never_shows_the_secret(self):
        c = self.pc()
        self.at(3)
        st = c.tick()
        self.assertTrue(st["connected"])
        self.assertNotIn(c.data["secret"], json.dumps(st))
        self.assertEqual(len(st["pc"]), 6)

    def test_training_never_sends_anything(self):
        c = self.pc()
        c.store = mock.Mock(mode="training")
        self.assertFalse(c.enabled)
        c.tick(force=True)
        self.assertFalse((self.home / "cloud.json").exists())

    def test_restore_on_a_new_pc_brings_the_newest_copy_and_keeps_its_own_space(self):
        self.receive(self.grocery(), 5, 500, expires=None)
        c = self.pc()
        self.at(3)
        c.tick()
        code = c.recovery_code()
        self.assertTrue(code.startswith("SMC1-"))
        new_data = self.folder / "new-pc-data"
        pick = cloud.restore(code, new_data, self.folder / "work")
        self.assertTrue(pick["name"].startswith("nightly-"))
        restored = Store(new_data, "shop")
        self.addCleanup(restored.close)
        self.assertEqual(restored.scalar("SELECT COUNT(*) FROM receipts"), self.store.scalar("SELECT COUNT(*) FROM receipts"))
        new_home = self.folder / "new-home"
        new_home.mkdir()
        mine = self.pc(new_home, restored)
        mine.connect()
        self.assertNotEqual(mine.data["pc"], c.data["pc"], "the new PC never sends into the old PC's space")

    def test_restore_never_overwrites_existing_data_and_refuses_a_bad_code(self):
        c = self.pc()
        self.at(3)
        c.tick()
        with self.assertRaises(cloud.CloudError):
            cloud.restore("SMC1-not-a-code", self.folder / "x", self.folder / "w")
        from server.core import ShopError
        with self.assertRaises(ShopError):
            cloud.restore(c.recovery_code(), self.folder / "data", self.folder / "w2")   # the live shop folder

    def test_start_menu_restore_on_a_new_pc_that_opened_the_program_once(self):
        import launcher
        self.receive(self.grocery(), 5, 500, expires=None)
        c = self.pc()
        self.at(3)
        c.tick()
        new_home = self.folder / "new-pc"
        empty = Store(new_home / "shop-data", "shop")           # Setup opened the program once: empty database
        empty.close()
        with mock.patch.object(launcher, "HOME", new_home), mock.patch.object(launcher, "running_here", return_value=None), \
                mock.patch("builtins.input", return_value=c.recovery_code()):
            self.assertEqual(launcher.cloud_restore(new_home / "shop-data"), 0)
            restored = Store(new_home / "shop-data", "shop")
            self.addCleanup(restored.close)
            self.assertEqual(restored.scalar("SELECT COUNT(*) FROM receipts"), 1)
            self.assertEqual(len(list(new_home.glob("shop-data-empty-*"))), 1, "the empty folder is kept aside, not deleted")
            # a second restore over a working shop is refused
            self.assertEqual(launcher.cloud_restore(new_home / "shop-data"), 1)

    def test_api_status_and_send_now_need_the_backup_permission(self):
        from server.app import App
        app = App(self.folder / "data2", "shop")
        self.addCleanup(app.store.close)
        boss = self.owner_of(app)
        self.assertEqual(app.cloud_status(boss, {}, {}), {"enabled": False})
        app.cloud = cloud.Cloud(self.home, app.store, self.url)
        self.assertTrue(app.cloud_now(boss, {}, {})["started"])
        with self.assertRaises(Exception):
            app.cloud_status({"id": 2, "role": "cashier", "workstation": "t"}, {}, {})

    def owner_of(self, app):
        uid = app.store.tx(lambda: app.auth.create_user("boss", "Boss", "owner", "test-pass-123"))
        user = app.auth.public(app.store.one("SELECT * FROM users WHERE id=?", (uid,)))
        user["workstation"] = "t"
        return user

    @staticmethod
    def closed_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


class CloudAddress(unittest.TestCase):
    def test_source_runs_never_send_and_SM_CLOUD_URL_wins(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SM_CLOUD_URL", None)
            self.assertEqual(cloud.cloud_url(False), "")
            self.assertEqual(cloud.cloud_url(True), cloud.BUILT_IN_URL)
            os.environ["SM_CLOUD_URL"] = ""
            self.assertEqual(cloud.cloud_url(True), "", "an empty value turns the cloud off")
            os.environ["SM_CLOUD_URL"] = "off"
            self.assertEqual(cloud.cloud_url(True), "", "CI on Windows uses off")

    def test_signature_covers_the_body_hash_and_the_pc(self):
        h = cloud.sign("s" * 40, "a" * 32, "PUT", "/pc/copies/x", "b" * 64, t=1, nonce="0" * 32)
        h2 = cloud.sign("s" * 40, "a" * 32, "PUT", "/pc/copies/x", "c" * 64, t=1, nonce="0" * 32)
        h3 = cloud.sign("s" * 40, "d" * 32, "PUT", "/pc/copies/x", "b" * 64, t=1, nonce="0" * 32)
        self.assertNotEqual(h["X-SM-Sig"], h2["X-SM-Sig"])
        self.assertNotEqual(h["X-SM-Sig"], h3["X-SM-Sig"])


if __name__ == "__main__":
    unittest.main()
