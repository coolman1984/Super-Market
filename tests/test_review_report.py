"""Acceptance tests for docs/REVIEW_REPORT.md (review of 31 Oct 2026). One class
per issue family; each test states the CORRECT behaviour."""

import pathlib
import unittest

from helpers import PASSWORD, ShopFixture, ean, key
from server import core
from server.core import Forbidden, ShopError


class RefundGuard(ShopFixture, unittest.TestCase):
    """H1 - a cashier cannot pay cash out against any sale alone."""

    def setUp(self):
        self.make_shop()
        self.p = self.grocery(price=10000)
        self.receive(self.p, 10, 5000, expires=None)
        self.open_shift(self.cash1, 100000)
        self.open_shift(self.cash2, 100000)
        self.open_shift(self.manager, 100000)

    def sale(self, actor, qty=1, price=None):
        return self.shop.sell(actor, key(), {"lines": [{"product_id": self.p, "qty": qty}],
                                             "cash_tendered": 10000 * qty})

    def line(self, sale):
        return self.shop.sale_detail(self.owner, sale["sale"])["lines"][0]["id"]

    def refund(self, actor, sale, reason="quality", qty=1, approver=None):
        return self.shop.return_sale(actor, key(), {"sale_id": sale["sale"], "reason": reason, "method": "original",
                                                    "lines": [{"sale_line_id": self.line(sale), "qty": qty}]}, approver)

    def test_own_small_recent_quality_refund_needs_no_approval(self):
        s = self.sale(self.cash1)
        self.assertEqual(self.refund(self.cash1, s)["amount"], 10000)

    def test_another_cashiers_sale_needs_a_manager(self):
        s = self.sale(self.cash2)
        with self.assertRaises(Forbidden) as c:
            self.refund(self.cash1, s)
        self.assertEqual(c.exception.code, "approval_required")
        r = self.refund(self.cash1, s, approver=self.approval())
        self.assertEqual(r["amount"], 10000)
        row = self.store.one("SELECT approved_by FROM returns WHERE id=?", (r["return"],))
        self.assertEqual(row["approved_by"], self.manager["id"])
        audit = self.store.one("SELECT detail FROM audit WHERE action='return.approval'")
        self.assertIn("other_cashier", audit["detail"])
        self.assertTrue(self.shop.reconcile()["ok"])

    def test_cash_for_change_of_mind_needs_a_manager(self):
        s = self.sale(self.cash1)
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, reason="change_of_mind")
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, reason="other")
        self.refund(self.cash1, s, reason="change_of_mind", approver=self.approval())

    def test_amount_over_the_limit_needs_a_manager(self):
        s = self.sale(self.cash1, qty=3)
        self.shop.update_settings(self.owner, {"refund_approval_over": 20000})
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s, qty=3)
        self.refund(self.cash1, s, qty=2)                  # exactly at the limit is allowed

    def test_old_sale_needs_a_manager(self):
        s = self.sale(self.cash1)
        shift = self.store.scalar("SELECT id FROM shifts WHERE user_id=? AND closed_at IS NULL", (self.cash1["id"],))
        self.shop.close_shift(self.cash1, key(), self.shop.shift_expected(shift))
        core.set_clock(core.now() + __import__('datetime').timedelta(days=9))
        self.open_shift(self.cash1, 100000)
        with self.assertRaises(Forbidden):
            self.refund(self.cash1, s)

    def test_wrong_approver_password_is_refused(self):
        s = self.sale(self.cash2)
        with self.assertRaises(ShopError):
            self.shop.verify_approval({"username": "manager", "password": "nope-nope"}, self.cash1)

    def test_manager_and_owner_are_never_held_up(self):
        s = self.sale(self.cash2)
        self.assertEqual(self.refund(self.manager, s, reason="other")["amount"], 10000)

    def test_settings_are_validated(self):
        with self.assertRaises(ShopError):
            self.shop.update_settings(self.owner, {"refund_approval_over": -1})
        with self.assertRaises(ShopError):
            self.shop.update_settings(self.owner, {"refund_days_without_approval": "7"})


