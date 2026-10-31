"""Training proves a task by ledger rows, not by page views; the permission edge of the
new report and pack endpoints is checked through HTTP (hiding a button is not enough)."""

import http.client
import json
import pathlib
import tempfile
import threading
import unittest

import helpers  # noqa: F401
from server.app import serve


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json", "X-SM": "1"}
        if self.cookie:
            headers["Cookie"] = "sm_session=" + self.cookie
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        data = json.loads(resp.read() or b"null")
        cookie = resp.getheader("Set-Cookie")
        conn.close()
        if cookie and "sm_session=" in cookie:
            self.cookie = cookie.split("sm_session=")[1].split(";")[0] or None
        return resp.status, data

    def login(self, user):
        self.cookie = None
        status, body = self.call("POST", "/api/login", {"username": user, "password": "training-only", "workstation": "t-" + user})
        assert status == 200, body


class TrainingProof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.httpd = serve(pathlib.Path(cls.tmp.name) / "training-data", "training", port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.httpd.app.store.close()
        cls.tmp.cleanup()

    def test_exercise_is_done_only_when_the_ledger_has_the_document(self):
        c = Client(self.port)
        c.login("cashier")
        _, before = c.call("GET", "/api/training/progress")
        by = {x["id"]: x for x in before["exercises"]}
        self.assertFalse(by["sell"]["done"])
        self.assertEqual(by["sell"]["waiting_for"], ["shift_open"])
        self.assertFalse(by["receive"]["for_you"])                       # a cashier is not asked to receive goods
        self.assertEqual(c.call("POST", "/api/shift/open", {"key": "tr-open-1", "opening_float": 10000})[0], 200)
        juice = self.httpd.app.store.scalar("SELECT id FROM products WHERE code='1001'")
        cart = {"lines": [{"product_id": juice, "qty": 1}]}
        quote = c.call("POST", "/api/quote", {"payload": cart})[1]
        status, sale = c.call("POST", "/api/sales", {"key": "tr-sale-1", "payload": dict(cart, cash_tendered=10000, expected_total=quote["total"],
                                                                                         quote_hash=quote["quote_hash"])})
        self.assertEqual(status, 200, sale)
        _, after = c.call("GET", "/api/training/progress")
        by = {x["id"]: x for x in after["exercises"]}
        self.assertTrue(by["shift_open"]["done"])
        self.assertTrue(by["sell"]["done"])
        self.assertEqual(by["sell"]["evidence"], [sale["no"]])           # the proof is the real document number
        self.assertFalse(by["return"]["done"])

    def test_progress_belongs_to_the_user_who_did_the_work(self):
        other = Client(self.port)
        other.login("manager")
        _, data = other.call("GET", "/api/training/progress")
        by = {x["id"]: x for x in data["exercises"]}
        self.assertFalse(by["sell"]["done"])

    def test_report_endpoints_need_the_reports_permission(self):
        c = Client(self.port)
        c.login("cashier")
        self.assertEqual(c.call("GET", "/api/reports/period?start=2026-10-01&end=2026-10-31")[0], 403)
        self.assertEqual(c.call("GET", "/api/aging")[0], 403)
        c.login("owner")
        self.assertEqual(c.call("GET", "/api/reports/period?start=2026-10-01&end=2026-10-31")[0], 200)
        self.assertEqual(c.call("GET", "/api/aging")[0], 200)
        self.assertEqual(c.call("GET", "/api/reports/period?start=2026-10-31&end=2026-10-01")[0], 400)


class ShopModeHasNoTraining(unittest.TestCase):
    def test_training_progress_is_refused_on_the_real_shop(self):
        with tempfile.TemporaryDirectory() as tmp:
            httpd = serve(pathlib.Path(tmp) / "shop-data", "shop", port=0)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                c = Client(httpd.server_address[1])
                c.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "long-enough-1"})
                c.cookie = None
                c.call("POST", "/api/login", {"username": "boss", "password": "long-enough-1", "workstation": "w"})
                self.assertNotEqual(c.call("GET", "/api/training/progress")[0], 200)
            finally:
                httpd.shutdown()
                httpd.server_close()
                httpd.app.store.close()


if __name__ == "__main__":
    unittest.main()
