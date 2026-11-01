"""Version 10 was used twice: PIN sign-in on main, the shortage column on be00d4d.

Opening any of those databases with this program must reach both schemas
without dropping a row and without failing on a column that is already there.
"""

import pathlib
import tempfile
import unittest

from helpers import ShopFixture  # noqa: F401  (path + fast hashing)
from server.db import Store
from server import schema


def columns(store, table):
    return {row[1] for row in store.db.execute('PRAGMA table_info("%s")' % table)}


def remember(store, label):
    store.db.execute("INSERT INTO settings VALUES('keep_me', ?)", (label,))
    store.db.execute(
        "INSERT INTO users(username,display_name,role,pw_hash,pw_salt,pw_iter,created_at) "
        "VALUES('boss','صاحب المحل','owner','h','00',1,'2026-10-30')")


class SchemaCollision(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "data"

    def _open(self, upto=None, keep=True):
        store = Store(self.path, upto=upto)
        if keep:
            self.addCleanup(store.close)
        return store

    def test_a_fresh_database_has_the_pin_and_the_shortage_column(self):
        store = self._open()
        self.assertEqual(store.schema_version(), schema.SCHEMA_VERSION)
        self.assertEqual(schema.SCHEMA_VERSION, 11)
        self.assertTrue({"pin_hash", "pin_salt", "pin_iter"} <= columns(store, "users"))
        self.assertIn("via_pin", columns(store, "sessions"))
        self.assertIn("gap_handled_at", columns(store, "shifts"))
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.schema_version(), 11)

    def test_main_v10_gains_the_shortage_column_and_keeps_its_rows(self):
        old = self._open(upto=10, keep=False)
        self.assertEqual(old.schema_version(), 10)
        self.assertIn("pin_hash", columns(old, "users"))
        self.assertNotIn("gap_handled_at", columns(old, "shifts"))
        remember(old, "from-main")
        old.close()
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.schema_version(), 11)
        self.assertIn("gap_handled_at", columns(store, "shifts"))
        self.assertIn("pin_hash", columns(store, "users"))
        self.assertEqual(store.scalar("SELECT value FROM settings WHERE key='keep_me'"), "from-main")
        self.assertEqual(store.scalar("SELECT display_name FROM users WHERE username='boss'"), "صاحب المحل")

    def test_v9_reaches_v11_with_both_schemas_and_keeps_its_rows(self):
        old = self._open(upto=9, keep=False)
        self.assertEqual(old.schema_version(), 9)
        self.assertNotIn("pin_hash", columns(old, "users"))
        self.assertNotIn("gap_handled_at", columns(old, "shifts"))
        remember(old, "from-v9")
        old.close()
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.schema_version(), 11)
        self.assertTrue({"pin_hash", "pin_salt", "pin_iter"} <= columns(store, "users"))
        self.assertIn("via_pin", columns(store, "sessions"))
        self.assertIn("gap_handled_at", columns(store, "shifts"))
        self.assertEqual(store.scalar("SELECT value FROM settings WHERE key='keep_me'"), "from-v9")
        self.assertEqual(store.scalar("SELECT COUNT(*) FROM users"), 1)

    def test_shortage_v10_from_the_old_branch_opens_and_gains_the_pin(self):
        old = self._open(upto=9, keep=False)
        old.db.execute("ALTER TABLE shifts ADD COLUMN gap_handled_at TEXT")
        old.db.execute("INSERT INTO schema_migrations VALUES(10, ?, ?)",
                       ("shift gaps stay open until explained or marked done", "2026-10-30T22:00:00"))
        remember(old, "from-branch")
        uid = old.scalar("SELECT id FROM users WHERE username='boss'")
        old.db.execute(
            "INSERT INTO shifts(user_id,workstation,opened_at,opening_float,closed_at,counted,expected,"
            "difference,close_note,gap_handled_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (uid, "till", "2026-10-30T18:00:00", 10000, "2026-10-30T22:00:00", 5000, 10000, -5000,
             "نقص في العد", "2026-10-30T22:05:00"))
        self.assertNotIn("pin_hash", columns(old, "users"))
        old.close()
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.schema_version(), 11)
        self.assertTrue({"pin_hash", "pin_salt", "pin_iter"} <= columns(store, "users"))
        self.assertIn("via_pin", columns(store, "sessions"))
        self.assertIn("gap_handled_at", columns(store, "shifts"))
        row = store.one("SELECT difference, close_note, gap_handled_at FROM shifts")
        self.assertEqual((row["difference"], row["close_note"], row["gap_handled_at"]),
                         (-5000, "نقص في العد", "2026-10-30T22:05:00"))
        self.assertIsNone(store.scalar("SELECT pin_hash FROM users WHERE username='boss'"))
        self.assertEqual(store.scalar("SELECT value FROM settings WHERE key='keep_me'"), "from-branch")
        self.assertEqual(store.scalar("SELECT display_name FROM users WHERE username='boss'"), "صاحب المحل")
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.one("SELECT difference, gap_handled_at FROM shifts"),
                         {"difference": -5000, "gap_handled_at": "2026-10-30T22:05:00"})


if __name__ == "__main__":
    unittest.main()
