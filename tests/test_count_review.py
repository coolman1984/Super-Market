"""Count sheet: resume an open count, show every difference, and never post a 10× jump quietly."""

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
        self.assertEqual(saved["saved"], True)
        self.assertTrue(saved["far"])
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
