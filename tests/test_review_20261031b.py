"""Review of 1170bf2 (31 Oct 2026): food-storage rules must hold on EVERY path, not
only on the thaw button. Each test expects the correct behaviour and failed on 1170bf2."""

import unittest

from helpers import ShopFixture, key
from server.core import ShopError


class FrozenStock(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        self.freezer = self.shop.add_location(self.owner, "FZ", "مجمد", "Freezer", "freezer", 0)
        self.shop.update_settings(self.owner, {"thaw_shelf_life_days": 2, "cutting_loss_tolerance_permille": 100})
        self.m = self.meat()
        self.lot = self.receive(self.m, 5000, 200000, location=self.freezer, expires="2027-03-01")["lines"][0]["lot"]

    def code(self, fn, *a):
        with self.assertRaises(ShopError) as err:
            fn(*a)
        return err.exception.code

    # case 6: a plain move out of the freezer kept the frozen date
    def test_case6_moving_frozen_meat_to_a_fridge_must_go_through_thawing(self):
        self.assertEqual(self.code(self.shop.transfer, self.owner, key(), self.lot, self.freezer, self.prep, 1000), "use_thaw")
        self.assertEqual(self.code(self.shop.transfer, self.owner, key(), self.lot, self.freezer, self.shelf, 1000), "use_thaw")
        self.assertEqual(self.shop.available(self.m), 0)                        # nothing moved
        self.assertEqual(self.store.scalar("SELECT expires FROM lots WHERE id=?", (self.lot,)), "2027-03-01")

    def test_case6_cutting_straight_from_the_freezer_also_needs_thawing(self):
        cut = self.meat("M2")
        self.assertEqual(self.code(self.shop.produce, self.butcher, key(), {"kind": "cutting",
            "inputs": [{"lot_id": self.lot, "location_id": self.freezer, "qty": 1000}],
            "outputs": [{"product_id": cut, "qty": 1000, "location_id": self.fridge, "expires": "2027-03-01"}]}), "use_thaw")

    def test_case6_freezer_to_freezer_stays_allowed(self):
        other = self.shop.add_location(self.owner, "FZ2", "مجمد 2", "Freezer 2", "freezer", 0)
        self.shop.transfer(self.owner, key(), self.lot, self.freezer, other, 1000)

    # case 7: thawing onto an ordinary shelf
    def test_case7_thawing_goes_only_to_a_fridge(self):
        for place in (self.shelf, self.quarantine):
            self.assertEqual(self.code(self.shop.thaw, self.owner, key(), self.lot, self.freezer, place, 1000), "thaw_needs_fridge")
        t = self.shop.thaw(self.owner, key(), self.lot, self.freezer, self.prep, 1000)
        self.assertEqual(t["expires"], "2026-11-01")


if __name__ == "__main__":
    unittest.main()
