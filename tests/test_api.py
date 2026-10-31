"""HTTP contract: auth, CSRF header, setup only once and only locally, cost
hidden from cashier even through direct API calls (A01), errors without
internals, training seed isolated."""

import http.client
import json
import pathlib
import tempfile
import threading
import unittest

import helpers  # noqa: F401  (path + fast hashing)
from helpers import key
from server.app import serve


class Api(unittest.TestCase):
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

    def call(self, method, path, body=None, header=True, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-SM"] = "1"
        if cookie or self.cookie:
            headers["Cookie"] = "sm_session=" + (cookie or self.cookie)
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = resp.read()
        set_cookie = resp.getheader("Set-Cookie")
        conn.close()
        if set_cookie and "sm_session=" in set_cookie:
            self.cookie = set_cookie.split("sm_session=")[1].split(";")[0] or None
        try:
            return resp.status, json.loads(data)
        except ValueError:
            return resp.status, data

    def login(self, user, password):
        self.cookie = None
        status, body = self.call("POST", "/api/login", {"username": user, "password": password, "workstation": "till-1"})
        self.assertEqual(status, 200, body)

    def test_setup_login_sell_and_permissions(self):
        status, state = self.call("GET", "/api/state")
        self.assertTrue(state["setup_needed"])
        self.assertEqual(self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "short"})[0], 400)
        self.assertEqual(self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1"})[0], 200)
        self.assertEqual(self.call("POST", "/api/setup", {"username": "x", "name": "X", "password": "long-enough-1"})[0], 409)
        self.assertEqual(self.call("GET", "/api/stock")[0], 401)
        self.login("boss", "long-enough-1")
        self.assertEqual(self.call("POST", "/api/users", {"username": "c", "name": "C", "role": "cashier", "password": "cashier-pass"})[0], 200)
        status, prod = self.call("POST", "/api/products", {"code": "1", "name_ar": "أ", "name_en": "A", "kind": "grocery",
                                                           "measure": "unit", "price": 1000})
        locs = self.call("GET", "/api/locations")[1]
        shelf = [l for l in locs if l["code"] == "SH"][0]["id"]
        status, _ = self.call("POST", "/api/receipts", {"key": key(), "payload": {"lines": [
            {"product_id": prod["id"], "qty": 5, "line_cost": 2500, "location_id": shelf}]}})
        self.assertEqual(status, 200)
        # CSRF: POST without the custom header is refused
        self.assertEqual(self.call("POST", "/api/shift/open", {"key": key(), "opening_float": 0}, header=False)[0], 403)
        self.login("c", "cashier-pass")
        self.assertEqual(self.call("POST", "/api/shift/open", {"key": key(), "opening_float": 10000})[0], 200)
        k = key()
        cart = {"lines": [{"product_id": prod["id"], "qty": 2}]}
        status, err = self.call("POST", "/api/sales", {"key": k, "payload": dict(cart, cash_tendered=5000)})
        self.assertEqual((status, err["error"]), (400, "quote_required"))       # R14: no silent pricing
        quote = self.call("POST", "/api/quote", {"payload": cart})[1]
        sale = {"key": k, "payload": dict(cart, cash_tendered=5000, expected_total=quote["total"], quote_hash=quote["quote_hash"])}
        status, first = self.call("POST", "/api/sales", sale)
        self.assertEqual((status, first["change"]), (200, 3000))
        self.assertNotIn("cost", first)
        status, again = self.call("POST", "/api/sales", sale)   # retry after lost reply
        self.assertEqual(again["sale"], first["sale"])
        # A01: cashier calling cost/report/export endpoints directly
        for path in ("/api/reports/day", "/api/export/stock.csv", "/api/users", "/api/backups"):
            self.assertEqual(self.call("GET", path)[0], 403, path)
        stock = self.call("GET", "/api/stock")[1]
        self.assertNotIn("cost", stock[0])
        status, err = self.call("POST", "/api/sales", {"key": key(), "payload": {"lines": [{"product_id": 999, "qty": 1}], "quote_hash": "x"}})
        self.assertEqual((status, err["error"]), (400, "unknown_product"))
        status, err = self.call("POST", "/api/sales", {"key": key(), "payload": "junk"})
        self.assertEqual(status, 400)
        self.assertNotIn("Traceback", json.dumps(err))
        # R18: reprint keeps the saved sale time and says it is a reprint
        reprint = self.call("GET", "/api/sales/%d/receipt" % first["sale"])[1]
        self.assertEqual((reprint["at"], reprint["reprint"]), (first["receipt"]["at"], True))
        # static files cannot escape the web folder
        self.assertEqual(self.call("GET", "/../server/app.py")[0], 404)
        self.call("POST", "/api/logout", {})
        self.assertEqual(self.call("GET", "/api/stock")[0], 401)

    def test_dns_rebinding_host_and_bad_length_refused(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/state", headers={"Host": "evil.example:%d" % self.port})
        self.assertEqual(conn.getresponse().status, 421)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.putrequest("POST", "/api/login", skip_accept_encoding=True)
        conn.putheader("Content-Length", "-1")
        conn.putheader("X-SM", "1")
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        self.assertEqual(self.call("GET", "/api/products?limit=abc")[0], 401)

    def test_r09_scanner_events_reach_only_the_bound_till_and_its_active_window(self):
        self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1"})
        hub = self.httpd.app.hub
        hub.bindings = {"dev-1": "till-1"}
        self.login("boss", "long-enough-1")                               # logs in as workstation till-1
        claim = self.call("POST", "/api/scanner/claim", {"window": "w1"})[1]
        hub.push("dev-1", "6221234567890")
        hub.push("dev-2", "999")                                         # a device of no / another till
        r = self.call("GET", "/api/scanner/events?wait=0&window=w1&after=0&epoch=" + claim["epoch"])[1]
        self.assertEqual([e["text"] for e in r["events"]], ["6221234567890"])
        self.call("POST", "/api/scanner/claim", {"window": "w2"})       # the same till opens a second window
        status, err = self.call("GET", "/api/scanner/events?wait=0&window=w1&after=0")
        self.assertEqual((status, err["error"]), (409, "scanner_taken_by_other_window"))

    def test_login_lockout(self):
        self.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1"})
        for _ in range(5):
            self.call("POST", "/api/login", {"username": "boss", "password": "nope", "workstation": "w"})
        status, body = self.call("POST", "/api/login", {"username": "boss", "password": "long-enough-1", "workstation": "w"})
        self.assertEqual((status, body["error"]), (401, "account_locked"))


class TrainingApi(unittest.TestCase):
    def test_training_seed_is_marked_and_sells(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd = serve(pathlib.Path(tmp) / "training-data", "training", port=0)
            try:
                app = httpd.app
                self.assertEqual(app.store.mode, "training")
                cashier = app.auth.public(app.store.one("SELECT * FROM users WHERE username='cashier'"))
                cashier["workstation"] = "t"
                app.shop.open_shift(cashier, key(), 0)
                juice = app.store.scalar("SELECT id FROM products WHERE code='1001'")
                sale = app.shop.sell(cashier, key(), {"lines": [{"product_id": juice, "qty": 1}]})
                self.assertTrue(sale["no"].startswith("TR-"))
                self.assertTrue(sale["training"])
            finally:
                httpd.server_close()
                httpd.app.store.close()


if __name__ == "__main__":
    unittest.main()
