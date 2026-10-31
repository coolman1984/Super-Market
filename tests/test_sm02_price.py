"""SM-02 / T-M4: price 0 is a real price; unreadable text is never stored as no price.

The screen asks once before saving zero, and refuses text such as "abc" by name.
The server refuses the same values even if the screen is bypassed. Arabic digits
and the decimal comma stay a client concern: the server stores integer piastres only.
"""

import pathlib
import unittest

from helpers import ShopFixture, key
from server.core import ShopError

ROOT = pathlib.Path(__file__).resolve().parents[1]


class UnreadablePrice(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def _count(self):
        return self.store.scalar("SELECT COUNT(*) FROM products")

    def test_a_present_unreadable_price_does_not_create_the_product(self):
        before = self._count()
        for bad in ("abc", None, 20.5, True, "٢٥", "20,5", "0", "", "  "):
            with self.subTest(bad=bad):
                with self.assertRaises(ShopError) as cm:
                    self.shop.add_product(self.owner, {
                        "name_ar": "س", "name_en": "S", "kind": "grocery", "measure": "unit", "price": bad})
                self.assertEqual(cm.exception.code, "bad_price")
                self.assertEqual(self._count(), before)

    def test_omitting_the_price_still_creates_the_product(self):
        pid = self.shop.add_product(self.owner, {
            "code": "NOP", "name_ar": "بلا سعر", "name_en": "No price yet", "kind": "grocery", "measure": "unit"})
        self.assertIsNone(self.shop.current_price(pid))

    def test_zero_is_stored_only_as_the_integer_zero(self):
        pid = self.shop.add_product(self.owner, {
            "code": "Z", "name_ar": "مجاني", "name_en": "Free", "kind": "grocery", "measure": "unit", "price": 0})
        self.assertEqual(self.shop.current_price(pid), 0)
        self.shop.set_price(self.owner, key(), pid, 1500)
        self.shop.set_price(self.owner, key(), pid, 0)
        self.assertEqual(self.shop.current_price(pid), 0)

    def test_changing_the_price_refuses_text_and_keeps_the_old_price(self):
        pid = self.grocery(price=3500)
        for bad in ("abc", None, 20.5, True, "٢٥", "20,5", "0"):
            with self.subTest(bad=bad):
                with self.assertRaises(ShopError) as cm:
                    self.shop.set_price(self.owner, key(), pid, bad)
                self.assertEqual(cm.exception.code, "bad_price")
        self.assertEqual(self.shop.current_price(pid), 3500)
        self.shop.set_price(self.owner, key(), pid, 2500)
        self.assertEqual(self.shop.current_price(pid), 2500)
        self.shop.set_price(self.owner, key(), pid, 2050)
        self.assertEqual(self.shop.current_price(pid), 2050)

    def test_the_screen_names_the_field_and_asks_the_zero_question(self):
        ar = (ROOT / "web/js/i18n/ar.js").read_text(encoding="utf-8")
        en = (ROOT / "web/js/i18n/en.js").read_text(encoding="utf-8")
        admin = (ROOT / "web/js/views/admin.js").read_text(encoding="utf-8")
        self.assertIn('"cat.zero_price_title": "السعر صفر، متأكد؟"', ar)
        self.assertIn('"err.bad_price": "سعر البيع غير مفهوم. اكتب رقمًا واضحًا."', ar)
        self.assertIn('"err.bad_price":', en)
        self.assertIn("askZeroPrice", admin)
        self.assertIn("err.bad_price", admin)
        self.assertNotIn("parseMoney(f.price.value)", admin)


if __name__ == "__main__":
    unittest.main()
