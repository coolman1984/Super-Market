"""A closed shift with a gap is visible to the owner: who, how many piastres, and why.

The day report keeps the difference as an integer number of piastres. The today
screen names the cashier and the amount, and does not tell the owner to open a
shift when that gap is the news. The shifts report shows the name and the note.
"""

import pathlib
import shutil
import subprocess
import unittest

from helpers import ShopFixture, key

NODE = shutil.which("node")
ROOT = pathlib.Path(__file__).resolve().parents[1]


class ShiftGap(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_day_report_names_the_cashier_and_keeps_the_gap_in_piastres(self):
        self.open_shift(self.cash1, 10000)
        first = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.cash1["id"],))
        closed = self.shop.close_shift(self.cash1, key(), 5000, "نقص في العد")
        self.assertEqual(closed["difference"], -5000)
        self.open_shift(self.cash2, 2000)
        second = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.cash2["id"],))
        self.shop.close_shift(self.cash2, key(), 1500, "باقي ناقص")
        self.open_shift(self.manager, 1000)
        third = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.manager["id"],))
        self.shop.close_shift(self.manager, key(), 2500, "زيادة في الدرج")
        self.open_shift(self.owner, 0)
        open_id = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.owner["id"],))

        rows = {s["id"]: s for s in self.shop.day_report(self.owner)["shifts"]}
        self.assertEqual([s["id"] for s in self.shop.day_report(self.owner)["shifts"]], [first, second, third, open_id])
        self.assertEqual(rows[first]["cashier"], "Cashier 1")
        self.assertEqual(rows[first]["close_note"], "نقص في العد")
        self.assertEqual(rows[first]["difference"], -5000)
        self.assertIsInstance(rows[first]["difference"], int)
        self.assertEqual((rows[first]["expected"], rows[first]["counted"]), (10000, 5000))
        self.assertTrue(rows[first]["closed_at"])
        self.assertEqual(rows[second]["cashier"], "Cashier 2")
        self.assertEqual((rows[second]["difference"], rows[second]["close_note"]), (-500, "باقي ناقص"))
        self.assertEqual((rows[third]["difference"], rows[third]["cashier"], rows[third]["close_note"]), (1500, "Manager", "زيادة في الدرج"))
        self.assertEqual(rows[open_id]["cashier"], "Owner")
        self.assertIsNone(rows[open_id]["difference"])
        self.assertIsNone(rows[open_id]["close_note"])
        self.assertIsNone(rows[open_id]["closed_at"])

    @unittest.skipUnless(NODE, "node renders the today and shifts screens")
    def test_owner_today_names_the_shortage_and_the_report_shows_the_reason(self):
        proc = subprocess.run([NODE, str(ROOT / "tests" / "ui" / "shift_view.js")], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stdout + "\n" + proc.stderr)
        self.assertIn("shift view", proc.stdout)


if __name__ == "__main__":
    unittest.main()