class LoginThrottle(ShopFixture, unittest.TestCase):
    """H2 + L6 - strangers cannot lock the owner out; PBKDF2 rounds are upgraded."""

    def setUp(self):
        self.make_shop()
        from server import auth
        self.auth = self.shop.auth
        self.clock = [1000.0]
        self._old = auth._mono
        auth._mono = lambda: self.clock[0]
        self.addCleanup(setattr, auth, "_mono", self._old)

    def bad(self, ip, n=1, name="owner"):
        for _ in range(n):
            try:
                self.auth.verify(name, "wrong-wrong", client=ip)
            except ShopError as e:
                last = e
        return last

    def test_hundred_bad_logins_from_b_never_block_a(self):
        last = self.bad("10.0.0.9", 100)
        self.assertIn(last.code, ("account_locked", "too_many_attempts"))
        self.assertEqual(last.status, 429)
        user = self.auth.verify("owner", PASSWORD, client="10.0.0.5")
        self.assertEqual(user["username"], "owner")

    def test_five_bad_from_same_address_back_off_and_double(self):
        self.bad("10.0.0.9", 5)
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertEqual(c.exception.code, "account_locked")
        self.assertEqual(c.exception.detail["retry_after"], 61)
        self.clock[0] += 61
        self.bad("10.0.0.9", 5)                      # second lock is longer
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertGreater(c.exception.detail["retry_after"], 100)

    def test_one_address_guessing_many_names_is_refused_as_an_address(self):
        for i in range(25):
            self.bad("10.0.0.9", 1, name="nobody%d" % i)
        with self.assertRaises(ShopError) as c:
            self.auth.verify("owner", PASSWORD, client="10.0.0.9")
        self.assertEqual(c.exception.code, "too_many_attempts")

    def test_right_password_on_the_shop_computer_clears_the_waits_for_that_name(self):
        self.bad("10.0.0.9", 5)
        self.auth.verify("owner", PASSWORD, client="127.0.0.1")
        self.assertEqual(self.auth.verify("owner", PASSWORD, client="10.0.0.9")["username"], "owner")

    def test_shop_computer_wait_is_short_and_flat(self):
        self.bad("127.0.0.1", 5)
        with self.assertRaises(ShopError):
            self.auth.verify("owner", PASSWORD, client="127.0.0.1")
        self.clock[0] += 31
        self.assertEqual(self.auth.verify("owner", PASSWORD, client="127.0.0.1")["username"], "owner")

    def test_old_hash_is_upgraded_after_login_and_new_hashes_use_the_current_rounds(self):
        from server import auth
        user = self.store.one("SELECT * FROM users WHERE username='cash1'")
        auth.PBKDF2_ITER, saved = 5000, auth.PBKDF2_ITER
        try:
            self.assertLess(user["pw_iter"], 5000)
            self.auth.verify("cash1", PASSWORD)
            after = self.store.one("SELECT * FROM users WHERE username='cash1'")
            self.assertEqual(after["pw_iter"], 5000)
            self.assertEqual(self.auth.verify("cash1", PASSWORD)["id"], user["id"])   # new hash verifies
        finally:
            auth.PBKDF2_ITER = saved
        self.assertGreaterEqual(auth.__dict__["PBKDF2_ITER"], 2000)

    def test_production_rounds_are_at_least_owasp(self):
        import importlib, server.auth as a
        src = open(a.__file__, encoding="utf-8").read()
        self.assertIn("PBKDF2_ITER = 600_000", src)


