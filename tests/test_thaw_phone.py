"""SM-THAW-OFF and SM-PHONE-RO.

A new shop neither shows nor runs thaw until the owner turns it on and types
the shop's own period. A phone session is reports-only on the server.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest

import helpers  # noqa: F401
from helpers import ShopFixture, key
from server.app import serve
from server.core import ShopError
from server.db import Store
from server.phone_access import is_phone, reject_if_phone
from server.services import Shop
from server.training import seed

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class GatesJs(unittest.TestCase):
    @unittest.skipUnless(NODE, "node runs the browser gate")
    def test_phone_detection_and_hidden_thaw_lesson(self):
        script = r"""
        const g = require(process.argv[1]);
        const cat = {problems:[{id:'freezer-and-places', page:'stock'},{id:'cold-places', page:'stock'},
          {id:'phone-reports-only', page:'reports'}], guides:[{id:'first-sale'}]};
        const off = g.visibleCatalogue(cat, {thaw:false});
        const on = g.visibleCatalogue(cat, {thaw:true});
        const ids = (c) => c.problems.map(p => p.id);
        if (ids(off).includes('freezer-and-places')) throw new Error('thaw lesson still visible');
        if (!ids(off).includes('cold-places') || !ids(off).includes('phone-reports-only')) throw new Error('other lessons dropped');
        if (!ids(on).includes('freezer-and-places')) throw new Error('thaw lesson missing when on');
        if (off.guides.length !== 1) throw new Error('guides changed');
        if (g.phoneWorkstation(null, true, 'ab12') !== 'phone-ab12') throw new Error('new phone id');
        if (g.phoneWorkstation('till-k1', true, 'zz') !== 'phone-k1') throw new Error('upgrade id');
        if (g.phoneWorkstation('till-k1', false, 'zz') !== 'till-k1') throw new Error('desktop id changed');
        if (g.phoneWorkstation('phone-k1', false, 'zz') !== 'phone-k1') throw new Error('phone id dropped');
        if (!g.isPhoneDevice('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)')) throw new Error('iphone');
        if (!g.isPhoneDevice('Mozilla/5.0 (Linux; Android 14; Pixel) Mobile')) throw new Error('android');
        if (g.isPhoneDevice('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')) throw new Error('desktop flagged');
        if (g.isPhoneDevice('Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)')) throw new Error('ipad flagged');
        """
        subprocess.run([NODE, "-e", script, str(ROOT / "web" / "js" / "gates.js")], check=True)

    @unittest.skipUnless(NODE, "node reads the dictionaries")
    def test_stock_help_hides_thaw_until_the_extra_line(self):
        script = (
            "global.window=global;global.SM={};const fs=require('fs');"
            "for (const f of ['i18n.js','i18n/en.js','i18n/ar.js']) eval(fs.readFileSync(process.argv[1]+'/'+f,'utf8'));"
            "const ar=SM.i18n.ar, en=SM.i18n.en;"
            "for (const d of [ar, en]) {"
            " if (d['help.stock.steps'].includes('thaw.btn')) process.exit(2);"
            " if (!d['help.stock.steps.thaw'].includes('thaw.btn')) process.exit(3);"
            " if (!d['help.phone'] || !d['set.thaw_on'] || !d['err.thaw_off'] || !d['err.phone_reports_only']) process.exit(4);"
            "}"
            "if (ar['help.stock.steps'].includes('فك التجميد')) process.exit(5);"
        )
        subprocess.run([NODE, "-e", script, str(ROOT / "web" / "js")], check=True)
        src = (ROOT / "web" / "js" / "views" / "ops.js").read_text(encoding="utf-8")
        self.assertIn("SM.thawOn()", src)
        admin = (ROOT / "web" / "js" / "views" / "admin.js").read_text(encoding="utf-8")
        self.assertIn("thaw-enabled", admin)
        self.assertIn("set.advanced", admin)
        guide = (ROOT / "web" / "js" / "guide.js").read_text(encoding="utf-8")
        self.assertIn("visibleCatalogue", guide)


class Copy(unittest.TestCase):
    def test_user_guide_does_not_call_a_period_the_approved_default(self):
        text = (ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")
        self.assertNotIn("يومان", text)
        self.assertNotIn("يومين", text)
        self.assertIn("إعدادات متقدمة", text)
        self.assertIn("للتقارير فقط", text)
        brief = (ROOT / "docs" / "00-OWNER-BRIEF.md").read_text(encoding="utf-8")
        self.assertIn("للتقارير فقط", brief)
        self.assertNotIn("للمتابعة والجرد", brief)

    def test_training_can_enable_thaw_without_touching_the_real_shop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            real = Store(root / "real", "shop")
            train = Store(root / "train", "training")
            try:
                self.assertFalse(Shop(real).settings()["thaw_enabled"])
                self.assertIsNone(Shop(real).settings()["thaw_shelf_life_days"])
                shop = Shop(train)
                self.assertTrue(seed(shop))
                self.assertTrue(shop.settings()["thaw_enabled"])
                self.assertIsInstance(shop.settings()["thaw_shelf_life_days"], int)
                self.assertFalse(Shop(real).settings()["thaw_enabled"])
                self.assertIsNone(Shop(real).settings()["thaw_shelf_life_days"])
            finally:
                real.close()
                train.close()

    def test_upgrade_keeps_thaw_only_after_a_real_thaw_and_invents_no_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            fresh = Store(root / "fresh", upto=12)
            fresh.close()
            opened = Store(root / "fresh")
            try:
                self.assertEqual(opened.schema_version(), 13)
                self.assertFalse(Shop(opened).settings()["thaw_enabled"])
                self.assertIsNone(Shop(opened).settings()["thaw_shelf_life_days"])
            finally:
                opened.close()

            saved = Store(root / "saved", upto=12)
            saved.db.execute("INSERT INTO settings VALUES('thaw_shelf_life_days', '1')")
            saved.close()
            kept = Store(root / "saved")
            try:
                self.assertFalse(Shop(kept).settings()["thaw_enabled"])
                self.assertEqual(Shop(kept).settings()["thaw_shelf_life_days"], 1)
            finally:
                kept.close()

            used = Store(root / "used", upto=12)
            used.db.execute("PRAGMA foreign_keys=OFF")
            used.db.execute("INSERT INTO settings VALUES('thaw_shelf_life_days', '1')")
            used.db.execute(
                "INSERT INTO thaws(op_id,parent_lot,child_lot,from_location,to_location,qty,policy_days,user_id,at) "
                "VALUES(1,1,2,1,2,100,1,1,'2026-01-01')")
            self.assertEqual(used.scalar("SELECT COUNT(*) FROM thaws"), 1)
            used.close()
            upgraded = Store(root / "used")
            try:
                self.assertTrue(Shop(upgraded).settings()["thaw_enabled"])
                self.assertEqual(Shop(upgraded).settings()["thaw_shelf_life_days"], 1)
            finally:
                upgraded.close()


class PhoneRule(unittest.TestCase):
    def test_either_signal_is_a_phone_and_count_is_refused(self):
        self.assertFalse(is_phone({"workstation": "till-1"}, {}))
        self.assertTrue(is_phone({"workstation": "phone-1"}, {}))
        self.assertTrue(is_phone({"workstation": "Phone-abc"}, {}))
        self.assertTrue(is_phone({"workstation": "till-1"}, {"X-SM-Client": "phone"}))
        self.assertTrue(is_phone({"workstation": "till-1"}, {"X-SM-Capability": "reports"}))
        self.assertIsNone(reject_if_phone({"workstation": "till-1"}, {}, "POST", "/api/counts"))
        with self.assertRaises(ShopError) as err:
            reject_if_phone({"workstation": "phone-1"}, {}, "GET", "/api/counts")
        self.assertEqual(err.exception.status, 403)
        self.assertEqual(err.exception.code, "phone_reports_only")
        with self.assertRaises(ShopError):
            reject_if_phone({"workstation": "phone-1"}, {}, "POST", "/api/sales")
        self.assertIsNone(reject_if_phone({"workstation": "phone-1"}, {}, "GET", "/api/reports/day"))
        self.assertIsNone(reject_if_phone({"workstation": "phone-1"}, {}, "POST", "/api/login"))


class HttpShop(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.httpd = serve(pathlib.Path(tmp.name) / "data", "shop", port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.app.store.close)
        self.cookie = None

    def call(self, method, path, body=None, headers=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Content-Type": "application/json"}
        if method == "POST":
            hdrs["X-SM"] = "1"
        if headers:
            hdrs.update(headers)
        if self.cookie:
            hdrs["Cookie"] = "sm_session=" + self.cookie
        conn.request(method, path, json.dumps(body) if body is not None else None, hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        conn.close()
        if set_cookie and "sm_session=" in set_cookie:
            self.cookie = set_cookie.split("sm_session=")[1].split(";")[0] or None
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return resp.status, data

    def login(self, workstation):
        self.cookie = None
        status, body = self.call("POST", "/api/login", {"username": "boss", "password": "long-enough-1", "workstation": workstation})
        self.assertEqual(status, 200, body)

    def test_new_shop_thaw_is_off_until_the_owner_types_a_period(self):
        self.assertEqual(self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1", "shop_name": "محل"})[0], 200)
        self.login("till-1")
        settings = self.call("GET", "/api/settings")[1]
        self.assertIs(settings["thaw_enabled"], False)
        self.assertIsNone(settings["thaw_shelf_life_days"])
        status, body = self.call("POST", "/api/thaw", {"key": key(), "lot_id": 1, "from": 1, "to": 1, "qty": 1})
        self.assertEqual((status, body["error"]), (403, "thaw_off"))
        status, body = self.call("POST", "/api/settings", {"thaw_enabled": True})
        self.assertEqual((status, body["error"]), (400, "thaw_policy_missing"))
        self.assertIs(self.call("GET", "/api/settings")[1]["thaw_enabled"], False)
        self.assertIsNone(self.call("GET", "/api/settings")[1]["thaw_shelf_life_days"])
        # The owner types the shop's own period. The server does not fill one in.
        status, body = self.call("POST", "/api/settings", {"thaw_enabled": True, "thaw_shelf_life_days": 1})
        self.assertEqual(status, 200, body)
        self.assertIs(body["thaw_enabled"], True)
        self.assertEqual(body["thaw_shelf_life_days"], 1)
        status, body = self.call("POST", "/api/thaw", {"key": key(), "lot_id": 1, "from": 1, "to": 1, "qty": 1})
        self.assertNotEqual(body.get("error"), "thaw_off")

    def test_phone_cannot_count_sell_or_return_and_the_till_still_can(self):
        self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1", "shop_name": "محل"})
        self.login("till-1")
        shelf = [row["id"] for row in self.call("GET", "/api/locations")[1] if row["code"] == "SH"][0]
        status, opened = self.call("POST", "/api/counts", {"location_id": shelf})
        self.assertEqual(status, 200, opened)
        self.assertEqual(self.call("GET", "/api/counts")[0], 200)
        status, body = self.call("POST", "/api/sales", {"key": key(), "payload": {}})
        self.assertNotEqual(body.get("error"), "phone_reports_only")
        self.login("phone-counter")
        for method, path, payload in (
            ("POST", "/api/counts", {"location_id": shelf}),
            ("GET", "/api/counts", None),
            ("GET", "/api/counts/%d" % opened["count"], None),
            ("POST", "/api/sales", {"key": key(), "payload": {}}),
            ("POST", "/api/returns", {"key": key(), "payload": {}}),
            ("POST", "/api/quote", {"payload": {}}),
        ):
            status, body = self.call(method, path, payload)
            self.assertEqual((path, status, body.get("error")), (path, 403, "phone_reports_only"))
        self.assertEqual(self.call("GET", "/api/reports/day")[0], 200)
        self.login("till-1")
        status, body = self.call("POST", "/api/counts", {"location_id": shelf}, {"X-SM-Client": "phone"})
        self.assertEqual((status, body["error"]), (403, "phone_reports_only"))
        status, body = self.call("POST", "/api/counts", {"location_id": shelf}, {"X-SM-Capability": "reports"})
        self.assertEqual((status, body["error"]), (403, "phone_reports_only"))
        self.assertEqual(self.call("POST", "/api/counts", {"location_id": shelf})[0], 200)


class DirectThaw(ShopFixture, unittest.TestCase):
    def test_moving_frozen_stock_says_thaw_is_off(self):
        self.make_shop()
        freezer = self.shop.add_location(self.owner, "FZ", "مجمد", "Freezer", "freezer", 0)
        lot = self.receive(self.meat(), 1000, 10000, location=freezer, expires="2027-01-01")["lines"][0]["lot"]
        with self.assertRaises(ShopError) as err:
            self.shop.transfer(self.owner, key(), lot, freezer, self.prep, 100)
        self.assertEqual(err.exception.code, "thaw_off")
        self.assertEqual(err.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
