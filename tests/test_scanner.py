"""Scanner contracts: text parsing, framing, real pty serial and TCP transports."""

import os
import socket
import sys
import threading
import time
import unittest

from helpers import ShopFixture, ean, key
from server import barcode as bc
from server.scanner import Framer, PosixSerial, ScannerHub, TcpSource


class Parsing(unittest.TestCase):
    def test_leading_zeros_and_lengths_stay_text(self):
        upc = ean("01234567890")
        self.assertEqual(bc.classify(upc)["code"], upc)
        self.assertEqual(bc.classify("0" + upc)["code"], "0" + upc)     # no silent 12->13
        self.assertEqual(bc.classify(ean("1234567"))["length"], 8)

    def test_bad_check_digit_and_damage(self):
        good = ean("622123456789")
        bad = good[:-1] + str((int(good[-1]) + 1) % 10)
        for raw in (bad, "62212\x0034567", "x" * 600):
            with self.subTest(raw=raw[:20]), self.assertRaises(bc.ScanError):
                bc.classify(raw)

    def test_arabic_digits_and_layout(self):
        self.assertEqual(bc.normalize("٦٢٢١٢٣"), "622123")
        with self.assertRaises(bc.ScanError) as err:
            bc.normalize("حنشلاة")        # "PKabm" typed while Windows layout is Arabic
        self.assertEqual(err.exception.code, "layout_mismatch")
        self.assertEqual(bc.normalize("حنشلاة", {"layout_fix": "arabic101"}), "pkabm")
        self.assertEqual(bc.normalize("ABC-123", {"layout_fix": "arabic101"}), "ABC-123")

    def test_gs1_element_strings(self):
        gtin = ean("0622123456789")
        raw = "01" + gtin + "17261105" + "10LOT42" + bc.GS + "3102001250"
        info = bc.classify(raw)
        self.assertEqual(info["kind"], "gs1")
        self.assertEqual(info["fields"], {"gtin": gtin, "expiry": "2026-11-05", "batch": "LOT42", "net_g": 12500})
        self.assertEqual(bc.classify("]C1" + raw, {"aim_ids": True})["symbology"], "]C1")
        self.assertEqual(bc.classify("(01)%s(17)261100" % gtin)["fields"]["expiry"], "2026-11-30")
        self.assertEqual(bc.classify("01" + gtin + "17261105<GS>10A")["fields"]["batch"], "A")

    def test_scale_labels_weight_and_price(self):
        weight = bc.check_template({"prefix": "21", "length": 13, "item_start": 2, "item_len": 5,
                                    "value_start": 7, "value_len": 5, "value_kind": "weight", "decimals": 3})
        code = ean("211234501250")
        self.assertEqual(bc.classify(code, templates=[weight])["net_g"], 1250)
        price = dict(weight, value_kind="price", decimals=2)
        self.assertEqual(bc.classify(code, templates=[price])["price_piastres"], 1250)
        with self.assertRaises(bc.ScanError):
            bc.check_template(dict(weight, item_start=6))           # overlaps value

    def test_urls_and_qr_are_not_products(self):
        self.assertEqual(bc.classify("https://example.com/x")["kind"], "url")
        self.assertEqual(bc.classify("SOME-QR-TEXT")["kind"], "code")

    def test_ean8_is_not_expanded_as_upce(self):
        self.assertTrue(bc.valid_gs1("19999982"))
        self.assertEqual(bc.gtin_variants("19999982"), [])

    def test_upce(self):
        self.assertEqual(bc.upce_to_upca("04252614"), "042100005264")

    def test_visible_shows_control_characters(self):
        self.assertEqual(bc.visible("\x02123\x1d4\r\n"), "<STX>123<GS>4<CR><LF>")


class Framing(unittest.TestCase):
    def test_fragmented_reads_join_and_two_identical_scans_stay_two(self):
        f = Framer(suffix="CR")
        self.assertEqual(f.feed(b"6221", 0.0), [])
        self.assertEqual(f.feed(b"23\r62", 0.01), ["622123"])
        self.assertEqual(f.feed(b"2123\r", 0.02), ["622123"])

    def test_crlf_prefix_and_noise(self):
        f = Framer(prefix=b"\x02", suffix="CR")
        self.assertEqual(f.feed(b"\x02ABC\r\n\x02DEF\r\n", 0), ["ABC", "DEF"])

    def test_partial_frame_dropped_not_glued(self):
        f = Framer(suffix="CR", partial_ms=500)
        f.feed(b"12", 0.0)
        self.assertEqual(f.poll(1.0), [])
        self.assertEqual(f.dropped, 1)
        self.assertEqual(f.feed(b"34\r", 1.1), ["34"])

    def test_gap_mode_without_suffix(self):
        f = Framer(suffix=None, gap_ms=50)
        f.feed(b"123", 0.0)
        f.feed(b"45", 0.02)
        self.assertEqual(f.poll(0.03), [])
        self.assertEqual(f.poll(0.2), ["12345"])

    def test_overlong_garbage_discarded(self):
        f = Framer(suffix="CR")
        f.feed(b"9" * 600, 0)
        self.assertEqual(f.buffer, b"")
        self.assertEqual(f.dropped, 1)