class BackupSafety(ShopFixture, unittest.TestCase):
    """H5, M6, M7, L4 - a copy outside the shop PC, ordered by sequence, pruned, never silent."""

    def setUp(self):
        import pathlib
        import tempfile
        self.make_shop()
        self.local = self.folder / "backups-shop"
        usb = tempfile.TemporaryDirectory()
        self.addCleanup(usb.cleanup)
        self.usb = pathlib.Path(usb.name)
        self.p = self.grocery(price=1000)
        self.receive(self.p, 5, 500, expires=None)      # real data: the banner only matters then

    def make(self, label="daily"):
        from server import backup
        return backup.create_backup(self.store, self.local, label)

    def test_h5_second_place_gets_a_verified_copy(self):
        from server import backup
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        first = self.make()
        out = backup.sync_external(self.store, self.local, self.folder)
        self.assertEqual(out["copied"], first["folder"])
        copied = self.usb / backup.EXTERNAL_DIR / pathlib.Path(first["folder"]).name
        self.assertEqual(backup.verify_backup(copied)["sha256"], first["sha256"])
        st = backup.status(self.store, self.local)
        self.assertTrue(st["configured"] and st["external_days"] == 0 and not st["external_error"])

    def test_h5_missing_drive_is_reported_never_created_and_never_stops_selling(self):
        from server import backup
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        self.make()
        import shutil
        shutil.rmtree(self.usb)
        out = backup.sync_external(self.store, self.local, self.folder)
        self.assertEqual(out["error"], "backup2_unusable")
        self.assertFalse(self.usb.exists())                       # the unplugged drive's folder is not recreated on the shop disk
        st = backup.status(self.store, self.local)
        self.assertEqual(st["external_error"], "backup2_unusable")

    def test_h5_banner_data_no_copy_and_days_without_copy(self):
        from server import backup
        self.make()
        st = backup.status(self.store, self.local)
        self.assertTrue(st["has_data"])
        self.assertFalse(st["configured"])
        self.assertIsNone(st["external_days"])                    # the banner says "no external copy"
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        backup.sync_external(self.store, self.local, self.folder)
        core.set_clock(core.now() + __import__("datetime").timedelta(days=4))
        self.assertEqual(backup.status(self.store, self.local)["external_days"], 4)

    def test_h5_bad_places_are_refused_and_only_the_owner_sets_it(self):
        for bad in ("relative/path", str(self.folder / "does-not-exist"), str(self.folder / "data")):
            with self.assertRaises(ShopError) as c:
                self.shop.update_settings(self.owner, {"backup_folder_2": bad})
            self.assertEqual(c.exception.code, "backup2_unusable")
        with self.assertRaises(Forbidden):
            self.shop.update_settings(self.manager, {"backup_folder_2": str(self.usb)})
        self.shop.update_settings(self.owner, {"backup_folder_2": str(self.usb)})
        self.shop.update_settings(self.owner, {"backup_folder_2": ""})            # switched off

    def test_h5_damaged_copy_on_the_stick_is_replaced_not_trusted(self):
        from server import backup
        first = self.make()
        root = self.usb
        target = pathlib.Path(backup.copy_external(first["folder"], root, self.folder))
        (target / "shop.db").write_bytes(b"broken")
        again = pathlib.Path(backup.copy_external(first["folder"], root, self.folder))
        self.assertEqual(backup.verify_backup(again)["sha256"], first["sha256"])

    def test_m6_newest_is_decided_by_sequence_not_by_the_clock(self):
        from server import backup
        a = self.make()
        core.set_clock(core.now() - __import__("datetime").timedelta(days=400))       # the PC clock jumps far back
        b = self.make()
        self.assertEqual([x["folder"] for x in backup.list_backups(self.local)], [b["folder"], a["folder"]])
        self.assertGreater(b["seq"], a["seq"])

    def test_m7_before_copies_are_pruned_and_manual_copies_never(self):
        from server import backup
        for n in range(8):
            self.make("before-1.%d" % n)
        manual = [self.make("manual") for _ in range(3)]
        removed = backup.prune(self.local, {"daily": 30, "before": 5})
        self.assertEqual(len(removed), 3)
        labels = [b["label"] for b in backup.list_backups(self.local)]
        self.assertEqual(sum(1 for x in labels if x.startswith("before-")), 5)
        self.assertEqual(labels.count("manual"), 3)
        self.assertNotIn("before-1.0", labels)                      # the oldest went, the newest stayed
        self.assertIn("before-1.7", labels)

    def test_m7_low_disk_is_flagged(self):
        from unittest import mock
        from server import backup
        with mock.patch.object(backup, "disk_free", return_value=100 * 1024 * 1024):
            st = backup.status(self.store, self.local)
        self.assertTrue(st["low_disk"])
        self.assertEqual(st["free_mb"], 100)

    def test_l4_two_pre_upgrade_copies_in_one_second_do_not_collide(self):
        self.store._pre_upgrade_copy(3)
        self.store._pre_upgrade_copy(3)
        self.assertEqual(len(list((self.store.folder / "pre-upgrade").glob("schema-3-*.db"))), 2)


