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
        saved = self.shop.record_count(self.counter, count, lot, 200)
        self.assertEqual(saved, {"ok": True, "saved": True})
        self.assertEqual(self.shop.available(pid), 20)
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
        self.shop.record_count(self.counter, count, lots["BIG"], 50)  # 10×
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
        saved = self.shop.record_count(self.counter, count, lot, 0)
        self.assertEqual(saved, {"ok": True, "saved": True})
        line = self.shop.count_review(self.manager, count)["lines"][0]
        self.assertEqual((line["system"], line["counted"], line["big"]), (20, 0, True))
        self.assertEqual(self.shop.available(pid), 20)

    def test_differences_sort_by_money_then_percent_when_cost_is_zero(self):
        """180 pieces must outrank 0.25 kg when the pieces are worth more, even though 250 > 180."""
        pieces = self.grocery(code="PC", barcodes=[])
        grams = self.meat(code="KG")
        cheap = self.grocery(code="LO", barcodes=[])
        dear = self.grocery(code="HI", barcodes=[])
        self.receive(pieces, 20, 2000, location=self.fridge, expires=None)                 # 100 piastres a piece
        self.receive(grams, 1000, 400, expires="2026-11-05")         # 0.4 piastres a gram
        self.shop.receive(self.owner, key(), {"lines": [
            {"product_id": cheap, "qty": 100, "line_cost": 0, "line_kind": "gift", "location_id": self.fridge},
            {"product_id": dear, "qty": 4, "line_cost": 0, "line_kind": "gift", "location_id": self.fridge},
        ]})
        lots = {r["code"]: r["id"] for r in self.store.all(
            "SELECT p.code, l.id FROM lots l JOIN products p ON p.id=l.product_id")}
        count = self.shop.open_count(self.counter, self.fridge)["count"]
        self.shop.record_count(self.counter, count, lots["PC"], 200)   # +180, value 18000
        self.shop.record_count(self.counter, count, lots["KG"], 1250)                    # +250 g = 0.25 kg, value 100
        self.shop.record_count(self.counter, count, lots["LO"], 110)                     # +10, 10%
        self.shop.record_count(self.counter, count, lots["HI"], 7)                       # +3, 75%
        diffs = [r["diff"] for r in self.shop.count_review(self.manager, count)["lines"]]
        self.assertEqual(diffs, [180, 250, 3, 10])

    def test_counter_learns_nothing_about_stock_and_cannot_cancel(self):
        """Any typed figure saves the same way. Repeats never 429. Only the approver sees a far line."""
        stock = 137
        pid = self.grocery(code="HID", barcodes=[])
        self.receive(pid, stock, 1000, expires=None)
        lot = self.store.scalar("SELECT id FROM lots")
        count = self.shop.open_count(self.counter, self.shelf)["count"]
        hidden = ("far", "near", "system", "expected", "diff", "wide", "big", "qty", "cost", "balance")
        for guess in (0, 1, 9, 10, 13, 14, 100, stock, stock * 10, stock * 10 + 1, 5000, 20, 200, 8, 21, 55):
            answer = self.shop.record_count(self.counter, count, lot, guess)
            self.assertEqual(answer, {"ok": True, "saved": True})
            blob = json.dumps(answer)
            for word in hidden:
                self.assertNotIn(word, blob)
            self.assertNotIn(str(stock), blob)
        sheet = self.shop.count_sheet(self.counter, count)
        allowed = {"lot_id", "lot_no", "expires", "name_ar", "name_en", "measure", "code", "counted"}
        self.assertTrue(set(sheet["lots"][0]) <= allowed)
        self.assertEqual(sheet["lots"][0]["counted"], 55)
        with self.assertRaises(Forbidden) as err:
            self.shop.count_review(self.counter, count)
        self.assertEqual(err.exception.status, 403)
        with self.assertRaises(Forbidden) as err:
            self.shop.cancel_count(self.counter, count, "the counter must not close it")
        self.assertEqual(err.exception.status, 403)
        self.assertEqual(self.store.scalar("SELECT status FROM counts WHERE id=?", (count,)), "open")
        self.shop.record_count(self.counter, count, lot, stock * 10)
        review = self.shop.count_review(self.manager, count)
        self.assertTrue(review["big"])
        self.assertTrue(review["lines"][0]["big"])
        self.assertEqual((review["lines"][0]["system"], review["lines"][0]["counted"]), (stock, stock * 10))
