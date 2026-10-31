"""Accounts (gap table row 5): password change, stopping an employee, server-side
screen lock and owner recovery on the shop computer. Every rule is checked over
HTTP, because hiding a button is not a permission."""

import http.client
import json
import pathlib
import tempfile
import threading
import unittest

import helpers  # noqa: F401
from server.app import serve
from server.db import Store
from tools import shopctl


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

    def login(self, user, password, till="t1"):
        self.cookie = None
        return self.call("POST", "/api/login", {"username": user, "password": password, "workstation": till})


class Accounts(unittest.TestCase):
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
        self.owner.call("POST", "/api/setup", {"username": "boss", "name": "Boss", "password": "boss-pass-1"})
        self.owner.login("boss", "boss-pass-1")
        self.cid = self.owner.call("POST", "/api/users", {"username": "cash", "name": "C", "role": "cashier", "password": "temp-pass-1"})[1]["id"]
        self.cash = Client(self.httpd.server_address[1])
        self.cash.login("cash", "temp-pass-1")
        self.cash.call("POST", "/api/account/password", {"old": "temp-pass-1", "new": "cash-pass-1"})

    def test_password_change_needs_the_old_one_and_ends_other_sessions(self):
        other = Client(self.httpd.server_address[1])
        other.login("cash", "cash-pass-1", "t2")
        self.assertEqual(self.cash.call("POST", "/api/account/password", {"old": "wrong-pass", "new": "x-new-pass-1"})[0], 401)
        self.assertEqual(self.cash.call("POST", "/api/account/password", {"old": "cash-pass-1", "new": "short"})[1]["error"], "weak_password")
        self.assertEqual(self.cash.call("POST", "/api/account/password", {"old": "cash-pass-1", "new": "cash-pass-2"})[0], 200)
        self.assertEqual(other.call("GET", "/api/shift")[0], 401)                    # the other till is logged out
        self.assertEqual(self.cash.call("GET", "/api/shift")[0], 200)                # this one stays
        self.assertEqual(Client(self.httpd.server_address[1]).login("cash", "cash-pass-1")[0], 401)

    def test_stopping_an_employee_ends_his_sessions_and_blocks_login_but_keeps_his_records(self):
        self.assertEqual(self.cash.call("POST", f"/api/users/{self.cid}/active", {"active": False, "reason": "x"})[0], 403)
        self.assertEqual(self.owner.call("POST", f"/api/users/{self.cid}/active", {"active": False, "reason": "left the shop"})[0], 200)
        self.assertEqual(self.cash.call("GET", "/api/shift")[0], 401)
        self.assertEqual(Client(self.httpd.server_address[1]).login("cash", "cash-pass-1")[0], 401)
        users = {u["username"]: u for u in self.owner.call("GET", "/api/users")[1]}
        self.assertEqual(users["cash"]["active"], 0)                                 # stopped, never deleted
        self.owner.call("POST", f"/api/users/{self.cid}/active", {"active": True, "reason": "back"})
        self.assertEqual(Client(self.httpd.server_address[1]).login("cash", "cash-pass-1")[0], 200)

    def test_owner_cannot_stop_himself_or_the_last_owner(self):
        me = self.owner.call("GET", "/api/state")[1]["user"]["id"]
        self.assertEqual(self.owner.call("POST", f"/api/users/{me}/active", {"active": False, "reason": "x"})[1]["error"], "cannot_stop_self")

    def test_reset_by_owner_forces_a_new_password_at_next_login(self):
        self.assertEqual(self.owner.call("POST", f"/api/users/{self.cid}/password", {"password": "reset-pass-1"})[0], 200)
        self.assertEqual(self.cash.call("GET", "/api/shift")[0], 401)                # old session ended
        self.cash.login("cash", "reset-pass-1")
        self.assertEqual(self.cash.call("GET", "/api/shift")[1]["error"], "must_change_password")
        self.assertEqual(self.cash.call("POST", "/api/account/password", {"old": "reset-pass-1", "new": "reset-pass-1"})[1]["error"], "same_password")
        self.cash.call("POST", "/api/account/password", {"old": "reset-pass-1", "new": "mine-again-1"})
        self.assertEqual(self.cash.call("GET", "/api/shift")[0], 200)

    def test_locked_screen_stays_locked_after_reload_until_the_same_password(self):
        self.cash.call("POST", "/api/account/lock", {})
        status, body = self.cash.call("GET", "/api/shift")
        self.assertEqual((status, body["error"]), (423, "screen_locked"))            # a page reload sends the same cookie
        self.assertEqual(self.cash.call("POST", "/api/account/unlock", {"password": "boss-pass-1"})[0], 401)
        self.assertEqual(self.cash.call("POST", "/api/account/unlock", {"password": "cash-pass-1"})[0], 200)
        self.assertEqual(self.cash.call("GET", "/api/shift")[0], 200)

    def test_owner_recovery_on_the_shop_computer_changes_only_the_password(self):
        self.owner.call("POST", "/api/products", {"code": "1", "name_ar": "أ", "name_en": "A", "kind": "grocery", "measure": "unit", "price": 100})
        out = shopctl.recover(self.data, "boss", "recovered-1")
        self.assertTrue(out["ok"])
        self.assertEqual(self.owner.call("GET", "/api/shift")[0], 401)               # old owner sessions ended
        self.assertEqual(self.owner.login("boss", "recovered-1")[0], 200)
        self.assertEqual(len(self.owner.call("GET", "/api/products")[1]), 1)         # nothing deleted
        store = Store(self.data, "shop")
        try:
            self.assertTrue(store.scalar("SELECT 1 FROM audit WHERE action='owner.recovery'"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