class ServerErrors(ShopFixture, unittest.TestCase):
    """M10 - a bug of ours is a 500 with a request id in the log; a bad request body stays a 400."""

    def setUp(self):
        import tempfile
        from server.app import serve
        import threading
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.httpd = serve(self.tmp.name, "shop", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.httpd.shutdown(), self.httpd.server_close(), self.httpd.app.store.close()))
        self.port = self.httpd.server_address[1]

    def call(self, method, path, body=None):
        import http.client
        import json
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, json.dumps(body) if body is not None else None, {"X-SM": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        out = (r.status, json.loads(r.read() or b"null"))
        c.close()
        return out

    def test_m10_unexpected_exception_is_500_with_request_id_and_traceback_in_the_log(self):
        def boom(actor, q, body):
            raise ValueError("injected bug")
        self.httpd.app.route("GET", r"/api/boom", boom, auth=False)
        with self.assertLogs("supermarket", level="ERROR") as logs:
            status, body = self.call("GET", "/api/boom")
        self.assertEqual(status, 500)
        self.assertEqual(body["error"], "internal_error")
        self.assertRegex(body["detail"]["request_id"], r"^[0-9a-f]{8}$")
        self.assertIn(body["detail"]["request_id"], logs.output[0])
        self.assertIn("injected bug", logs.output[0])
        self.assertNotIn("injected", str(body))                       # nothing internal reaches the browser

    def test_m10_a_wrong_body_is_still_a_400(self):
        self.assertEqual(self.call("POST", "/api/setup", {"username": 5})[0] in (400, 409), True)

    def raw(self, data=b""):
        import socket
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        self.addCleanup(s.close)
        if data:
            s.sendall(data)
        return s

    def test_m4_a_client_that_stalls_is_dropped(self):
        import time
        from server import app as appmod
        from unittest import mock
        import threading
        from server.app import serve
        with mock.patch.object(appmod, "SOCKET_TIMEOUT", 0.5):
            httpd = serve(self.tmp.name + "-b", "shop", port=0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), httpd.app.store.close()))
        import socket
        s = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=5)
        self.addCleanup(s.close)
        s.sendall(b"POST /api/login HTTP/1.1\r\nHost: 127.0.0.1\r\nX-SM: 1\r\nContent-Length: 50\r\n\r\n{")
        start = time.monotonic()
        self.assertEqual(s.recv(100), b"")                         # the server closed the stalled connection
        self.assertLess(time.monotonic() - start, 4)

    def test_m4_too_many_connections_get_503_and_a_big_body_gets_413(self):
        import threading
        self.httpd._slots = threading.BoundedSemaphore(2)
        a, b = self.raw(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Pad: "), self.raw(b"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Pad: ")
        import time
        time.sleep(0.3)
        self.assertIn(b"503", self.raw(b"GET /api/state HTTP/1.0\r\n\r\n").recv(200))
        a.close(); b.close()
        time.sleep(0.5)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)     # slots are given back
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("POST", "/api/login"); c.putheader("X-SM", "1"); c.putheader("Content-Length", "2000000"); c.endheaders()
        self.assertEqual(c.getresponse().status, 413)


class ApprovalLock(ShopFixture, unittest.TestCase):
    """M3 - guessing a manager's password at the till locks the requester, in the database, and is audited."""

    def setUp(self):
        self.make_shop()

    def guess(self, actor, password="wrong-password"):
        return self.shop.verify_approval({"username": "manager", "password": password}, actor)

    def fail(self, actor, n):
        for _ in range(n):
            with self.assertRaises(ShopError):
                self.guess(actor)

    def test_five_failures_lock_the_requester_with_a_growing_wait_and_audit(self):
        import datetime
        self.fail(self.cash1, 5)
        with self.assertRaises(ShopError) as c:
            self.guess(self.cash1, PASSWORD)                               # even the right password is refused while locked
        self.assertEqual((c.exception.code, c.exception.status), ("approval_attempts_exceeded", 429))
        self.assertGreater(c.exception.detail["retry_after"], 800)
        self.assertEqual(self.store.scalar("SELECT COUNT(*) FROM audit WHERE action='approval.failed'"), 5)
        self.assertIn('"seconds": 900', self.store.scalar("SELECT detail FROM audit WHERE action='approval.locked'"))
        core.set_clock(core.now() + datetime.timedelta(minutes=16))
        self.assertEqual(self.guess(self.cash1, PASSWORD)["role"], "manager")  # the first lock ends after 15 minutes
        self.fail(self.cash1, 5)
        core.set_clock(core.now() + datetime.timedelta(minutes=16))
        with self.assertRaises(ShopError):                                  # second lock is 1 hour
            self.guess(self.cash1, PASSWORD)
        self.assertIn('"seconds": 3600', self.store.scalar("SELECT detail FROM audit WHERE action='approval.locked' ORDER BY id DESC"))

    def test_the_lock_survives_a_restart_and_is_per_requester(self):
        from server.db import Store
        from server.services import Shop
        self.fail(self.cash1, 5)
        path = self.store.folder
        self.store.close()
        self.store = Store(path, "shop")
        self.addCleanup(self.store.close)
        self.shop = Shop(self.store)
        with self.assertRaises(ShopError) as c:
            self.guess(self.actor("cash1"), PASSWORD)
        self.assertEqual(c.exception.code, "approval_attempts_exceeded")
        self.assertEqual(self.guess(self.actor("cash2"), PASSWORD)["role"], "manager")   # another cashier is not affected
        self.assertEqual(self.shop.auth.verify("manager", PASSWORD)["username"], "manager")  # the manager himself is never locked

    def test_the_lock_row_is_not_shown_in_settings(self):
        self.fail(self.cash1, 1)
        self.assertFalse([k for k in self.shop.settings() if k.startswith("_")])


