"""Aggressive test of 31 Oct 2026 (owner) + the second model's report: every server-side fix
has a test here that failed before the fix. UI fixes are covered by tests/ui/review_d.js."""

import datetime as dt
import http.client
import pathlib
import tempfile
import threading
import unittest

import helpers  # noqa: F401
from helpers import ShopFixture, key
from server import auth, core
from server.app import serve
from server.core import ShopError


class Passwords(unittest.TestCase):
    def test_easy_passwords_are_refused_M3(self):
        for p in ("12345678", "87654321", "aaaaaaaa", "11111111", "12121212", "abcdefgh", "password", "qwerty123", "abcabcab"):
            with self.subTest(p=p), self.assertRaises(ShopError):
                auth.check_password_rules(p)

    def test_ordinary_passwords_still_work(self):
        for p in ("dwr45cnekr", "Mo7amed2026", "test-pass-123", "سوبر-ماركت-٢٠٢٦"):
            auth.check_password_rules(p)


class Shop(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_an_item_without_a_number_gets_the_next_free_one_M6(self):
        a = self.shop.add_product(self.owner, {"name_ar": "سكر", "name_en": "Sugar", "kind": "grocery", "measure": "unit", "price": 3000})
        b = self.shop.add_product(self.owner, {"code": "", "name_ar": "شاي", "name_en": "Tea", "kind": "grocery", "measure": "unit", "price": 2000})
        codes = [self.shop.product(x)["code"] for x in (a, b)]
        self.assertEqual(codes, ["1001", "1002"])

    def test_an_item_number_with_spaces_or_emoji_is_refused_M6(self):
        for code in ("B K 1", "😀", "a b"):
            with self.subTest(code=code), self.assertRaises(ShopError):
                self.shop.add_product(self.owner, {"code": code, "name_ar": "x", "name_en": "x", "kind": "grocery", "measure": "unit"})

    def test_the_shop_name_cannot_be_saved_empty_M5(self):
        with self.assertRaises(ShopError) as cm:
            self.shop.update_settings(self.owner, {"shop_name": "   "})
        self.assertEqual(cm.exception.detail.get("key"), "shop_name", "the till can point at the wrong box")

    def test_a_cart_asking_more_than_can_be_sold_is_reported_at_once_M8(self):
        p = self.grocery()
        self.receive(p, 3, 500, expires=None)
        q = self.shop.quote(self.cash1, {"lines": [{"product_id": p, "qty": 5}]})
        self.assertEqual(q["short"], [{"product": p, "available": 3, "asked": 5}])
        self.assertEqual(self.shop.quote(self.cash1, {"lines": [{"product_id": p, "qty": 2}]})["short"], [])

    def test_absurd_numbers_are_refused_L6(self):
        with self.assertRaises(ShopError):
            core.integer(10 ** 12)
        self.assertEqual(core.integer(10 ** 11), 10 ** 11)

    def test_session_expiry_compares_moments_not_text_L13(self):
        cairo_summer, cairo_winter = dt.timezone(dt.timedelta(hours=3)), dt.timezone(dt.timedelta(hours=2))
        core.set_clock(dt.datetime(2026, 10, 30, 0, 30, tzinfo=cairo_winter))      # 22:30 UTC
        # 01:00+03:00 is 22:00 UTC: already past, though the text "2026-10-30T01:00" sorts after "2026-10-30T00:30"
        self.assertTrue(auth._expired(dt.datetime(2026, 10, 30, 1, 0, tzinfo=cairo_summer).isoformat()))
        self.assertFalse(auth._expired(dt.datetime(2026, 10, 30, 1, 0, tzinfo=cairo_winter).isoformat()))
        self.assertTrue(auth._expired("not a date"))


class Http(unittest.TestCase):
    def test_head_and_options_get_a_short_json_answer_L11(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        httpd = serve(pathlib.Path(tmp.name) / "data", "shop", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.app.store.close)
        for method in ("HEAD", "OPTIONS", "PUT", "DELETE"):
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
            conn.request(method, "/api/state", headers={"X-SM": "1"})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
            with self.subTest(method=method):
                self.assertEqual(resp.status, 405)
                self.assertIn("GET", resp.getheader("Allow"))
                self.assertNotIn(b"<html", body.lower())


if __name__ == "__main__":
    unittest.main()
