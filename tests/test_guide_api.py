"""The in-app guide over HTTP: each person's course follows their role, progress is
saved per person on the server, and a lesson counts as done when the shop's own
records show the person did it (the training copy's starting delivery does not count)."""

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
        return self.call("POST", "/api/login", {"username": user, "password": "training-only", "workstation": "t-" + user})


class GuideApi(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.httpd = serve(pathlib.Path(tmp.name) / "training-data", "training", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.app.store.close)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]

    def test_course_follows_the_role_and_progress_is_per_person(self):
        cash, owner = Client(self.port), Client(self.port)
        cash.login("cashier"); owner.login("owner")
        st = cash.call("GET", "/api/guide/state")[1]
        self.assertEqual(st["role"], "cashier")
        ids = [i["id"] for i in st["course"]["items"]]
        self.assertEqual(ids[:4], ["first-look", "change-password", "open-shift", "first-sale"])
        self.assertNotIn("receive-goods", ids)                                   # not a cashier's job
        self.assertEqual(cash.call("POST", "/api/guide/progress", {"update": {"op": "done", "guide": "first-look"}})[0], 200)
        self.assertTrue(next(i for i in cash.call("GET", "/api/guide/state")[1]["course"]["items"] if i["id"] == "first-look")["done"])
        self.assertFalse(next(i for i in owner.call("GET", "/api/guide/state")[1]["course"]["items"] if i["id"] == "first-look")["done"])
        self.assertEqual(cash.call("POST", "/api/guide/progress", {"update": {"op": "done", "guide": "no-such"}})[0], 400)

    def test_a_lesson_is_done_when_the_records_show_it(self):
        cash = Client(self.port)
        cash.login("cashier")
        open_item = lambda: next(i for i in cash.call("GET", "/api/guide/state")[1]["course"]["items"] if i["id"] == "open-shift")
        self.assertFalse(open_item()["done"])
        cash.call("POST", "/api/shift/open", {"key": "guide-shift-1", "opening_float": 50000})
        self.assertTrue(open_item()["done"] and open_item()["auto"])
        owner = Client(self.port)
        owner.login("owner")
        recv = next(i for i in owner.call("GET", "/api/guide/state")[1]["course"]["items"] if i["id"] == "receive-goods")
        self.assertFalse(recv["done"])                                            # the seed delivery is not the learner's


if __name__ == "__main__":
    unittest.main()
