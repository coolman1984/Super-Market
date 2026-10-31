"""Portable launcher: daily backup once a day, a 'before-<version>' copy when
the program version changes, refusal on a busy port."""

import json
import pathlib
import socket
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401
from helpers import ShopFixture, key
import launcher
from server import backup


class Launcher(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        self.dest = self.folder / "backups-shop"

    def test_daily_backup_once_and_before_new_version(self):
        launcher.daily_backup(self.store, self.dest)        # empty shop: nothing to protect yet
        self.assertEqual(backup.list_backups(self.dest), [])
        self.receive(self.grocery(), 5, 500, expires=None)
        launcher.daily_backup(self.store, self.dest)
        launcher.daily_backup(self.store, self.dest)
        self.assertEqual([b["label"] for b in backup.list_backups(self.dest)], ["daily"])
        with mock.patch.object(launcher, "VERSION", "9.9.9"):
            launcher.daily_backup(self.store, self.dest)
        labels = sorted(b["label"] for b in backup.list_backups(self.dest))
        self.assertEqual(labels, ["before-9.9.9", "daily"])
        self.assertEqual(self.store.setting("last_app_version"), "9.9.9")

    def test_row40_a_long_running_program_backs_up_each_new_day(self):
        import threading
        from server import core
        self.receive(self.grocery(), 5, 500, expires=None)
        stop = threading.Event()
        threading.Thread(target=launcher.backup_loop, args=(self.store, self.dest, stop, 0.05), daemon=True).start()
        deadline = __import__("time").time() + 5
        while __import__("time").time() < deadline and not backup.list_backups(self.dest):
            __import__("time").sleep(0.05)
        self.assertEqual(len(backup.list_backups(self.dest)), 1)
        core.set_clock(core.now().replace(day=31))                      # the program is still open next day
        deadline = __import__("time").time() + 5
        while __import__("time").time() < deadline and len(backup.list_backups(self.dest)) < 2:
            __import__("time").sleep(0.05)
        stop.set()
        self.assertEqual(len(backup.list_backups(self.dest)), 2)

    def test_busy_port_is_detected(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            self.assertIs(launcher.running_here(port, "shop"), False)

    def test_s04_other_shop_and_lan_mode_are_told_apart(self):
        import threading
        from server.app import serve
        with tempfile.TemporaryDirectory() as tmp:
            a, b = pathlib.Path(tmp) / "shop-a", pathlib.Path(tmp) / "shop-b"
            httpd = serve(a, "shop", port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                port = httpd.server_address[1]
                self.assertEqual(launcher.running_here(port, "shop", a), ("same", False))
                serve_b = serve(b, "shop", port=0)            # creates folder b with its own identity
                serve_b.server_close(); serve_b.app.store.close()
                self.assertEqual(launcher.running_here(port, "shop", b), "other")
                self.assertEqual(launcher.running_here(port, "training", a), "other")
            finally:
                httpd.shutdown(); httpd.server_close(); httpd.app.store.close()

    def test_unwritable_folder_refused(self):
        with mock.patch.object(pathlib.Path, "write_text", side_effect=PermissionError):
            self.assertFalse(launcher.writable(self.folder / "x"))


if __name__ == "__main__":
    unittest.main()
