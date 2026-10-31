"""Business day (gap table row 15). A shift belongs to the business day it was opened
on, so a sale at 01:30 in an evening shift is that evening's; without a shift the
shop's day-start hour decides (default 4 a.m., as in common POS systems); a closed
day takes nothing more; the reports and the day close follow the business day."""

import datetime as dt
import unittest

from helpers import CAIRO, ShopFixture, key
from server import core
from server.core import ShopError


def at(day, hour, minute=0):
    return dt.datetime(2026, 10, day, hour, minute, tzinfo=CAIRO)


class BusinessDay(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        self.p = self.grocery(price=1000)
        self.receive(self.p, 50, 5000, expires=None)

    def sale(self, actor):
        cart = {"lines": [{"product_id": self.p, "qty": 1}]}
        return self.shop.sell(actor, key(), dict(cart, quote_hash=self.shop.quote(actor, cart)["quote_hash"]))

    def test_a_sale_after_midnight_in_an_evening_shift_belongs_to_that_evening(self):
        core.set_clock(at(30, 18))
        self.assertEqual(self.open_shift(self.cash1, 0)["business_day"], "2026-10-30")
        self.sale(self.cash1)
        core.set_clock(at(31, 1, 30))
        late = self.sale(self.cash1)
        self.assertEqual(self.store.scalar("SELECT business_day FROM ops WHERE id=?", (late["op"],)), "2026-10-30")
        self.assertEqual(self.shop.day_report(self.owner, "2026-10-30")["sales_count"], 2)
        self.assertEqual(self.shop.day_report(self.owner, "2026-10-31")["sales_count"], 0)
        self.assertEqual(self.shop.period_report(self.owner, "2026-10-30", "2026-10-31")["series"][0]["sales"], 2)

    def test_without_a_shift_the_day_start_hour_decides_and_the_owner_can_change_it(self):
        core.set_clock(at(31, 3, 59))
        self.assertEqual(self.store.business_day_for(None), "2026-10-30")       # before 4 a.m.: still yesterday
        core.set_clock(at(31, 4, 0))
        self.assertEqual(self.store.business_day_for(None), "2026-10-31")
        self.shop.update_settings(self.owner, {"day_start_hour": 0})            # a shop that counts calendar days
        core.set_clock(at(31, 1, 0))
        self.assertEqual(self.store.business_day_for(None), "2026-10-31")
        with self.assertRaises(ShopError):
            self.shop.update_settings(self.owner, {"day_start_hour": 12})

    def test_closing_the_evening_waits_for_the_night_shift_and_counts_its_late_sales(self):
        core.set_clock(at(30, 18))
        self.open_shift(self.cash1, 0)
        core.set_clock(at(31, 1, 30))
        self.sale(self.cash1)
        with self.assertRaises(ShopError) as err:
            self.shop.close_day(self.owner, key(), "2026-10-30")
        self.assertEqual(err.exception.code, "shift_open_on_day")               # the evening shift is still running
        shift = self.store.scalar("SELECT id FROM shifts")
        self.shop.close_shift(self.cash1, key(), self.shop.shift_expected(shift))
        closed = self.shop.close_day(self.owner, key(), "2026-10-30")
        self.assertEqual(closed["net_sales"], 1000)
        # the next posting at 02:00 belongs to the new day, never to the closed one
        self.open_shift(self.cash2, 0)
        self.assertEqual(self.store.scalar("SELECT business_day FROM shifts WHERE user_id=?", (self.cash2["id"],)), "2026-10-31")

    def test_a_clock_set_back_before_a_closed_day_is_refused(self):
        core.set_clock(at(30, 22))
        self.shop.close_day(self.owner, key(), "2026-10-30")
        core.set_clock(at(29, 12))
        with self.assertRaises(ShopError) as err:
            self.receive(self.p, 1, 100, expires=None)
        self.assertEqual(err.exception.code, "day_closed")

    def test_shift_close_report_shows_the_business_day(self):
        core.set_clock(at(30, 20))
        self.open_shift(self.cash1, 0)
        core.set_clock(at(31, 2))
        shift = self.store.scalar("SELECT id FROM shifts")
        self.shop.close_shift(self.cash1, key(), 0)
        rep = self.shop.day_report(self.owner, "2026-10-30")
        self.assertEqual([s["id"] for s in rep["shifts"]], [shift])
        self.assertEqual(rep["shifts"][0]["business_day"], "2026-10-30")


if __name__ == "__main__":
    unittest.main()
