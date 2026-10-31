"""Scales (owner, 31 Oct 2026: support the scales common in Egypt).
Label-printing scales: the layout is found from ONE real sticker. Scales cabled to the till:
the weight text of the common protocols is read as integer grams, never as a float."""

import os
import time
import unittest

import helpers  # noqa: F401
from helpers import ShopFixture
from server import barcode as bc
from server import scale
from server.scanner import ScannerHub


def ean(body12):
    return body12 + bc.gs1_check_digit(body12)


class Weight(unittest.TestCase):
    def test_the_common_lines_of_scales(self):
        cases = {
            "ST,GS,+  1.250kg": (1250, True),          # CAS-style and most Chinese scales
            "ST,NT,+001.250kg": (1250, True),
            "US,GS,+  0.785kg": (785, False),          # still moving
            "  2.5 kg": (2500, True),
            "1250 g": (1250, True),
            "\x02001.250\r": (1250, True),            # Toledo 8217 style answer to "W"
            "\x02 0.500?": (500, False),              # "?" = moving
            "W:  0.300 kg": (300, True),
            "1,750 kg": (1750, True),                  # decimal comma
            "١٫٢٥٠ kg": (1250, True),                  # Arabic digits
            "ST,GS,-  0.020kg": (-20, True),
        }
        for line, (grams, stable) in cases.items():
            with self.subTest(line=line):
                self.assertEqual(scale.parse_weight(line), {"grams": grams, "stable": stable})

    def test_nothing_usable_is_refused_not_guessed(self):
        for line in ("", "OL", "ST,OL,+------kg", "ERR 03", "2.5 lb", "hello", "1.23456 kg"):
            with self.subTest(line=line):
                self.assertIsNone(scale.parse_weight(line))

    def test_no_float_rounding(self):
        # 0.1 + 0.2 style errors cannot happen: digits are joined, not multiplied as floats
        self.assertEqual(scale.parse_weight("0.300kg")["grams"], 300)
        self.assertEqual(scale.parse_weight("12.345kg")["grams"], 12345)


class LabelLayout(unittest.TestCase):
    def test_one_real_sticker_and_the_weight_on_it_find_the_layout(self):
        code = ean("21" + "00123" + "01250")           # 2P IIIII WWWWW C, 1.250 kg, item 123
        found, err = scale.detect_label_layout(code, "1.250")
        self.assertIsNone(err)
        self.assertEqual(found[0]["layout"], "w5")
        self.assertEqual(found[0]["item_code"], "00123")
        t = bc.check_template(found[0])                 # what is saved is a valid normal template
        self.assertEqual(bc.decode_label(code, t), {"item_code": "00123", "net_g": 1250})

    def test_price_stickers_and_other_item_lengths(self):
        code = ean("22" + "0042" + "005250")           # 2P IIII PPPPPP C, 52.50 EGP
        found, _ = scale.detect_label_layout(code, "52.50", item_code="42")
        self.assertEqual([f["layout"] for f in found], ["p4"])
        self.assertEqual(found[0]["value_kind"], "price")

    def test_the_item_number_settles_an_ambiguous_sticker(self):
        code = ean("21" + "00012" + "00500")
        both, _ = scale.detect_label_layout(code)
        self.assertGreater(len(both), 1, "without the printed values several layouts fit")
        one, _ = scale.detect_label_layout(code, "0.500", item_code="12")
        self.assertEqual([f["layout"] for f in one], ["w5"])

    def test_a_damaged_or_foreign_code_is_explained(self):
        good = ean("21" + "00123" + "01250")
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        self.assertEqual(scale.detect_label_layout(bad, "1.250")[1], "bad_check_digit")
        self.assertEqual(scale.detect_label_layout("6221234567890", "1")[1], "not_scale_label")
        self.assertEqual(scale.detect_label_layout(good, "9.999")[1], "label_not_understood")

    def test_one_tap_layouts_are_valid_templates(self):
        for lay in scale.LAYOUTS:
            with self.subTest(layout=lay["id"]):
                bc.check_template(scale.template_for(lay["id"], "21"))
        self.assertIsNone(scale.template_for("w5", "61"), "a scale prefix starts with 2")


class FakePort:
    def __init__(self, replies):
        self.replies, self.asked, self.out = list(replies), [], []

    def write(self, data):
        self.asked.append(data)
        if self.replies:
            self.out.append(self.replies.pop(0))

    def read(self, timeout=0.05):
        time.sleep(0.01)
        return self.out.pop(0) if self.out else b""

    def close(self):
        pass


