"""The owner guide's cashier lines must follow the server, in shop Arabic.

Reads ROLES and the refund/discount defaults. Fails if the guide calls the
cashier sell-only («فقط» or «بس»), drops a till permission it is supposed to
name, or states a limit the code does not use.
"""

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.auth import ROLES  # noqa: E402
from server.services import DEFAULT_SETTINGS  # noqa: E402

GUIDE = ROOT / "docs" / "USER_GUIDE.md"

# Words the short cashier line uses. scanner_setup stays in ROLES, but the
# line must not say the hard phrase «ضبط الماسح»; the scanner has its own lesson.
CASHIER_MARKS = {
    "sell": "يبيع",
    "shift": "درج",
    "discount": "خصم",
    "refund": "رجّع",
}
UNNAMED_OK = {"scanner_setup"}

PIASTRES_PER_POUND = 100
PERMILLE_PER_PERCENT = 10

SELL_ONLY = re.compile(r"(?:يبيع|بيبيع|البيع|للبيع)\s*[،,]?\s*(?:فقط|بس)(?![\u0621-\u064A])")
FALSE_CLAIM = re.compile(r"لا\s+يحتاج\s+موافقة|لا\s+يوافق|مش\s+لازم|من\s+غير\s+موافقة|بدون\s+موافقة")
NEXT_ROLE = re.compile(r"\s(?:مدير|محاسب|المالك)\s*:")
HARD_SCANNER = "ضبط الماسح"


def ar_digits(number):
    return str(number).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


def numbers(text):
    western = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    return [int(n) for n in re.findall(r"\d+", western)]


def guide_limits():
    """(percent, pounds, days) from the same defaults the till uses."""
    permille = DEFAULT_SETTINGS["cashier_discount_permille"]
    over = DEFAULT_SETTINGS["refund_approval_over"]
    days = DEFAULT_SETTINGS["refund_days_without_approval"]
    if permille % PERMILLE_PER_PERCENT or over % PIASTRES_PER_POUND:
        raise AssertionError("defaults are not whole percent and pounds")
    return permille // PERMILLE_PER_PERCENT, over // PIASTRES_PER_POUND, days


def staff_roles_row(text):
    rows = [line for line in text.splitlines() if line.startswith("| أدوار الموظفين")]
    if len(rows) != 1:
        raise AssertionError("expected one staff-roles row in USER_GUIDE.md")
    return rows[0]


def refund_row(text):
    rows = [line for line in text.splitlines() if line.startswith("| رد")]
    if len(rows) != 1:
        raise AssertionError("expected one refund row in USER_GUIDE.md")
    return rows[0]


def cashier_clause(row):
    """The cashier's own words, stopping at the next role label in the same cell."""
    match = re.search(r"(?:الكاشير|كاشير)\s*:?\s*(.*)", row)
    if not match:
        raise AssertionError("the staff-roles row does not describe the cashier")
    rest = match.group(1)
    end = NEXT_ROLE.search(rest)
    return rest[:end.start()] if end else rest


def clause_problems(clause, limits):
    """Why this cashier text is sell-only or states a rule the server does not."""
    percent, pounds, days = limits
    problems = []
    if SELL_ONLY.search(clause):
        problems.append("sell-only")
    if FALSE_CLAIM.search(clause):
        problems.append("false-approval")
    if HARD_SCANNER in clause:
        problems.append("hard-scanner")
    if not re.search(r"لحد\s+" + ar_digits(percent), clause):
        problems.append("discount")
    if not re.search(r"أكتر من\s+" + ar_digits(pounds), clause):
        problems.append("amount")
    if not re.search(r"أقدم من\s+" + ar_digits(days), clause):
        problems.append("age")
    if "تاني" not in clause:
        problems.append("other-cashier")
    if "غيّر رأيه" not in clause:
        problems.append("change-of-mind")
    if "لازم المدير يوافق" not in clause:
        problems.append("manager")
    if sorted(numbers(clause)) != sorted((percent, pounds, days)):
        problems.append("numbers")
    for perm, mark in CASHIER_MARKS.items():
        if mark not in clause:
            problems.append(perm)
    return problems


class UserGuideRoles(unittest.TestCase):
    def setUp(self):
        self.text = GUIDE.read_text(encoding="utf-8")
        self.limits = guide_limits()
        self.clause = cashier_clause(staff_roles_row(self.text))

    def test_cashier_role_in_user_guide_matches_server_roles(self):
        self.assertEqual(clause_problems(self.clause, self.limits), [])
        unnamed = set(ROLES["cashier"]) - set(CASHIER_MARKS) - UNNAMED_OK
        self.assertEqual(unnamed, set())

    def test_sell_only_and_false_claims_fail(self):
        limits = self.limits
        clause = self.clause
        self.assertEqual(clause_problems(clause, limits), [])
        self.assertIn("sell-only", clause_problems(clause.replace("يبيع،", "يبيع بس،", 1), limits))
        self.assertIn("sell-only", clause_problems(clause.replace("يبيع،", "يبيع فقط،", 1), limits))
        self.assertIn("false-approval", clause_problems("المرتجع الكبير أو القديم لا يحتاج موافقة. " + clause, limits))
        self.assertIn("false-approval", clause_problems(clause.replace("لازم المدير يوافق", "من غير موافقة", 1), limits))
        self.assertIn("amount", clause_problems(clause.replace("٢٠٠", "٣٠٠", 1), limits))
        self.assertIn("discount", clause_problems(clause.replace("لحد ٥٪", "لحد ١٠٪", 1), limits))
        self.assertIn("age", clause_problems(clause.replace("أقدم من ٧", "أقدم من ٣", 1), limits))
        self.assertIn("other-cashier", clause_problems(clause.replace("كاشير تاني", "نفس الكاشير", 1), limits))
        self.assertIn("change-of-mind", clause_problems(clause.replace("غيّر رأيه", "عيب في الصنف", 1), limits))
        self.assertIn("hard-scanner", clause_problems(clause.replace("ويفتح الدرج", "ويضبط الماسح ويفتح الدرج", 1), limits))
        self.assertIsNotNone(SELL_ONLY.search("يبيع بس"))
        self.assertIsNotNone(SELL_ONLY.search("الكاشير يبيع فقط"))

    def test_refund_row_states_the_same_approval_rules(self):
        percent, pounds, days = self.limits
        row = refund_row(self.text)
        self.assertIsNone(SELL_ONLY.search(row), row)
        self.assertIsNone(FALSE_CLAIM.search(row), row)
        self.assertRegex(row, r"أكتر من\s+" + ar_digits(pounds))
        self.assertRegex(row, r"أقدم من\s+" + ar_digits(days))
        self.assertIn("تاني", row)
        self.assertIn("غيّر رأيه", row)
        self.assertIn("سبب آخر", row)
        self.assertIn("موافقة", row)
        self.assertIn("كلمة مروره", row)
        stated = set(numbers(row))
        self.assertIn(pounds, stated)
        self.assertIn(days, stated)
        self.assertLessEqual(stated, {percent, pounds, days})
