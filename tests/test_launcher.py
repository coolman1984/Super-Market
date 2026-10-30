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

    def test_busy_port_is_detected(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            self.assertIs(launcher.running_here(port, "shop"), False)

    def test_unwritable_folder_refused(self):
        with mock.patch.object(pathlib.Path, "write_text", side_effect=PermissionError):
            self.assertFalse(launcher.writable(self.folder / "x"))


if __name__ == "__main__":
    unittest.main()
