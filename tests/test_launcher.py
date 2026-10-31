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

    def test_h6_second_windows_account_opens_the_running_program_or_is_told_why(self):
        with mock.patch.object(launcher, "COMPILED", True), mock.patch.object(launcher, "writable", return_value=False), \
                mock.patch.object(launcher.window, "open_ui") as open_ui, mock.patch.object(launcher, "tell") as tell:
            with mock.patch.object(launcher, "running_here", return_value=("same", False)):
                self.assertEqual(launcher.main([]), 0)
                open_ui.assert_called_once()
                open_ui.reset_mock()
                self.assertEqual(launcher.main(["--background"]), 0)       # its autostart stays silent
                open_ui.assert_not_called()
            with mock.patch.object(launcher, "running_here", return_value=None):
                self.assertEqual(launcher.main(["--background"]), 0)
                tell.assert_not_called()
                self.assertEqual(launcher.main([]), 2)
                tell.assert_called_once_with("no_access_user")

    def test_h6_the_installer_keeps_the_data_folder_closed_unless_the_operator_chooses_all_users(self):
        iss = (pathlib.Path(launcher.__file__).parent / "installer" / "super-market.iss").read_text(encoding="utf-8")
        self.assertIn('Name: "allusers"', iss)
        self.assertIn("Flags: unchecked", iss[iss.index('Name: "allusers"'):iss.index('Name: "allusers"') + 120])
        self.assertIn('/remove:g "*S-1-5-32-545" "*S-1-5-11"', iss)           # default: Users and Authenticated Users removed

    def test_h5_usb_backup_action_copies_verified_and_remembers_the_place(self):
        import tempfile
        from server import backup
        home = self.folder
        self.store.close()
        with tempfile.TemporaryDirectory() as usb, mock.patch.object(launcher, "HOME", home), \
                mock.patch("builtins.input", return_value=usb):
            (home / "shop-data").mkdir(exist_ok=True)
            self.store = type(self.store)(home / "shop-data", "shop")        # the launcher opens the same data folder
            self.store.close()
            self.assertEqual(launcher.usb_backup(home / "shop-data"), 0)
            copies = list((pathlib.Path(usb) / backup.EXTERNAL_DIR).glob("*/manifest.json"))
            self.assertEqual(len(copies), 1)
            backup.verify_backup(copies[0].parent)
            self.assertEqual(launcher.usb_backup(home / "shop-data"), 0)        # place remembered: no question the second time
        with mock.patch.object(launcher, "HOME", home), mock.patch("builtins.input", return_value=str(home / "nope")):
            conn = __import__("sqlite3").connect(home / "shop-data" / "shop.db")
            conn.execute("DELETE FROM settings WHERE key='backup_folder_2'"); conn.commit(); conn.close()
            self.assertEqual(launcher.usb_backup(home / "shop-data"), 1)        # an unplugged stick: told, nothing changed


if __name__ == "__main__":
    unittest.main()
