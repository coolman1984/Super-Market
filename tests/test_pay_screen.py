"""Sale payment screen: an empty card or transfer row is never saved as cash,
and InstaPay is a choice on that screen (ISSUE-003, ISSUE-002)."""

import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node runs the sale payment screen")
class PayScreen(unittest.TestCase):
    def test_empty_card_or_transfer_is_not_saved_as_cash_and_instapay_is_a_choice(self):
        done = subprocess.run([NODE, "tests/ui/pay_empty.js"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('"ok":true', done.stdout)