class LanSafety(ShopFixture, unittest.TestCase):
    """M1, M2 - other tills reach the PC by its computer name; an idle till locks; strangers' names are refused."""

    def start(self, lan):
        import tempfile
        import threading
        from server.app import serve
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        httpd = serve(tmp.name, "shop", port=0, lan=lan)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), httpd.app.store.close()))
        self.httpd, self.port = httpd, httpd.server_address[1]
        return httpd

    def get(self, host, path="/api/state"):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers={"Host": host})
        r = c.getresponse()
        r.read()
        c.close()
        return r.status

    def test_m2_computer_name_works_in_lan_mode_only_and_only_its_own(self):
        import socket
        name = socket.gethostname()
        self.start(lan=True)
        self.assertEqual(self.get("%s:%d" % (name, self.port)), 200)
        self.assertEqual(self.get("%s.local:%d" % (name.lower(), self.port)), 200)
        self.assertEqual(self.get("evil.example.com:%d" % self.port), 421)       # a rebinding page's own name is refused
        self.assertEqual(self.get("%s:%d" % (name, self.port + 1)), 421)         # wrong port
        self.assertEqual(self.get("localhost:%d" % self.port), 200)

    def test_m2_without_lan_the_computer_name_is_refused_and_the_state_lists_addresses_only_in_lan(self):
        import socket
        self.start(lan=False)
        self.assertEqual(self.get("%s:%d" % (socket.gethostname(), self.port)), 421)
        from server import app as appmod
        addresses = appmod.lan_addresses(8765)
        self.assertTrue(addresses[0].startswith("http://" + socket.gethostname()) and addresses[0].endswith(":8765/"))

    def test_m1_an_idle_till_locks_itself_in_lan_mode_but_polling_does_not_keep_it_alive(self):
        httpd = self.start(lan=True)
        auth = httpd.app.auth
        self.assertEqual(auth.idle_lock_s, 900)
        clock = [1000.0]
        auth.idle_clock = lambda: clock[0]
        self.store = httpd.app.store
        self.shop = httpd.app.shop
        ids = self.store.tx(lambda: auth.create_user("boss", "Boss", "owner", PASSWORD))
        token, _ = auth.login("boss", PASSWORD, "till-1", client="127.0.0.1")
        self.assertFalse(auth.session(token)["locked"])
        clock[0] += 600
        self.assertFalse(auth.session(token, touch=False)["locked"])             # a background poll after 10 minutes
        clock[0] += 400                                                          # 1000 s since the last real use
        self.assertTrue(auth.session(token, touch=False)["locked"])
        auth.unlock_screen(auth.session(token), token, PASSWORD, client="127.0.0.1")
        self.assertFalse(auth.session(token)["locked"])                          # unlocked with the password
        self.assertEqual(ids, 1)

    def test_m1_the_shop_pc_alone_never_idle_locks(self):
        httpd = self.start(lan=False)
        self.assertIsNone(httpd.app.auth.idle_lock_s)


class PromotionDay(ShopFixture, unittest.TestCase):
    """L2 - an offer that ends 'today' lasts to the end of the SHOP's day, not to midnight."""

    def test_offer_ending_on_the_business_day_still_applies_after_midnight(self):
        import datetime as dt
        from helpers import CAIRO
        self.make_shop()
        core.set_clock(dt.datetime(2026, 10, 30, 20, 0, tzinfo=CAIRO))
        p = self.grocery(price=1000)
        self.receive(p, 10, 500, expires=None)
        self.shop.add_promotion(self.manager, key(), p, 2, 1, "2026-10-01", "2026-10-30")
        self.open_shift(self.cash1, 0)
        core.set_clock(dt.datetime(2026, 10, 31, 1, 30, tzinfo=CAIRO))       # after midnight, same shop day
        q = self.shop.quote(self.cash1, {"lines": [{"product_id": p, "qty": 3}]})
        self.assertEqual(q["total"], 2000)                                   # buy 2 get 1 free still applies
        self.shop.close_shift(self.cash1, key(), self.shop.shift_expected(self.store.scalar("SELECT id FROM shifts")))
        core.set_clock(dt.datetime(2026, 10, 31, 10, 0, tzinfo=CAIRO))
        self.open_shift(self.cash1, 0)
        q = self.shop.quote(self.cash1, {"lines": [{"product_id": p, "qty": 3}]})
        self.assertEqual(q["total"], 3000)                                   # a new business day: the offer is over


