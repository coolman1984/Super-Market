"""Count sheet: resume an open count, show every difference, and never post a 10× jump quietly."""

import json
import unittest

from helpers import ShopFixture, key
from server.core import Forbidden, ShopError
from server.services import Shop


class CountReview(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_far_means_ten_times_and_wide_means_more_than_half(self):
        far, wide = Shop.count_is_far, Shop.count_is_wide
        self.assertTrue(far(20, 200))          # extra zero
        self.assertTrue(far(200, 20))          # missing zero
        self.assertFalse(far(20, 199))
        self.assertFalse(far(20, 21))
        self.assertFalse(far(0, 0))
        self.assertFalse(far(0, 9))
        self.assertTrue(far(0, 10))
        self.assertTrue(far(10, 0))
        self.assertTrue(wide(20, 31))          # more than half
        self.assertFalse(wide(20, 30))         # exactly half is not highlighted
        self.assertTrue(wide(0, 1))

    def test_start_again_resumes_the_open_sheet_and_keeps_the_lines(self):
        pid = self.grocery()
        self.receive(pid, 20, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        first = self.shop.open_count(self.counter, self.shelf)
        self.assertFalse(first["resumed"])
        self.shop.record_count(self.counter, first["count"], lot, 18)
        again = self.shop.open_count(self.counter, self.shelf)
        self.assertEqual(again, {"count": first["count"], "resumed": True})
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM counts"), 1)
        sheet = self.shop.count_sheet(self.counter, again["count"])
        self.assertEqual(sheet["lots"][0]["counted"], 18)
        self.assertNotIn("qty", sheet["lots"][0])
        self.assertNotIn("system", sheet["lots"][0])

    def test_a_tenfold_count_is_not_saved_or_approved_until_confirmed(self):
        pid = self.grocery(code="ST005", barcodes=[])
        self.receive(pid, 20, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        held = self.shop.record_count(self.counter, count, lot, 200)
        self.assertEqual(held, {"ok": False, "saved": False, "far": True})
        self.assertEqual(set(held), {"ok", "saved", "far"})
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM count_lines"), 0)
        self.assertEqual(self.shop.available(pid), 20)
        saved = self.shop.record_count(self.counter, count, lot, 200, confirm_far=True)
        self.assertEqual(saved, {"ok": True, "saved": True})
        self.assertNotIn("far", saved)
        with self.assertRaises(Forbidden):
            self.shop.count_review(self.counter, count)
        review = self.shop.count_review(self.manager, count)
        self.assertEqual(review["big"], True)
        line = review["lines"][0]
        self.assertEqual(line["name_ar"], "عصير")
        self.assertEqual((line["system"], line["counted"], line["diff"]), (20, 200, 180))
        self.assertTrue(line["big"])
        self.assertTrue(line["wide"])
        with self.assertRaises(ShopError) as err:
            self.shop.approve_count(self.manager, key(), count)
        self.assertEqual(err.exception.code, "count_jump_unconfirmed")
        self.assertEqual(self.shop.available(pid), 20)
        self.assertEqual(self.store.scalar("SELECT status FROM counts WHERE id=?", (count,)), "open")
        posted = self.shop.approve_count(self.manager, key(), count, confirm_jump=True)
        self.assertEqual(posted["differences"][0]["diff"], 180)
        self.assertEqual(self.shop.available(pid), 200)

    def test_review_lists_the_largest_difference_first_and_a_small_one_posts_quietly(self):
        big = self.grocery(code="BIG", barcodes=[])
        small = self.grocery(code="SML", barcodes=[])
        self.receive(big, 5, 500, expires=None)
        self.receive(small, 20, 500, expires=None)
        lots = {r["code"]: r["id"] for r in self.store.all(
            "SELECT p.code, l.id FROM lots l JOIN products p ON p.id=l.product_id")}
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        self.shop.record_count(self.counter, count, lots["SML"], 22)          # +2, not wide
        self.shop.record_count(self.counter, count, lots["BIG"], 50, confirm_far=True)  # 10×
        lines = self.shop.count_review(self.manager, count)["lines"]
        self.assertEqual([r["name_en"] for r in lines], ["Juice", "Juice"])
        self.assertEqual([r["diff"] for r in lines], [45, 2])
        self.assertTrue(lines[0]["big"] and lines[0]["wide"])
        self.assertFalse(lines[1]["big"] or lines[1]["wide"])
        quiet = self.shop.open_count(self.counter, self.fridge)["count"]
        self.receive(small, 20, 400, self.fridge, None)
        lot = self.store.scalar("SELECT id FROM lots WHERE product_id=? ORDER BY id DESC", (small,))
        self.shop.record_count(self.counter, quiet, lot, 21)
        self.assertFalse(self.shop.count_review(self.manager, quiet)["big"])
        self.shop.approve_count(self.manager, key(), quiet)
        self.assertEqual(self.shop._balance(lot, self.fridge)[0], 21)

    def test_counting_zero_when_ten_or_more_are_on_the_shelf_is_a_jump(self):
        pid = self.grocery(code="Z", barcodes=[])
        self.receive(pid, 20, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        held = self.shop.record_count(self.counter, count, lot, 0)
        self.assertTrue(held["far"] and not held["saved"])
        self.shop.record_count(self.counter, count, lot, 0, confirm_far=True)
        line = self.shop.count_review(self.manager, count)["lines"][0]
        self.assertEqual((line["system"], line["counted"], line["big"]), (20, 0, True))
        self.assertEqual(self.shop.available(pid), 20)

    def test_differences_sort_by_money_then_percent_when_cost_is_zero(self):
        """180 pieces must outrank 0.25 kg when the pieces are worth more, even though 250 > 180."""
        pieces = self.grocery(code="PC", barcodes=[])
        grams = self.meat(code="KG")
        cheap = self.grocery(code="LO", barcodes=[])
        dear = self.grocery(code="HI", barcodes=[])
        self.receive(pieces, 20, 2000, expires=None)                 # 100 piastres a piece
        self.receive(grams, 1000, 400, expires="2026-11-05")         # 0.4 piastres a gram
        self.shop.receive(self.owner, key(), {"lines": [
            {"product_id": cheap, "qty": 100, "line_cost": 0, "line_kind": "gift", "location_id": self.shelf},
            {"product_id": dear, "qty": 4, "line_cost": 0, "line_kind": "gift", "location_id": self.shelf},
        ]})
        lots = {r["code"]: r["id"] for r in self.store.all(
            "SELECT p.code, l.id FROM lots l JOIN products p ON p.id=l.product_id")}
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        self.shop.record_count(self.counter, count, lots["PC"], 200, confirm_far=True)   # +180, value 18000
        self.shop.record_count(self.counter, count, lots["KG"], 1250)                    # +250 g = 0.25 kg, value 100
        self.shop.record_count(self.counter, count, lots["LO"], 110)                     # +10, 10%
        self.shop.record_count(self.counter, count, lots["HI"], 7)                       # +3, 75%
        diffs = [r["diff"] for r in self.shop.count_review(self.manager, count)["lines"]]
        self.assertEqual(diffs, [180, 250, 3, 10])

    def test_counter_cannot_binary_search_the_stock(self):
        """The old far/near bit recovered the exact balance in about 19 probes. It must not."""
        stock = 137
        pid = self.grocery(code="HID", barcodes=[])
        self.receive(pid, stock, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        inside = []
        far_answers = 0
        limited = 0
        lo, hi = 1, 200000
        for _ in range(40):
            if lo >= hi:
                break
            mid = (lo + hi) // 2
            try:
                answer = self.shop.record_count(self.counter, count, lot, mid)
            except ShopError as exc:
                self.assertEqual(exc.code, "count_recheck")
                self.assertEqual(exc.detail, {})
                limited += 1
                break
            blob = json.dumps(answer)
            self.assertNotIn("near", answer)
            self.assertNotIn("system", answer)
            self.assertNotIn("expected", answer)
            self.assertNotIn(str(stock), blob)
            if answer.get("far") is True:
                far_answers += 1
                hi = mid
            else:
                self.assertNotIn("far", answer)
                inside.append(mid)
                lo = mid + 1
        self.assertLessEqual(far_answers + len(inside), Shop.COUNT_RECHECK_MAX)
        self.assertGreaterEqual(limited, 1)
        self.assertNotIn(stock, inside)
        leaked = 0
        for guess in (stock, stock * 10, max(stock // 10, 1), 2, 8, 21, 55, 144, 377, 987, 1597, 4096):
            try:
                answer = self.shop.record_count(self.counter, count, lot, guess)
            except ShopError as exc:
                self.assertEqual(exc.detail, {})
                self.assertNotIn(str(stock), json.dumps(exc.detail))
                continue
            if "far" in answer or "near" in answer or "system" in answer or str(stock) in json.dumps(answer):
                leaked += 1
        self.assertEqual(leaked, 0)