@unittest.skipUnless(sys.platform != "win32", "pty is POSIX")
class SerialAndTcp(unittest.TestCase):
    def test_virtual_serial_port_with_disconnect(self):
        import pty
        master, slave = pty.openpty()
        path = os.ttyname(slave)
        port = PosixSerial(path, 9600)
        f = Framer(suffix="CR")
        os.write(master, b"622")
        time.sleep(0.02)
        os.write(master, b"1234567890\r")
        got, deadline = [], time.time() + 2
        while not got and time.time() < deadline:
            data = port.read(0.05)
            got += f.feed(data) if data else []
        self.assertEqual(got, ["6221234567890"])
        port.close()
        os.close(master)
        os.close(slave)

    def test_tcp_source_and_hub_reconnect(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        host, port_no = server.getsockname()
        hub = ScannerHub()
        hub.configure([{"id": "tcp1", "type": "tcp", "host": host, "port": port_no, "suffix": "CRLF",
                        "workstation": "till-1"}])
        conn, _ = server.accept()
        conn.sendall(b"ABC\r\nABC\r\n")
        events, last = hub.since(0, wait=2)
        while len(events) < 2:
            more, last = hub.since(last, wait=2)
            self.assertTrue(more)
            events += more
        self.assertEqual([e["text"] for e in events], ["ABC", "ABC"])
        self.assertNotEqual(events[0]["id"], events[1]["id"])
        conn.close()                                   # disconnect
        conn2, _ = server.accept()                     # device manager reconnects
        conn2.sendall(b"XYZ\r\n")
        found, _ = hub.since(last, wait=3)
        self.assertEqual(found[0]["text"], "XYZ")
        self.assertEqual(hub.status()[0]["status"], "connected")
        hub.stop()
        conn2.close()
        server.close()


class ScanToShop(ShopFixture, unittest.TestCase):
    def setUp(self):
        self.make_shop()

    def test_b01_b03_weight_label_and_unknown(self):
        pid = self.meat("12345", 40000)
        self.shop.save_label_template(self.owner, {"prefix": "21", "length": 13, "item_start": 2, "item_len": 5,
                                                   "value_start": 7, "value_len": 5, "value_kind": "weight", "decimals": 3})
        hit = self.shop.resolve_scan(self.cash1, ean("211234501250"))
        self.assertEqual((hit["product"]["id"], hit["qty"]), (pid, 1250))
        miss = self.shop.resolve_scan(self.cash1, ean("219999901250"))
        self.assertEqual(miss["error"], "unknown_item")
        bad = self.shop.resolve_scan(self.cash1, "2112345012501")
        self.assertFalse(bad["ok"])

    def test_b02_price_label_without_weight_is_blocked(self):
        self.meat("12345", 40000)
        self.shop.save_label_template(self.owner, {"prefix": "21", "length": 13, "item_start": 2, "item_len": 5,
                                                   "value_start": 7, "value_len": 5, "value_kind": "price", "decimals": 2})
        self.assertEqual(self.shop.resolve_scan(self.cash1, ean("211234505000"))["error"], "price_label_needs_weight")

    def test_similar_gtin_is_offered_not_merged(self):
        self.grocery(barcodes=[ean("01234567890")])          # stored as 12-digit UPC-A
        result = self.shop.resolve_scan(self.cash1, "0" + ean("01234567890"))
        self.assertEqual(result["error"], "similar_barcode_needs_link")
        self.shop.update_settings(self.owner, {"gtin_equivalence": True})
        self.assertTrue(self.shop.resolve_scan(self.cash1, "0" + ean("01234567890"))["ok"])

    def test_link_scan_is_refused(self):
        self.assertEqual(self.shop.resolve_scan(self.cash1, "http://evil.example/x")["error"], "scan_is_link")


if __name__ == "__main__":
    unittest.main()
