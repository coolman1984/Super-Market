"""The installed program really talks to the cloud: the built-in address, the one-code connection from the
Start menu, the background copy, the restore on a new PC, and the Arabic texts of every cloud error.
The Worker itself is the real code run locally (cloud/dev/server.js), so the whole path is exercised."""
import io
import os
import pathlib
import re
import subprocess
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
        for command in ("cloud-join", "cloud-status", "cloud-restore"):
            self.assertIn(command, iss)
        self.assertNotIn("cloud-code", iss)
        ci = (ROOT / ".github" / "workflows" / "windows-portable.yml").read_text(encoding="utf-8")
        self.assertNotIn("cloud.test.js", ci)


@unittest.skipUnless(node_ok(), "node with node:sqlite is needed to run the cloud service locally")
class StartMenuToRestore(ShopFixture, unittest.TestCase):
    ADMIN = "b" * 40

    @classmethod
    def setUpClass(cls):
        env = dict(os.environ, PORT="0", MASTER_KEY="n" * 48, ADMIN_KEY=cls.ADMIN)
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

    def code(self, store_id, role="shop"):
        made = cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/pairing", {"store_id": store_id, "role": role})
        return "SMC2-%s-%s" % (store_id, made["code"])

    def run_cmd(self, fn, data, typed):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SM_CLOUD_URL": self.url}), mock.patch("builtins.input", return_value=typed), \
                mock.patch.object(launcher, "running_here", return_value=None), redirect_stdout(out):
            code = fn(data)
        return code, out.getvalue()

    def test_join_copy_status_restore_leave(self):
        self.make_shop()
        data = self.folder / "data"
        pid = self.grocery()
        self.shop.receive(self.owner, __import__("helpers").key(), {"lines": [{"product_id": pid, "qty": 10, "line_cost": 8000,
                          "location_id": self.shelf, "expires": "2026-11-05"}]})
        store_id = cloudsync.admin_call(self.url, self.ADMIN, "POST", "/admin/stores", {"name": "wiring"})["store_id"]
        self.store.close()

        # before joining: nothing runs, the app says it can be connected
        app = mock.Mock(cloud=None)
        with mock.patch.dict(os.environ, {"SM_CLOUD_URL": self.url}):
            self.assertIsNone(launcher.start_cloud(app, "shop", mock.Mock(), data))
        # a wrong code changes nothing
        code, out = self.run_cmd(launcher.cloud_join, data, "SMC2-%s-WRONG" % store_id)
        self.assertEqual(code, 1)
        self.assertIsNone(cloudsync.load_config(data))
        # the vendor's code connects it
        code, out = self.run_cmd(launcher.cloud_join, data, self.code(store_id))
        self.assertEqual(code, 0, out)
        self.assertEqual(cloudsync.load_config(data)["store_id"], store_id)
        code, out = self.run_cmd(launcher.cloud_join, data, self.code(store_id))
        self.assertEqual(code, 1)                                  # already joined
        # training never copies
        with mock.patch.dict(os.environ, {"SM_CLOUD_URL": self.url}):
            self.assertIsNone(launcher.start_cloud(app, "training", mock.Mock(), data))
        # CI / test machines can switch it off even when joined
        with mock.patch.dict(os.environ, {"SM_CLOUD_URL": "off"}):
            self.assertIsNone(launcher.start_cloud(app, "shop", mock.Mock(), data))
        # the program starts the copy by itself
        with mock.patch.dict(os.environ, {"SM_CLOUD_URL": self.url}):
            sync = launcher.start_cloud(app, "shop", mock.Mock(is_set=mock.Mock(return_value=True)), data)
        self.assertIsNotNone(sync)
        self.assertIs(app.cloud, sync)
        sync.drain()
        status = sync.status()
        self.assertTrue(status["complete"], status)
        self.assertEqual(sync.status_light()["pending"] if "pending" in sync.status_light() else 0, 0)
        sync.close()
        code, out = self.run_cmd(launcher.cloud_status, data, "")
        self.assertEqual(code, 0, out)

        # a NEW PC: restore with a fresh one-time code, into an empty folder
        new = self.folder / "newpc" / "data"
        new.parent.mkdir()
        code, out = self.run_cmd(launcher.cloud_restore, new, "SMC2-%s-WRONG" % store_id)
        self.assertEqual(code, 1)
        self.assertFalse(new.exists() and any(new.iterdir()))
        with mock.patch.object(launcher, "owner_recovery", return_value=0) as recovery:
            code, out = self.run_cmd(launcher.cloud_restore, new, self.code(store_id))
        self.assertEqual(code, 0, out)
        recovery.assert_called_once()
        self.assertIn("سليمة", out)
        from server.db import Store
        restored = Store(new, "shop")
        try:
            self.assertEqual(restored.scalar("SELECT COUNT(*) FROM products"), 1)
            self.assertEqual(restored.scalar("PRAGMA foreign_key_check") or 0, 0)
            self.assertTrue(restored.scalar("SELECT COUNT(*) FROM users WHERE pw_hash='!restored'"))
        finally:
            restored.close()
        # a PC that already has shop data is never overwritten
        code, out = self.run_cmd(launcher.cloud_restore, data, self.code(store_id))
        self.assertEqual(code, 1)
        # leave
        code, out = self.run_cmd(launcher.cloud_leave, data, "")
        self.assertEqual(code, 0)
        self.assertIsNone(cloudsync.load_config(data))


if __name__ == "__main__":
    unittest.main()
