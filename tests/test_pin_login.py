"""SM-UX-LOGIN: name + PIN. Staff PIN is 4 digits, owner and manager are 6.
Weak patterns are refused. Five wrong tries use the same lockout as a password.
The hash never leaves the shop PC."""

import http.client
import json
import pathlib
import tempfile
import threading
import unittest

import helpers  # noqa: F401
from server.app import serve
from server.cloudsync import export_row
from test_language import NODE, dictionaries


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
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

    def login(self, user, password):
        self.cookie = None
        return self.call("POST", "/api/login", {"username": user, "password": password, "workstation": "t1"})

    def login_pin(self, user_id, pin):
        self.cookie = None
        return self.call("POST", "/api/login", {"user_id": user_id, "pin": pin, "workstation": "t1"})


class PinLogin(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = pathlib.Path(tmp.name) / "data"
        self.httpd = serve(self.data, "shop", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.app.store.close)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.owner = Client(self.httpd.server_address[1])
        self.owner.call("POST", "/api/setup", {"username": "boss", "name": "المالك", "password": "boss-pass-1"})
        self.owner.login("boss", "boss-pass-1")
        self.cid = self.owner.call("POST", "/api/users", {"username": "cash", "name": "أمين الصندوق", "role": "cashier", "password": "temp-pass-1"})[1]["id"]
        self.mid = self.owner.call("POST", "/api/users", {"username": "mgr", "name": "المدير", "role": "manager", "password": "temp-pass-1"})[1]["id"]

    def test_weak_pins_are_refused_and_a_good_pin_signs_in(self):
        weak4 = ["0000", "1111", "1234", "4321", "0123", "1212", "123456"]
        for pin in weak4:
            status, body = self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": pin})
            self.assertIn(body["error"], ("weak_pin", "bad_pin_length"), pin)
            self.assertNotEqual(status, 200, pin)
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "2580"})[0], 200)
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "١٣٥٧"})[0], 200)  # eastern digits, still 4
        people = self.owner.call("GET", "/api/login/people")[1]
        cash = next(p for p in people if p["username"] == "cash")
        self.assertEqual(cash["pin_digits"], 4)
        self.assertTrue(cash["has_pin"])
        self.assertNotIn("pin_hash", cash)
        till = Client(self.httpd.server_address[1])
        status, body = till.login_pin(self.cid, "1357")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["user"]["role"], "cashier")
        self.assertNotIn("pin_hash", body["user"])
        shift = till.call("GET", "/api/shift")
        self.assertEqual(shift[0], 200)
        self.assertFalse(isinstance(shift[1], dict) and shift[1].get("error") == "must_change_password")
        # the old password still opens the same account, and still asks for a new password
        pw = Client(self.httpd.server_address[1])
        self.assertEqual(pw.login("cash", "temp-pass-1")[0], 200)
        self.assertEqual(pw.call("GET", "/api/shift")[1]["error"], "must_change_password")

    def test_owner_and_manager_need_six_and_staff_need_four(self):
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.mid, {"pin": "2580"})[1]["error"], "bad_pin_length")
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.mid, {"pin": "2580"})[1]["detail"]["digits"], 6)
        for pin in ("000000", "111111", "123456", "654321", "123123"):
            self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.mid, {"pin": pin})[1]["error"], "weak_pin", pin)
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.mid, {"pin": "258013"})[0], 200)
        me = self.owner.call("GET", "/api/state")[1]["user"]["id"]
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % me, {"pin": "2580"})[1]["detail"]["digits"], 6)
        self.assertEqual(self.owner.call("POST", "/api/account/pin", {"old": "boss-pass-1", "pin": "246813"})[0], 200)
        self.assertEqual(Client(self.httpd.server_address[1]).login_pin(me, "246813")[0], 200)
        self.assertEqual(Client(self.httpd.server_address[1]).login_pin(self.mid, "258013")[0], 200)
        self.assertEqual(Client(self.httpd.server_address[1]).login("boss", "boss-pass-1")[0], 200)

    def test_five_wrong_pins_lock_and_the_owner_reset_clears_it(self):
        self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "2580"})
        till = Client(self.httpd.server_address[1])
        for _ in range(5):
            status, body = till.login_pin(self.cid, "1357")
            self.assertEqual((status, body["error"]), (401, "bad_pin"))
        status, body = till.login_pin(self.cid, "2580")
        self.assertEqual((status, body["error"]), (429, "account_locked"))
        self.assertGreaterEqual(body["detail"]["retry_after"], 1)
        # a wrong password counts on the same name, and a cashier cannot reset the PIN
        self.assertEqual(self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "2468"})[0], 200)
        self.assertEqual(till.login_pin(self.cid, "2468")[0], 200)
        self.assertEqual(till.call("POST", "/api/users/%d/pin" % self.mid, {"pin": "258013"})[0], 403)

    def test_pin_and_password_failures_share_one_lockout(self):
        self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "2580"})
        till = Client(self.httpd.server_address[1])
        for _ in range(4):
            self.assertEqual(till.login("cash", "wrong-pass-1")[1]["error"], "bad_login")
        self.assertEqual(till.login_pin(self.cid, "1357")[1]["error"], "bad_pin")
        self.assertEqual(till.login_pin(self.cid, "2580")[1]["error"], "account_locked")

    def test_pin_hash_is_not_exported_or_logged(self):
        self.owner.call("POST", "/api/users/%d/pin" % self.cid, {"pin": "2580"})
        row = export_row(self.httpd.app.store.db, "users", self.cid)
        self.assertNotIn("pin_hash", row)
        self.assertNotIn("pin_salt", row)
        self.assertNotIn("2580", json.dumps(row))
        users = self.owner.call("GET", "/api/users")[1]
        self.assertTrue(all("pin_hash" not in u for u in users))
        audit = json.dumps(self.httpd.app.store.all("SELECT action, IFNULL(detail,'') AS detail FROM audit"), ensure_ascii=False)
        self.assertNotIn("2580", audit)

    @unittest.skipUnless(NODE, "node reads the dictionaries")
    def test_pin_words_exist_in_arabic_and_english(self):
        d = dictionaries()
        keys = ["login.who", "login.who_sub", "login.pin_hint_4", "login.pin_hint_6",
                "login.use_password", "err.bad_pin", "err.weak_pin", "err.bad_pin_length",
                "set.pin_btn", "set.pin_help_4", "set.pin_help_6"]
        missing = [k for k in keys if k not in d["ar"] or k not in d["en"]]
        self.assertEqual(missing, [])
        self.assertEqual(d["ar"]["login.who"], "من أنت؟")
        self.assertNotIn("مين", d["ar"]["login.who"])
        ar4 = d["ar"]["login.pin_hint_4"].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        ar6 = d["ar"]["login.pin_hint_6"].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        self.assertIn("4", ar4)
        self.assertIn("6", ar6)
        self.assertIn("4", d["en"]["login.pin_hint_4"])
        self.assertIn("6", d["en"]["login.pin_hint_6"])
        self.assertIn("1234", d["ar"]["err.weak_pin"])
        self.assertIn("1234", d["en"]["err.weak_pin"])


if __name__ == "__main__":
    unittest.main()
