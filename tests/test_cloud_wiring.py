"""The installed program really talks to the cloud with NO step from the owner: the built-in address, the first-run
enrolment with the build token, the background copy that never holds up selling, the restore on a new PC with the
recovery code, and the Arabic texts of every cloud error.
The Worker itself is the real code run locally (cloud/dev/server.js), so the whole path is exercised."""
import hashlib
import hmac
import io
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import ROOT, ShopFixture
from server import cloudsync
from server.core import ShopError

import launcher
from test_cloudsync import NODE, CLOUD, node_ok

ALL_ERRORS = ("bad_connect_code", "sync_not_connected", "sync_shop_only", "cloud_copy_empty", "cloud_copy_newer_than_app",
              "cloud_off", "download_mismatch", "no_copies", "not_found", "rejected")


class Address(unittest.TestCase):
    def test_built_in_address_is_the_deployed_sync_worker(self):
        self.assertEqual(cloudsync.BUILT_IN_URL, "https://super-market-sync.mflma2040.workers.dev")

    def test_compiled_program_uses_it_source_runs_stay_silent(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("SM_CLOUD_URL", None)
            self.assertEqual(cloudsync.cloud_url(True), cloudsync.BUILT_IN_URL)
            self.assertEqual(cloudsync.cloud_url(False), "")
            os.environ["SM_CLOUD_URL"] = "off"
            self.assertEqual(cloudsync.cloud_url(True), "")
            os.environ["SM_CLOUD_URL"] = "http://127.0.0.1:9/"
            self.assertEqual(cloudsync.cloud_url(False), "http://127.0.0.1:9")

    def test_the_old_backup_design_is_gone_everywhere(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split("\n")
        me = pathlib.Path(__file__).name
        for name in tracked:
            if not name or name.endswith((".png", ".ico", ".woff2", ".zip", ".lock")) or name.startswith("docs/REVIEW") or name.endswith(me) \
                    or name.startswith("docs/CLAUDE_FIX") or name == "DEVELOPMENT_HISTORY.md" or name == "TASKS.md":
                continue
            path = ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            self.assertNotIn("super-market-backup", text, name)
        self.assertFalse((ROOT / "server" / "cloud.py").exists())

    def test_recovery_code_forms(self):
        self.assertEqual(cloudsync.normalize_recovery_code(" abcde fghjk-lmnpq 23456 "), "ABCDE-FGHJK-LMNPQ-23456")
        for bad in ("", "ABCDE-FGHJK", "x" * 21):
            with self.assertRaises(ShopError) as caught:
                cloudsync.normalize_recovery_code(bad)
            self.assertEqual(caught.exception.code, "bad_recovery_code")

    def test_connection_code_forms(self):
        self.assertEqual(cloudsync.parse_connect_code(" SMC2-st_abc-ABCD-EFGH "), ("st_abc", "ABCD-EFGH"))
        self.assertEqual(cloudsync.parse_connect_code("st_abc ABCD-EFGH"), ("st_abc", "ABCD-EFGH"))
        for bad in ("", "SMC2-", "SMC2-onlystore", "x"):
            with self.assertRaises(ShopError) as caught:
                cloudsync.parse_connect_code(bad)
            self.assertEqual(caught.exception.code, "bad_connect_code")

    def test_every_cloud_error_has_arabic_and_english(self):
        for lang in ("ar", "en"):
            text = (ROOT / "web" / "js" / "i18n" / (lang + ".js")).read_text(encoding="utf-8")
            for code in ALL_ERRORS:
                self.assertRegex(text, r'"err\.%s": "[^"]{8,}"' % code, (lang, code))
        ar = (ROOT / "web" / "js" / "i18n" / "ar.js").read_text(encoding="utf-8")
        for code in ("download_mismatch", "no_copies", "not_found", "rejected"):
            line = re.search(r'"err\.%s": "([^"]*)"' % code, ar).group(1)
            self.assertTrue(re.search("[\u0600-\u06ff]", line), code)

    def test_the_packaged_build_ships_the_sync_client(self):
        build = (ROOT / "tools" / "build_windows.py").read_text(encoding="utf-8")
        self.assertIn("--include-package=server", build)          # server/cloudsync.py travels inside the exe
        iss = (ROOT / "installer" / "super-market.iss").read_text(encoding="utf-8")
        self.assertIn("cloud-restore", iss)                        # the only cloud entry in the Start menu: a new PC
        for command in ("cloud-join", "cloud-status", "cloud-code"):
            self.assertNotIn(command, iss)                         # nothing to connect by hand
        window = (ROOT / "server" / "window.py").read_text(encoding="utf-8")
        self.assertIn("--user-data-dir", window)
        ci = (ROOT / ".github" / "workflows" / "windows-portable.yml").read_text(encoding="utf-8")
        self.assertNotIn("cloud.test.js", ci)


@unittest.skipUnless(node_ok(), "node with node:sqlite is needed to run the cloud service locally")
class ZeroSetup(ShopFixture, unittest.TestCase):
    ADMIN = "b" * 40
    ENROLL = "e" * 40

    def setUp(self):
        # a fresh local service for every test: each starts with its own empty database and its own per-address limits
        env = dict(os.environ, PORT="0", MASTER_KEY="n" * 48, ADMIN_KEY=self.ADMIN, ENROLL_KEY=self.ENROLL)
        proc = subprocess.Popen([NODE, "--no-warnings", str(CLOUD / "dev" / "server.js")], env=env, cwd=str(CLOUD),
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        line = proc.stdout.readline()
        assert line.startswith("READY"), line
        self.url = "http://127.0.0.1:%s" % line.split()[1]
        build = "v0.1.0-test"
        sig = hmac.new(self.ENROLL.encode(), ("SM-ENROLL1|" + build).encode(), hashlib.sha256).hexdigest()[:32]
        self.token = "%s.%s" % (build, sig)

    def env(self, **extra):
        return mock.patch.dict(os.environ, dict({"SM_CLOUD_URL": self.url, "SM_ENROLL_TOKEN": self.token}, **extra))

    def run_cmd(self, fn, data, typed):
        out = io.StringIO()
        with self.env(), mock.patch("builtins.input", return_value=typed), \
                mock.patch.object(launcher, "running_here", return_value=None), redirect_stdout(out):
            code = fn(data)
        return code, out.getvalue()

    def started(self, data, url=None):
        """What the launcher does at start-up, with the loop running in a thread."""
        app = mock.Mock(cloud=None)
        stop = threading.Event()
        before = set(threading.enumerate())
        with self.env(SM_CLOUD_URL=url or self.url):
            auto = launcher.start_cloud(app, "shop", stop, data)
        threads = [t for t in threading.enumerate() if t not in before and t.name == "cloud"]

        def finish():                       # Windows cannot delete shop.db while the loop still has it open
            stop.set()
            auto.wake.set()
            for t in threads:
                t.join(15)
        self.addCleanup(finish)
        return auto, stop

    def wait_for(self, check, seconds=30):
        end = time.time() + seconds
        while time.time() < end:
            if check():
                return True
            time.sleep(0.1)
        return False

    def stocked(self):
        self.make_shop()
        pid = self.grocery()
        self.shop.receive(self.owner, __import__("helpers").key(), {"lines": [{"product_id": pid, "qty": 10, "line_cost": 8000,
                          "location_id": self.shelf, "expires": "2026-11-05"}]})
        return self.folder / "data", pid

    def test_first_run_connects_by_itself_and_copies_everything(self):
        data, pid = self.stocked()
        self.assertIsNone(cloudsync.load_config(data))
        auto, stop = self.started(data)
        self.assertIsInstance(auto, cloudsync.CloudAuto)
        self.assertTrue(self.wait_for(lambda: auto.sync is not None), auto.last_error_kind)
        config = cloudsync.load_config(data)
        self.assertEqual(config["role"], "shop")
        self.assertEqual(config["status"], "pending")                  # a trial store (the licence hub activates it later)
        self.assertRegex(config["recovery_code"], r"^([A-Z2-9]{5}-){3}[A-Z2-9]{5}$")
        self.assertEqual(len(json.loads((data / cloudsync.INSTALL_NAME).read_text())["install_id"]), 32)
        self.assertTrue(self.wait_for(lambda: auto.sync.pending() == 0 and auto.sync.status()["complete"]), auto.sync.status())
        light = auto.status_light()
        self.assertTrue(light["enabled"] and light["recovery_available"] and light["trial"])
        self.assertFalse(light["enrolling"])
        self.assertNotIn("secret", json.dumps(light))
        stop.set()
        auto.wake.set()

    def test_selling_never_waits_for_the_network(self):
        data, pid = self.stocked()
        self.open_shift()
        auto, stop = self.started(data, url="http://127.0.0.1:9")         # nothing listens: no internet
        self.assertTrue(self.wait_for(lambda: auto.last_error_kind == "offline"))
        began = time.time()
        for _ in range(3):
            self.shop.sell(self.cash1, __import__("helpers").key(), {"lines": [{"product_id": pid, "qty": 1}]})
        self.assertLess(time.time() - began, 5)
        light = auto.status_light()
        self.assertTrue(light["enrolling"])
        self.assertEqual(light["error"], "offline")
        self.assertFalse(light["recovery_available"])
        self.assertIsNone(cloudsync.load_config(data))
        stop.set()

    def test_a_lost_answer_or_a_new_computer_name_never_makes_a_second_store(self):
        data, _ = self.stocked()
        first = cloudsync.CloudAuto(data, self.url, self.token).enroll_now()
        (data / cloudsync.CONFIG_NAME).unlink()                          # the answer was lost before it was saved
        again = cloudsync.CloudAuto(data, self.url, self.token).enroll_now()
        self.assertEqual(first["store_id"], again["store_id"])
        self.assertEqual(first["recovery_code"], again["recovery_code"])
        with mock.patch.object(cloudsync.socket, "gethostname", return_value="renamed-pc"):     # M6, cloud half: not keyed by the name
            (data / cloudsync.CONFIG_NAME).unlink()
            renamed = cloudsync.CloudAuto(data, self.url, self.token).enroll_now()
        self.assertEqual(first["store_id"], renamed["store_id"])

    def test_source_runs_training_ci_and_token_less_builds_never_connect(self):
        data, _ = self.stocked()
        app = mock.Mock(cloud=None)
        stop = mock.Mock()
        with mock.patch.dict(os.environ):
            os.environ.pop("SM_CLOUD_URL", None)
            os.environ.pop("SM_ENROLL_TOKEN", None)
            self.assertIsNone(launcher.start_cloud(app, "shop", stop, data))            # source run
        with self.env():
            self.assertIsNone(launcher.start_cloud(app, "training", stop, data))        # training
        with self.env(SM_CLOUD_URL="off"):
            self.assertIsNone(launcher.start_cloud(app, "shop", stop, data))            # CI / test machines
        with self.env(SM_ENROLL_TOKEN=""), mock.patch.object(cloudsync, "enroll_token", return_value=""):
            self.assertIsNone(launcher.start_cloud(app, "shop", stop, data))            # a build without the token
        self.assertIsNone(cloudsync.load_config(data))
        self.assertFalse((data / cloudsync.INSTALL_NAME).exists())

    def test_a_refused_build_token_changes_nothing_and_says_why(self):
        data, _ = self.stocked()
        with self.assertRaises(cloudsync.CloudError) as caught:
            cloudsync.CloudAuto(data, self.url, "v9.9.9.0123456789abcdef0123456789abcdef").enroll_now()
        self.assertEqual(cloudsync.error_kind(caught.exception), "rejected_device")
        self.assertIsNone(cloudsync.load_config(data))

    def test_recovery_code_restores_on_a_new_pc_and_cuts_the_old_one_off(self):
        data, pid = self.stocked()
        auto, stop = self.started(data)
        self.assertTrue(self.wait_for(lambda: auto.sync is not None and auto.sync.pending() == 0), auto.last_error_kind)
        old = cloudsync.load_config(data)
        typed = old["recovery_code"].replace("-", "").lower() + " "      # typed any way the owner likes
        stop.set()
        auto.wake.set()

        new = self.folder / "newpc" / "data"
        new.parent.mkdir()
        # the program was opened once on the new PC before the restore: its empty store and folder are kept aside
        first = cloudsync.CloudAuto(new if new.exists() else self._empty(new), self.url, self.token)
        first.enroll_now()
        code, out = self.run_cmd(launcher.cloud_restore, new, "WRONGWRONGWRONGWRONG")
        self.assertEqual(code, 1, out)
        self.assertTrue((new / cloudsync.CONFIG_NAME).exists())            # a wrong code changes nothing
        code, out = self.run_cmd(launcher.cloud_restore, new, "short")
        self.assertEqual(code, 1)
        with mock.patch.object(launcher, "owner_recovery", return_value=0) as recovery:
            code, out = self.run_cmd(launcher.cloud_restore, new, typed)
        self.assertEqual(code, 0, out)
        recovery.assert_called_once()
        self.assertIn("سليمة", out)
        again = cloudsync.load_config(new)
        self.assertEqual(again["store_id"], old["store_id"])
        self.assertNotEqual(again["device_id"], old["device_id"])
        self.assertNotEqual(json.loads((new / cloudsync.INSTALL_NAME).read_text())["install_id"],
                            json.loads((data / cloudsync.INSTALL_NAME).read_text())["install_id"])
        from server.db import Store
        restored = Store(new, "shop")
        try:
            self.assertEqual(restored.scalar("SELECT COUNT(*) FROM products"), 1)
            self.assertEqual(restored.scalar("PRAGMA foreign_key_check") or 0, 0)
            self.assertTrue(restored.scalar("SELECT COUNT(*) FROM users WHERE pw_hash='!restored'"))
        finally:
            restored.close()
        # the restored PC connects itself and turns the copy on
        auto2, stop2 = self.started(new)
        self.assertTrue(self.wait_for(lambda: auto2.sync is not None), auto2.last_error_kind)
        sync_db = sqlite3.connect(str(new / "shop.db"))
        self.assertEqual(sync_db.execute("SELECT value FROM sync_meta WHERE key='enabled'").fetchone()[0], "1")
        sync_db.close()
        stop2.set()
        auto2.wake.set()
        # the old PC's device is cut off at once
        with self.assertRaises(cloudsync.CloudError) as caught:
            cloudsync.Cloud(old).summary()
        self.assertEqual(caught.exception.status, 401)
        # a PC that already has shop data is never overwritten
        code, out = self.run_cmd(launcher.cloud_restore, data, typed)
        self.assertEqual(code, 1)

    def _empty(self, path):
        path.mkdir(parents=True)
        return path

    def test_support_pairing_code_still_restores_as_a_fallback(self):
        data, _ = self.stocked()
        auto = cloudsync.CloudAuto(data, self.url, self.token)
        config = auto.enroll_now()
        auto._attach(config)
        auto.sync.drain()
        auto.sync.close()
        pairing = cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/pairing", {"store_id": config["store_id"], "role": "shop"})["code"]
        new = self.folder / "fallbackpc" / "data"
        new.parent.mkdir()
        with mock.patch.object(launcher, "owner_recovery", return_value=0):
            code, out = self.run_cmd(launcher.cloud_restore, new, "SMC2-%s-%s" % (config["store_id"], pairing))
        self.assertEqual(code, 0, out)
        self.assertEqual(cloudsync.load_config(new)["store_id"], config["store_id"])

    def test_the_owner_alone_can_read_the_recovery_code_and_it_is_audited(self):
        data, _ = self.stocked()
        auto = cloudsync.CloudAuto(data, self.url, self.token)
        auto.enroll_now()
        from server.app import App
        app = mock.Mock(cloud=auto, store=self.store)
        self.store.tx = mock.Mock(side_effect=lambda fn: fn())
        self.store.audit = mock.Mock()
        owner = self.owner
        got = App.cloud_recovery(app, owner, {}, {})
        self.assertEqual(got["code"], auto.recovery_code())
        self.store.audit.assert_called_once()
        self.assertEqual(self.store.audit.call_args[0][1], "cloud_recovery_code_shown")
        from server.auth import Forbidden
        with self.assertRaises(Forbidden):
            App.cloud_recovery(app, self.cash1, {}, {})
        with self.assertRaises(ShopError):
            App.cloud_recovery(mock.Mock(cloud=None, store=self.store), owner, {}, {})


if __name__ == "__main__":
    unittest.main()
