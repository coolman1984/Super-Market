"""The owner guide's cashier line must follow server.auth.ROLES.

The guide is not a full permission matrix. This check only rejects a sell-only
description and a cashier permission the role row never names.
"""

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.auth import ROLES  # noqa: E402

GUIDE = ROOT / "docs" / "USER_GUIDE.md"

# Plain words the guide uses for each permission the cashier holds.
# A permission with no word here fails until the guide names it.
CASHIER_MARKS = {
    "sell": "يبيع",
    "shift": "وردية",
    "discount": "خصم",
    "refund": "مرتجع",
    "scanner_setup": "ماسح",
}

SELL_ONLY = re.compile(r"(?:يبيع|بيبيع|البيع|للبيع)\s*[،,]?\s*فقط")
NEXT_ROLE = re.compile(r"\s(?:مدير|محاسب|المالك)\s*:")


def staff_roles_row(text):
    rows = [line for line in text.splitlines() if line.startswith("| أدوار الموظفين")]
    if len(rows) != 1:
        raise AssertionError("expected one staff-roles row in USER_GUIDE.md")
    return rows[0]


def cashier_clause(row):
    """The cashier's own words, stopping at the next role label in the same cell."""
    match = re.search(r"(?:الكاشير|كاشير)\s*:?\s*(.*)", row)
    if not match:
        raise AssertionError("the staff-roles row does not describe the cashier")
    rest = match.group(1)
    end = NEXT_ROLE.search(rest)
    return rest[:end.start()] if end else rest


class UserGuideRoles(unittest.TestCase):
    def test_cashier_role_in_user_guide_matches_server_roles(self):
        clause = cashier_clause(staff_roles_row(GUIDE.read_text(encoding="utf-8")))
        self.assertIsNone(SELL_ONLY.search(clause), clause)
        missing = sorted(p for p in ROLES["cashier"] if CASHIER_MARKS.get(p, "") not in clause)
        self.assertEqual(missing, [])
