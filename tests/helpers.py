"""Test fixtures: a fresh shop folder with owner, cashier and basic locations."""

import datetime as dt
import pathlib
import sys
import tempfile
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import auth, core  # noqa: E402
from server.barcode import gs1_check_digit  # noqa: E402
from server.db import Store  # noqa: E402
from server.services import Shop  # noqa: E402

CAIRO = dt.timezone(dt.timedelta(hours=3))
NOW = dt.datetime(2026, 10, 30, 10, 0, tzinfo=CAIRO)
PASSWORD = "test-pass-123"
auth.PBKDF2_ITER = 2000  # fast tests; production default stays in server/auth.py


def ean(body):
    """Valid GS1 code from a body without check digit."""
    return body + gs1_check_digit(body)


def key():
    return uuid.uuid4().hex


class ShopFixture:
    def make_shop(self, mode="shop"):
        core.set_clock(NOW)
        self.addCleanup(core.set_clock, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = pathlib.Path(tmp.name)
        self.store = Store(self.folder / "data", mode=mode)
        self.addCleanup(self.store.close)
        self.shop = Shop(self.store)
        a = self.shop.auth
        ids = self.store.tx(lambda: [a.create_user("owner", "Owner", "owner", PASSWORD),
                                     a.create_user("cash1", "Cashier 1", "cashier", PASSWORD),
                                     a.create_user("cash2", "Cashier 2", "cashier", PASSWORD),
                                     a.create_user("butcher", "Butcher", "butcher", PASSWORD),
                                     a.create_user("manager", "Manager", "manager", PASSWORD),
                                     a.create_user("counter", "Counter", "counter", PASSWORD)])
        self.owner = self.actor("owner")
        self.cash1 = self.actor("cash1")
        self.cash2 = self.actor("cash2", "till-2")
        self.butcher = self.actor("butcher")
        self.manager = self.actor("manager")
        self.counter = self.actor("counter")
        s, o = self.shop, self.owner
        self.shelf = s.add_location(o, "SH", "رف", "Shelf", "shelf", 1)
        self.fridge = s.add_location(o, "FR", "ثلاجة عرض", "Display fridge", "display_fridge", 1)
        self.prep = s.add_location(o, "PR", "ثلاجة تجهيز", "Prep fridge", "prep_fridge", 0)
        self.quarantine = s.add_location(o, "QA", "حجر فحص", "Quarantine", "quarantine", 0)
        return ids

    def actor(self, username, workstation="till-1"):
        user = self.store.one("SELECT * FROM users WHERE username=?", (username,))
        out = self.shop.auth.public(user)
        out["workstation"] = workstation
        return out

    def approval(self, username="manager"):
        return self.shop.verify_approval({"username": username, "password": PASSWORD})

    def grocery(self, code="G1", price=1500, barcodes=(ean("622123456789"),), carton=None):
        product = {"code": code, "name_ar": "عصير", "name_en": "Juice", "kind": "grocery", "measure": "unit",
                   "price": price, "barcodes": list(barcodes)}
        if carton:
            product["packagings"] = [{"name_ar": "كرتونة", "name_en": "Carton", "factor_num": 12,
                                      "barcodes": [carton]}]
        return self.shop.add_product(self.owner, product)

    def meat(self, code="M1", price=40000, kind="raw_meat", allergens=""):
        return self.shop.add_product(self.owner, {"code": code, "name_ar": "لحم " + code, "name_en": "Meat " + code,
                                                  "kind": kind, "measure": "g", "price": price, "allergens": allergens})

    def receive(self, product, qty, cost, location=None, expires="2026-11-05", **extra):
        line = {"product_id": product, "qty": qty, "line_cost": cost, "location_id": location or self.shelf,
                "expires": expires}
        line.update(extra)
        return self.shop.receive(self.owner, key(), {"lines": [line]})

    def open_shift(self, actor=None, amount=50000):
        return self.shop.open_shift(actor or self.cash1, key(), amount)