class Reader(unittest.TestCase):
    def wait(self, fn, seconds=3):
        end = time.time() + seconds
        while time.time() < end:
            if fn():
                return True
            time.sleep(0.02)
        return False

    def test_a_request_style_scale_is_asked_and_answers(self):
        port = FakePort([b"\x02001.250\r"] * 50)
        r = scale.ScaleReader({"id": "s1", "type": "serial", "poll": "W"}, lambda cfg: port)
        r.thread.start()
        self.addCleanup(r.stop.set)
        self.assertTrue(self.wait(lambda: r.reading()["grams"] == 1250))
        self.assertEqual(port.asked[0], b"W")

    @unittest.skipUnless(hasattr(os, "openpty"), "a virtual serial port (pty) exists on Linux/macOS only; the Windows COM path is in docs/FIELD_TEST.md")
    def test_a_continuous_scale_on_a_real_virtual_port(self):
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        hub = ScannerHub()
        self.addCleanup(hub.stop)
        hub.configure([{"id": "scale1", "type": "serial", "port": os.ttyname(slave), "baud": 9600, "role": "scale",
                        "poll": "none", "workstation": "till-1"}])
        self.assertTrue(self.wait(lambda: hub.devices["scale1"].status == "connected"))
        for _ in range(5):
            os.write(master, b"ST,GS,+  0.750kg\r\n")
            time.sleep(0.05)
        self.assertTrue(self.wait(lambda: (hub.weight("till-1") or {}).get("grams") == 750))
        self.assertTrue(hub.weight("till-1")["stable"])
        self.assertIsNone(hub.weight("till-2"), "another till has no scale")
        os.write(master, b"US,GS,+  0.800kg\r\n")
        self.assertTrue(self.wait(lambda: hub.weight("till-1")["stable"] is False))

    def test_overload_or_error_drops_the_last_good_weight_at_once(self):
        # review PR #18: a stable 1.250 kg followed by an overload must not stay offered for two seconds
        r = scale.ScaleReader({"id": "s1", "type": "serial"}, lambda cfg: None)
        r.status = "connected"
        r.feed([b"ST,GS,+ 1.250kg"])
        self.assertEqual(r.reading()["grams"], 1250)
        r.feed([b"OL"])
        self.assertIsNone(r.reading()["grams"])
        r.feed([b"ST,GS,+ 0.500kg", b"ST,GS,+------kg"])
        self.assertIsNone(r.reading()["grams"])
        r.feed([b"ST,GS,+ 0.500kg", b"garbage line"])
        self.assertEqual(r.reading()["grams"], 500, "noise that is not an error keeps the weight")

    def test_an_old_reading_is_not_offered(self):
        r = scale.ScaleReader({"id": "s1", "type": "serial"}, lambda cfg: None)
        r.latest = {"grams": 900, "stable": True, "at": time.monotonic() - 10}
        self.assertIsNone(r.reading()["grams"])


class Api(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()
        from server.app import App
        self.app = App(self.folder / "app-data", "shop")
        self.addCleanup(self.app.store.close)
        self.addCleanup(self.app.hub.stop)
        a = self.app.auth
        uid = self.app.store.tx(lambda: a.create_user("boss", "Boss", "owner", helpers.PASSWORD))
        self.boss = dict(a.public(self.app.store.one("SELECT * FROM users WHERE id=?", (uid,))), workstation="till-1")

    def test_detect_needs_settings_and_saves_nothing(self):
        code = ean("21" + "00123" + "01250")
        r = self.app.detect_label(self.boss, {}, {"code": code, "printed": "1.250"})
        self.assertEqual(r["candidates"][0]["layout"], "w5")
        self.assertEqual(self.app.shop.label_templates(), [])
        with self.assertRaises(Exception):
            self.app.detect_label({"id": 9, "role": "cashier", "workstation": "t"}, {}, {"code": code})

    def test_scale_devices_are_saved_with_their_request_style(self):
        self.app.save_devices(self.boss, {}, {"devices": [{"id": "scale1", "type": "serial", "port": "/dev/null-x",
                                                            "role": "scale", "poll": "W", "workstation": "till-1"}]})
        saved = self.app.shop.settings()["scanner_devices"][0]
        self.assertEqual((saved["role"], saved["poll"]), ("scale", "W"))
        self.assertTrue(self.app.scale_weight(self.boss, {}, {})["has_scale"])
        other = dict(self.boss, workstation="till-9")
        self.assertEqual(self.app.scale_weight(other, {}, {}), {"has_scale": False})


if __name__ == "__main__":
    unittest.main()