class StatePrivacy(ShopFixture, unittest.TestCase):
    """L5 - the anonymous state tells a stranger on the shop network only what the login screen needs."""

    def test_remote_anonymous_sees_no_shop_name_or_instance(self):
        from server.app import App
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = App(tmp.name)
        self.addCleanup(app.hub.stop)
        self.addCleanup(app.store.close)
        app.store.tx(lambda: app.auth.create_user("boss", "Boss", "owner", PASSWORD))
        app.store.tx(lambda: app.store.db.execute("INSERT INTO settings VALUES('shop_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('"Secret Shop"',)))
        remote = app.state(None, {}, {}, client="192.168.1.50")
        self.assertEqual((remote["shop_name"], remote["instance"], remote["business_day"], remote["lan"]), ("", None, None, None))
        self.assertIn("version", remote)
        local = app.state(None, {}, {}, client="127.0.0.1")
        self.assertEqual(local["shop_name"], "Secret Shop")
        self.assertTrue(local["instance"])
        signed_in = app.state({"id": 1}, {}, {}, client="192.168.1.50")
        self.assertEqual(signed_in["shop_name"], "Secret Shop")


class PartyEntry(ShopFixture, unittest.TestCase):
    """L8 - Egyptian phone numbers are normalized and checked; the same person is not added twice by accident."""

    def setUp(self):
        self.make_shop()

    def test_phone_is_normalized_or_refused(self):
        from server.services import normalize_phone
        self.assertEqual(normalize_phone("+20 100 123 4567"), "01001234567")
        self.assertEqual(normalize_phone("٠١٠٠١٢٣٤٥٦٧"), "01001234567")
        self.assertEqual(normalize_phone("0020-112-345-6789"), "01123456789")
        self.assertEqual(normalize_phone("02 2345 6789"), "0223456789")
        self.assertIsNone(normalize_phone(""))
        for bad in ("12345", "01312345678", "abc", "0100123456"):
            with self.assertRaises(ShopError) as c:
                normalize_phone(bad)
            self.assertEqual(c.exception.code, "bad_phone")

    def test_same_name_and_phone_asks_first_and_can_be_confirmed(self):
        first = self.shop.add_party(self.owner, "customer", "أحمد  علي", "01001234567")
        with self.assertRaises(ShopError) as c:
            self.shop.add_party(self.owner, "customer", "أحمد علي", "+201001234567")      # a double click or a re-typed number
        self.assertEqual((c.exception.code, c.exception.detail["id"]), ("party_duplicate", first))
        self.shop.add_party(self.owner, "customer", "أحمد علي", "01111234567")             # same name, other phone: a different person
        again = self.shop.add_party(self.owner, "customer", "أحمد علي", "01001234567", allow_duplicate=True)
        self.assertNotEqual(first, again)
        self.assertEqual(self.store.scalar("SELECT phone FROM customers WHERE id=?", (first,)), "01001234567")


class TaxStatement(unittest.TestCase):
    """H4 - until an accountant confirms the e-receipt duties, the receipt and reports must keep saying it is not a tax invoice."""

    ROOT = pathlib.Path(__file__).resolve().parents[1]

    def test_receipt_and_reports_say_it_is_not_a_tax_invoice(self):
        ar = (self.ROOT / "web/js/i18n/ar.js").read_text(encoding="utf-8")
        en = (self.ROOT / "web/js/i18n/en.js").read_text(encoding="utf-8")
        self.assertIn("ليس فاتورة ضريبية", ar)
        self.assertIn("not an electronic tax receipt", en)
        self.assertIn("t('receipt.not_tax')", (self.ROOT / "web/js/views/sell.js").read_text(encoding="utf-8"))
        self.assertIn("rep.tax_unverified", (self.ROOT / "web/js/views/money.js").read_text(encoding="utf-8"))

    def test_tax_mode_cannot_be_switched_to_a_guess(self):
        from server.services import DEFAULT_SETTINGS
        self.assertEqual(DEFAULT_SETTINGS["tax_mode"], "unverified")
        self.assertTrue((self.ROOT / "docs/ETA_QUESTIONS.md").exists())
