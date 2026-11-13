"""Sample shop: a month of believable trading, made by the REAL services (never by writing rows).

Why it is built this way:
* Every sale, receipt, cut, count and payment goes through the same code the till uses, with the clock set
  to the past day by day. So the numbers obey the real rules, and a rule that breaks shows up here first.
* It loads only into an EMPTY shop (no product, no operation), so everything operational is sample.
* The shop's own accounts, settings and the five standard places are never touched. The sample adds
  inactive staff accounts (nobody can sign in with them until the owner resets a password), and a few
  places and policy settings, all remembered in one hidden record so "remove" can put things back.
* Removing is one transaction on the same database: posted rows are protected by triggers, so the
  protection is lifted inside that transaction and restored before it commits. A full copy is taken first.
"""

import datetime as dt
import json
import math
import random
import secrets
import uuid

from . import backup
from . import core
from .barcode import gs1_check_digit
from .core import Conflict, ShopError, add_days, now_iso
from .db import Store

META_KEY = "_sample"                 # hidden from the settings screen (keys starting with "_")
USER_PREFIX = "sample_"
TAG = " (عيّنة)"
DAYS = 30                            # days of history before today
SEED = 20261103                      # same numbers every time: a problem found once is found again

# tables that are NOT emptied when the sample is removed (everything else is operational data)
KEEP_TABLES = {"settings", "users", "sessions", "locations", "audit", "label_templates", "scanner_profiles",
               "schema_migrations", "sync_meta", "sync_changes"}

# ---------------------------------------------------------------- catalogue
# key, code, name_ar, name_en, price EGP, cost EGP, shelf days, units/day, supplier, place, carton units
GROCERY = [
    ("rice", "أرز مصري ١ كجم", "Egyptian rice 1kg", 38, 32, 365, 6, "dry", "SH", 0),
    ("sugar", "سكر ١ كجم", "Sugar 1kg", 45, 39, 365, 6, "dry", "SH", 0),
    ("pasta", "مكرونة ٤٠٠ جم", "Pasta 400g", 18, 14.5, 540, 8, "dry", "SH", 20),
    ("lentil", "عدس أصفر ٥٠٠ جم", "Yellow lentils 500g", 32, 26, 365, 3, "dry", "SH", 0),
    ("fava", "فول مدمس معلب ٤٠٠ جم", "Canned fava 400g", 22, 17, 720, 5, "dry", "SH", 24),
    ("oil", "زيت عباد الشمس ١ لتر", "Sunflower oil 1L", 98, 86, 540, 5, "dry", "SH", 12),
    ("ghee", "سمنة نباتية ٥٠٠ جم", "Vegetable ghee 500g", 70, 60, 365, 2, "dry", "SH", 0),
    ("tomato", "صلصة طماطم ٣٠٠ جم", "Tomato paste 300g", 14, 10.5, 540, 4, "dry", "SH", 24),
    ("salt", "ملح ٧٠٠ جم", "Salt 700g", 9, 6.5, 900, 3, "dry", "SH", 0),
    ("flour", "دقيق ١ كجم", "Flour 1kg", 28, 23, 240, 4, "dry", "SH", 0),
    ("tea", "شاي سايب ٢٥٠ جم", "Loose tea 250g", 92, 80, 540, 4, "dry", "SH", 0),
    ("teabag", "شاي أكياس ٢٥ كيس", "Tea bags x25", 58, 49, 540, 3, "dry", "SH", 0),
    ("coffee", "نسكافيه ٥٠ جم", "Instant coffee 50g", 85, 72, 540, 2, "dry", "SH", 0),
    ("milk", "لبن كامل الدسم ١ لتر", "Full-fat milk 1L", 52, 45, 7, 10, "dairy", "FR", 12),
    ("yogurt", "زبادي ١٢٥ جم", "Yoghurt 125g", 8, 6.2, 14, 8, "dairy", "FR", 24),
    ("feta", "جبنة بيضاء ٥٠٠ جم", "White cheese 500g", 75, 63, 21, 3, "dairy", "FR", 0),
    ("eggs", "بيض (طبق ٣٠)", "Eggs (tray of 30)", 160, 142, 21, 5, "dairy", "SH", 0),
    ("butter", "زبدة ٢٠٠ جم", "Butter 200g", 70, 59, 60, 2, "dairy", "FR", 0),
    ("water", "مياه معدنية ١٫٥ لتر", "Mineral water 1.5L", 8, 5.5, 540, 20, "drinks", "SH", 6),
    ("cola", "مشروب غازي ١ لتر", "Cola 1L", 22, 17, 180, 10, "drinks", "SH", 12),
    ("juice", "عصير مانجو ١ لتر", "Mango juice 1L", 38, 30, 120, 6, "drinks", "SH", 12),
    ("energy", "مشروب طاقة ٢٥٠ مل", "Energy drink 250ml", 30, 23, 365, 5, "drinks", "SH", 24),
    ("chips", "شيبسي ٣٧ جم", "Potato chips 37g", 7, 5.2, 150, 25, "snacks", "SH", 24),
    ("biscuit", "بسكويت بالشوكولاتة", "Chocolate biscuits", 12, 9, 240, 9, "snacks", "SH", 36),
    ("choco", "شوكولاتة ٤٠ جم", "Chocolate bar 40g", 15, 11.5, 300, 10, "snacks", "SH", 24),
    ("bread", "عيش توست", "Sliced bread", 28, 22, 5, 8, "snacks", "SH", 0),
    ("detergent", "مسحوق غسيل ١ كجم", "Washing powder 1kg", 85, 70, 720, 3, "home", "SH", 12),
    ("dish", "سائل أطباق ٧٥٠ مل", "Dish liquid 750ml", 40, 31, 720, 3, "home", "SH", 12),
    ("tissue", "مناديل ورقية", "Tissues", 25, 19, 1000, 6, "home", "SH", 0),
    ("soap", "صابون استحمام", "Bath soap", 15, 11, 1000, 6, "home", "SH", 72),
    ("shampoo", "شامبو ٣٢٠ مل", "Shampoo 320ml", 78, 62, 720, 2, "home", "SH", 12),
    ("paste", "معجون أسنان", "Toothpaste", 45, 36, 720, 3, "home", "SH", 24),
]
SUPPLIERS = {
    "dairy": ("مورد الألبان والبيض", "01000000101"), "dry": ("مورد المواد الغذائية الجافة", "01000000102"),
    "drinks": ("مورد المشروبات", "01000000103"), "meat": ("مورد اللحوم والدواجن", "01000000104"),
    "home": ("مورد المنظفات والعناية", "01000000105"), "snacks": ("مورد التسالي والمخبوزات", "01000000106"),
}
CUSTOMERS = [("أم محمد — جارة", "01000000201", 200000), ("مطعم الحمد", "01000000202", 800000),
             ("مقهى الحاج سيد", "01000000203", 500000), ("مدرسة النور", "01000000204", 1000000),
             ("عيادة د. منى", "01000000205", 300000)]
STAFF = [("cashier1", "كاشير ١ — محمود", "cashier", "till-1"), ("cashier2", "كاشير ٢ — منى", "cashier", "till-2"),
         ("butcher", "الجزار — عم صابر", "butcher", "butcher"), ("counter", "أمين الجرد — هاني", "counter", "store"),
         ("receiver", "مسؤول الاستلام — خالد", "receiver", "store"), ("manager", "المدير — أستاذ عادل", "manager", "office")]
MEAT = [  # key, name_ar, name_en, price EGP/kg
    ("carcass", "لحم بتلو كندوز بالعظم", "Veal carcass bone-in", 380), ("boneless", "لحم بتلو صافي", "Veal boneless", 520),
    ("mince", "لحم مفروم", "Minced veal", 440), ("bones", "عظم للشوربة", "Soup bones", 60),
    ("liver", "كبدة بتلو طازجة", "Fresh veal liver", 380), ("chicken", "فراخ بيضاء طازجة", "Fresh chicken", 145),
    ("breast", "صدور فراخ", "Chicken breast", 210), ("frozen", "فراخ مجمدة", "Frozen chicken", 130)]
MEAT_COST = {"carcass": 300, "liver": 310, "chicken": 122, "breast": 175, "frozen": 108}


def ean(body):
    return body + gs1_check_digit(body)


def egp(pounds):
    return int(round(pounds * 100))


# ---------------------------------------------------------------- the hidden record
def read_meta(store):
    row = store.db.execute("SELECT value FROM settings WHERE key=?", (META_KEY,)).fetchone()
    return json.loads(row[0]) if row else None


def _write_meta(store, meta):
    store.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (META_KEY, json.dumps(meta, ensure_ascii=False)))


def status(store):
    """What the settings card needs. Read only."""
    meta = read_meta(store)
    empty = not store.scalar("SELECT COUNT(*) FROM ops") and not store.scalar("SELECT COUNT(*) FROM products")
    out = {"loaded": bool(meta), "complete": bool(meta and meta.get("complete")), "can_load": store.mode == "shop" and empty and not meta,
           "shop_mode": store.mode == "shop"}
    if meta:
        out.update(loaded_at=meta.get("loaded_at"), first_day=meta.get("first_day"), last_day=meta.get("last_day"),
                   mine=meta.get("counts"), added_after=_added_after(store, meta))
    return out


def _added_after(store, meta):
    """Things made after the sample was loaded: they are deleted together with it, so the owner is told."""
    return {"operations": store.scalar("SELECT COUNT(*) FROM ops WHERE id>?", (meta.get("last_op", 0),)),
            "products": store.scalar("SELECT COUNT(*) FROM products WHERE id>?", (meta.get("last_product", 0),)),
            "parties": store.scalar("SELECT COUNT(*) FROM suppliers WHERE id>?", (meta.get("last_supplier", 0),))
            + store.scalar("SELECT COUNT(*) FROM customers WHERE id>?", (meta.get("last_customer", 0),))}


# ---------------------------------------------------------------- load
class Clock:
    """Moves the shop clock through the past; always put back."""

    def __init__(self):
        self.tz = core.SHOP_TZ or dt.datetime.now().astimezone().tzinfo

    def at(self, day, hour, minute=0, second=0):
        d = dt.date.fromisoformat(day)
        moment = dt.datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=self.tz)
        core.set_clock(moment)
        return moment


def load(shop, owner):
    """Fill an empty shop with a month of trading. Returns a short summary. Raises ShopError."""
    store = shop.store
    if store.mode != "shop":
        raise ShopError("sample_shop_only")
    st = status(store)
    if st["loaded"]:
        raise ShopError("sample_already_loaded")
    if not st["can_load"]:
        raise ShopError("sample_needs_empty_shop")
    with store.lock:
        if store.clock_behind():
            raise ShopError("day_closed")
        sync = store.db.execute("PRAGMA synchronous").fetchone()[0]
        store.db.execute("PRAGMA synchronous=NORMAL")      # a month of tills in seconds; the shop's setting returns below
        try:
            return _Sim(shop, owner).run()
        finally:
            core.set_clock(None)
            store.db.execute("PRAGMA synchronous=%d" % sync)


class _Sim:
    def __init__(self, shop, owner):
        self.shop, self.store, self.owner = shop, shop.store, owner
        self.rnd = random.Random(SEED)
        self.clock = Clock()
        self.p = {}            # key -> product id
        self.info = {}         # product id -> dict
        self.sup = {}          # key -> supplier id
        self.cust = []         # customer ids
        self.loc = {}          # code -> location id
        self.users = {}        # username -> actor
        self.counts = {"sales": 0, "receipts": 0, "returns": 0, "cuts": 0, "kofta": 0, "waste": 0, "counts": 0}
        self.recent_sales = []
        self.invoice_no = 0
        self.price_drift = {}

    def key(self):
        return uuid.uuid4().hex

    # ------------------------------------------------------------ setup of master data
    def run(self):
        shop, store = self.shop, self.store
        today_s = self.today = core.today()
        first = add_days(today_s, -DAYS)
        loaded_at = core.now_iso()
        self.clock.at(first, 7, 0)
        meta = {"version": 1, "loaded_at": loaded_at, "first_day": first, "last_day": add_days(today_s, -1), "complete": False,
                "audit_from": store.scalar("SELECT COALESCE(MAX(id),0) FROM audit"),
                "prev_settings": {k: (store.setting(k) if store.scalar("SELECT 1 FROM settings WHERE key=?", (k,)) else "__none__")
                                  for k in ("cutting_loss_tolerance_permille", "thaw_shelf_life_days")},
                "locations": [], "label_templates": []}
        store.tx(lambda: _write_meta(store, meta))
        self.meta = meta
        shop.update_settings(self.owner, {"cutting_loss_tolerance_permille": 80, "thaw_shelf_life_days": 2})
        for row in shop.locations():
            self.loc[row["code"]] = row["id"]
        if "FZ" not in self.loc:
            self.loc["FZ"] = shop.add_location(self.owner, "FZ", "ثلاجة التجميد", "Freezer", "freezer", 0)
            meta["locations"].append(self.loc["FZ"])
        self._staff()
        self._parties()
        self._catalogue()
        self._label_template()
        meta.update(last_product=store.scalar("SELECT MAX(id) FROM products"),
                    last_supplier=store.scalar("SELECT MAX(id) FROM suppliers"), last_customer=store.scalar("SELECT MAX(id) FROM customers"))
        store.tx(lambda: _write_meta(store, meta))
        self._history(first, today_s)
        meta["counts"] = self.counts
        meta["complete"] = True
        meta["last_op"] = store.scalar("SELECT MAX(id) FROM ops") or 0
        meta["last_product"] = store.scalar("SELECT MAX(id) FROM products")
        store.tx(lambda: _write_meta(store, meta))
        return {"days": DAYS, **self.counts}

    def _staff(self):
        a = self.shop.auth

        def work():
            for username, name, role, ws in STAFF:
                uid = a.create_user(USER_PREFIX + username, name + TAG, role, secrets.token_urlsafe(24) + "aZ9")
                self.store.db.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        self.store.tx(work)
        for username, name, role, ws in STAFF:
            row = self.store.one("SELECT * FROM users WHERE username=?", (USER_PREFIX + username,))
            actor = a.public(row)
            actor["workstation"] = ws
            self.users[username] = actor

    def _parties(self):
        for key, (name, phone) in SUPPLIERS.items():
            self.sup[key] = self.shop.add_party(self.owner, "supplier", name + TAG, phone)
        for name, phone, limit in CUSTOMERS:
            self.cust.append(self.shop.add_party(self.owner, "customer", name + TAG, phone, limit))

    def _catalogue(self):
        shop, o = self.shop, self.owner
        n = 0
        for key, ar, en, price, cost, shelf, rate, sup, place, carton in GROCERY:
            n += 1
            product = {"code": str(1000 + n), "name_ar": ar, "name_en": en, "kind": "grocery", "measure": "unit",
                       "needs_expiry": True, "price": egp(price), "barcodes": [ean("622%09d" % (100000 + n))],
                       "reorder_level": max(rate * 2, 4)}
            if carton:
                product["packagings"] = [{"name_ar": "كرتونة %d" % carton, "name_en": "Carton of %d" % carton,
                                          "factor_num": carton, "barcodes": [ean("1622%08d" % (100000 + n))]}]
            pid = shop.add_product(o, product)
            self.p[key] = pid
            self.info[pid] = dict(key=key, kind="grocery", cost=egp(cost), shelf=shelf, rate=rate, sup=sup, place=place,
                                  carton=carton, price=egp(price), name=ar,
                                  pk=self.store.scalar("SELECT id FROM packagings WHERE product_id=?", (pid,)) if carton else None)
        for i, (key, ar, en, price) in enumerate(MEAT):
            pid = shop.add_product(o, {"code": str(2001 + i), "name_ar": ar, "name_en": en, "kind": "raw_meat", "measure": "g",
                                       "price": egp(price), "reorder_level": 5000})
            self.p[key] = pid
            self.info[pid] = dict(key=key, kind="raw_meat", cost=egp(MEAT_COST.get(key, 0)), sup="meat", name=ar, price=egp(price))
        self.p["onion"] = shop.add_product(o, {"code": "3001", "name_ar": "بصل مفروم", "name_en": "Chopped onion", "kind": "ingredient",
                                               "measure": "g"})
        self.p["spice"] = shop.add_product(o, {"code": "3002", "name_ar": "توابل كفتة", "name_en": "Kofta spices", "kind": "ingredient",
                                               "measure": "g", "allergens": "celery"})
        self.p["tray"] = shop.add_product(o, {"code": "3003", "name_ar": "صينية ألومنيوم", "name_en": "Aluminium tray",
                                              "kind": "packaging", "measure": "unit"})
        self.p["kofta"] = shop.add_product(o, {"code": "4001", "name_ar": "كفتة جاهزة للشوي (نيئة)", "name_en": "Raw kofta tray",
                                               "kind": "prepared", "measure": "g", "price": egp(470), "meat_permille": 800})
        shop.save_recipe(o, self.p["kofta"], [{"product_id": self.p["mince"], "qty": 450, "role": "meat"},
                                              {"product_id": self.p["onion"], "qty": 80, "role": "food"},
                                              {"product_id": self.p["spice"], "qty": 20, "role": "food"},
                                              {"product_id": self.p["tray"], "qty": 1, "role": "packaging"}],
                         2, "celery", 30, 535, "وصفة عيّنة؛ المدة تعليمية وليست مدة معتمدة")

    def _label_template(self):
        tid = self.shop.save_label_template(self.owner, {"name": "ميزان الجزارة (عيّنة)", "prefix": "21", "length": 13, "item_start": 2,
                                                         "item_len": 5, "value_start": 7, "value_len": 5, "value_kind": "weight",
                                                         "decimals": 3})
        self.meta["label_templates"].append(tid)

    # ------------------------------------------------------------ the month
    def _history(self, first, today_s):
        shop = self.shop
        deposit_day = first
        self.clock.at(deposit_day, 7, 5)
        shop.safe_move(self.owner, self.key(), "deposit", egp(250000), "رأس مال تشغيل — عيّنة")
        stock_day = first
        self._opening_stock(stock_day)
        day = first
        while day < today_s:
            self._day(day)
            day = add_days(day, 1)
        self._today(today_s)

    def _opening_stock(self, day):
        """The shelves the shop starts the month with: one delivery per supplier."""
        self.clock.at(day, 7, 10)
        for sup in SUPPLIERS:
            if sup == "meat":
                continue
            wanted = [(pid, int(i["rate"] * self._cover(i) * 0.8) + 1) for pid, i in self.info.items() if i["kind"] == "grocery" and i["sup"] == sup]
            self._receive_groceries(sup, wanted, day, opening=True)
        self._receive_meat(day, 45000, 7000, 12000, 9000, 8000, opening=True)

    # --- buying
    def _cost(self, pid, day):
        info = self.info[pid]
        return int(info["cost"] * self.price_drift.get(info["key"], 1.0))

    @staticmethod
    def _cover(info):
        """Days of sales worth ordering at once: never more than about half the shelf life, so milk is bought often and rice rarely."""
        return max(3, min(10, int(info["shelf"] * 0.5)))

    def _receive_groceries(self, sup, wanted, day, opening=False):
        lines = []
        for pid, qty in wanted:
            info = self.info[pid]
            if qty <= 0:
                continue
            exp = add_days(day, max(2, int(info["shelf"] * (0.6 if opening else self.rnd.uniform(0.8, 0.97)))))
            unit = self._cost(pid, day)
            line = {"product_id": pid, "location_id": self.loc[info["place"]], "expires": exp,
                    "supplier_lot": "B%d%02d" % (self.rnd.randint(100, 999), self.rnd.randint(1, 28))}
            if info["carton"] and qty >= info["carton"]:
                packs = max(1, qty // info["carton"])
                line.update(packaging_id=info["pk"], packs=packs, line_cost=unit * packs * info["carton"])
            else:
                line.update(qty=qty, line_cost=unit * qty)
            lines.append(line)
        if not lines:
            return None
        self.invoice_no += 1
        total = sum(l["line_cost"] for l in lines)
        payload = {"supplier_id": self.sup[sup], "supplier_invoice": "SUP-%04d" % self.invoice_no, "invoice_date": day, "lines": lines}
        if total > 400000 and self.rnd.random() < 0.3:
            payload["invoice_discount"] = total // 100            # 1 % settlement discount
        out = self.shop.receive(self.users["receiver"], self.key(), payload)
        self.counts["receipts"] += 1
        return out

    def _receive_meat(self, day, carcass_g, liver_g, chicken_g, breast_g, frozen_g, opening=False):
        info = lambda k: self.info[self.p[k]]   # noqa: E731
        lines = []
        for key, grams, place, shelf in (("carcass", carcass_g, "PR", 5), ("liver", liver_g, "FR", 3), ("chicken", chicken_g, "FR", 3),
                                         ("breast", breast_g, "FR", 3), ("frozen", frozen_g, "FZ", 150)):
            if grams <= 0:
                continue
            tare = 600 if key == "carcass" else 300
            gross = grams + tare
            lines.append({"product_id": self.p[key], "gross_g": gross, "tare_g": tare, "location_id": self.loc[place],
                          "line_cost": info(key)["cost"] * grams // 1000, "expires": add_days(day, shelf),
                          "supplier_lot": "M%d" % self.rnd.randint(1000, 9999)})
        self.invoice_no += 1
        self.shop.receive(self.users["receiver"], self.key(), {"supplier_id": self.sup["meat"], "supplier_invoice": "SUP-%04d" % self.invoice_no,
                                                              "invoice_date": day, "lines": lines})
        self.counts["receipts"] += 1

    def _restock(self, day):
        """Order up to about ten days of sales for whatever is running low; one invoice per supplier."""
        by_sup = {}
        for pid, info in self.info.items():
            if info["kind"] != "grocery":
                continue
            have = self._on_hand(pid)
            cover = self._cover(info)
            if have <= info["rate"] * cover * 0.4:
                by_sup.setdefault(info["sup"], []).append((pid, int(info["rate"] * cover * self.rnd.uniform(0.9, 1.15)) + 1))
        if not by_sup:
            return
        self.clock.at(day, 7, 20)
        for sup, wanted in by_sup.items():
            self._receive_groceries(sup, wanted, day)

    def _on_hand(self, pid):
        return self.store.scalar("""SELECT COALESCE(SUM(b.qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            JOIN locations c ON c.id=b.location_id WHERE l.product_id=? AND l.status='released' AND c.sellable=1""", (pid,))

    def _meat_on_hand(self, key):
        return self.store.scalar("""SELECT COALESCE(SUM(b.qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE l.product_id=? AND l.status='released'""", (self.p[key],))

    # --- one trading day
    def _day(self, day):
        shop = self.shop
        wd = dt.date.fromisoformat(day).weekday()            # Mon=0 ... Fri=4
        # 1. morning: prices rise a little half way through the month (nothing else is as realistic as inflation)
        if day == add_days(self.today, -DAYS + 15):
            self._price_rise(day)
        if day == add_days(self.today, -DAYS + 10):
            self.clock.at(day, 7, 30)
            shop.add_promotion(self.users["manager"], self.key(), self.p["juice"], 2, 1, day, add_days(self.today, 7))
        # 2. deliveries
        self._restock(day)
        if wd in (1, 4):
            self.clock.at(day, 7, 40)
            self._receive_meat(day, 42000, 6500, 10000, 8000, 9000 if wd == 1 else 0)
        # 3. the butcher
        self._butcher(day)
        if wd == 3:
            self._thaw(day)
        # 4. two shifts
        total_tx = {4: 95, 5: 85}.get(wd, 70) + self.rnd.randint(-12, 12)
        morning = int(total_tx * 0.45)
        self._shift(day, "cashier1", 8, 0, 15, 50, morning)
        self._shift(day, "cashier2", 16, 0, 22, 45, total_tx - morning)
        # 5. evening office work
        self.clock.at(day, 23, 0)
        self._confirm_pending(day)
        if wd == 3:
            self._collect_and_pay(day)
        self._waste(day)
        if wd == 2 and day >= add_days(self.today, -DAYS + 12):
            self._maybe_count(day)
        self._extras(day)
        self.clock.at(day, 23, 40)
        shop.close_day(self.owner, self.key(), day)

    def _price_rise(self, day):
        self.clock.at(day, 7, 15)
        for key in ("oil", "sugar", "rice", "cola", "juice", "tea"):
            pid = self.p[key]
            old = self.info[pid]["price"]
            new = int(round(old * 1.08 / 50.0)) * 50
            self.shop.set_price(self.users["manager"], self.key(), pid, new)
            self.info[pid]["price"] = new
            self.price_drift[key] = 1.06

    def _butcher(self, day):
        butcher = self.users["butcher"]
        carcass = self.store.all("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE l.product_id=? AND l.status='released' AND b.qty>0 AND l.expires>=? ORDER BY l.expires, l.id""", (self.p["carcass"], day))
        short = self._meat_on_hand("boneless") < 8000 or self._meat_on_hand("mince") < 5000
        if carcass and short:
            c = carcass[0]
            take = min(c["qty"], 24000)
            boneless, mince, bones = int(take * 0.45), int(take * 0.38), int(take * 0.11)
            loss = take - boneless - mince - bones
            self.clock.at(day, 7, 50)
            self.shop.produce(butcher, self.key(), {
                "kind": "cutting", "inputs": [{"lot_id": c["lot_id"], "location_id": c["location_id"], "qty": take}],
                "outputs": [{"product_id": self.p["boneless"], "qty": boneless, "location_id": self.loc["FR"], "value_weight": 52},
                            {"product_id": self.p["mince"], "qty": mince, "location_id": self.loc["FR"], "value_weight": 44},
                            {"product_id": self.p["bones"], "qty": bones, "location_id": self.loc["FR"], "value_weight": 6}],
                "natural_loss_g": loss, "abnormal_loss_g": 0, "cost_method": "value", "note": "تقطيع يومي — عيّنة"})
            self.counts["cuts"] += 1
        # tray of kofta every other day, from the mince in the display fridge
        if dt.date.fromisoformat(day).toordinal() % 2 == 0 and self._meat_on_hand("mince") > 6000:
            self._kofta(day, butcher)

    def _kofta(self, day, butcher):
        n = 8
        mince = self.store.all("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE l.product_id=? AND l.status='released' AND b.qty>=? AND l.expires>=? ORDER BY l.expires, l.id""",
                               (self.p["mince"], 450 * n, day))
        if not mince:
            return
        # ingredients arrive with the mince (small purchase each time they are short)
        for key, grams, cost_kg in (("onion", 800, 12), ("spice", 200, 90)):
            if not self._ingredient_free(key, grams * 1):
                self.invoice_no += 1
                self.clock.at(day, 8, 5)
                self.shop.receive(self.users["receiver"], self.key(), {"supplier_id": self.sup["meat"],
                    "supplier_invoice": "SUP-%04d" % self.invoice_no, "invoice_date": day, "lines": [
                        {"product_id": self.p[key], "qty": grams * 8, "line_cost": cost_kg * grams * 8 // 10, "location_id": self.loc["PR"],
                         "expires": add_days(day, 6)}]})
        if not self.store.scalar("SELECT COALESCE(SUM(b.qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id WHERE l.product_id=?", (self.p["tray"],)):
            self.invoice_no += 1
            self.shop.receive(self.users["receiver"], self.key(), {"supplier_id": self.sup["meat"], "supplier_invoice": "SUP-%04d" % self.invoice_no,
                                                                  "invoice_date": day, "lines": [{"product_id": self.p["tray"], "qty": 200,
                                                                                                   "line_cost": 55000, "location_id": self.loc["PR"]}]})
        inputs = [{"lot_id": mince[0]["lot_id"], "location_id": mince[0]["location_id"], "qty": 450 * n}]
        for key, per in (("onion", 80), ("spice", 20), ("tray", 1)):
            lot = self.store.one("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
                WHERE l.product_id=? AND b.qty>=? AND (l.expires IS NULL OR l.expires>=?) ORDER BY l.expires, l.id""", (self.p[key], per * n, day))
            if not lot:
                return
            inputs.append({"lot_id": lot["lot_id"], "location_id": lot["location_id"], "qty": per * n})
        food_in = (450 + 80 + 20) * n
        out = 535 * n
        self.clock.at(day, 8, 20)
        recipe = self.store.scalar("SELECT v.id FROM recipe_versions v JOIN recipes r ON r.id=v.recipe_id WHERE r.product_id=? ORDER BY v.version DESC",
                                   (self.p["kofta"],))
        self.shop.produce(butcher, self.key(), {
            "kind": "recipe", "recipe_version_id": recipe, "inputs": inputs,
            "outputs": [{"product_id": self.p["kofta"], "qty": out, "location_id": self.loc["FR"]}],
            "natural_loss_g": food_in - out, "abnormal_loss_g": 0, "cost_method": "weight",
            "packs": [{"net_g": 535, "tare_g": 15} for _ in range(n)]})
        self.counts["kofta"] += 1

    def _ingredient_free(self, key, grams):
        return self.store.scalar("""SELECT COALESCE(SUM(b.qty),0) FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE l.product_id=? AND (l.expires IS NULL OR l.expires>=?)""", (self.p[key], core.today())) >= grams * 8

    def _thaw(self, day):
        lot = self.store.one("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE l.product_id=? AND b.location_id=? AND b.qty>0 ORDER BY l.expires""", (self.p["frozen"], self.loc["FZ"]))
        if lot:
            self.clock.at(day, 8, 40)
            out = self.shop.thaw(self.users["butcher"], self.key(), lot["lot_id"], lot["location_id"], self.loc["FR"], min(lot["qty"], 3000))
            self.meta.setdefault("thawed_lots", []).append(out.get("lot"))

    # --- the till
    def _shift(self, day, who, h1, m1, h2, m2, transactions):
        shop, actor = self.shop, self.users[who]
        self.clock.at(day, h1, m1)
        shop.open_shift(actor, self.key(), egp(500))
        span = (h2 * 60 + m2) - (h1 * 60 + m1) - 20
        times = sorted(self.rnd.randint(5, span) for _ in range(transactions))
        refunds_at = set(self.rnd.sample(range(transactions), k=1)) if transactions > 10 and self.rnd.random() < 0.6 else set()
        for i, minute in enumerate(times):
            t = h1 * 60 + m1 + minute
            self.clock.at(day, t // 60, t % 60, self.rnd.randint(0, 59))
            self._sale(actor, day)
            if i in refunds_at:
                self._refund(actor)
        self.clock.at(day, h2, m2)
        self._close_shift(actor)

    def _close_shift(self, actor):
        """Count the drawer. A cashier cannot move cash to the safe (only a manager can, from his own drawer),
        so the whole drawer is counted; now and then it is a few pounds off, as in every real shop."""
        shop = self.shop
        expected = shop.shift_expected(shop._open_shift_of(actor)["id"])
        diff = self.rnd.choice([0, 0, 0, 0, 0, 0, 0, -egp(5), egp(2), -egp(10), 0, 0])
        shop.close_shift(actor, self.key(), expected + diff, None if not diff else "فرق بسيط عند العدّ")

    def _pick_products(self):
        pids = [pid for pid, i in self.info.items() if i["kind"] == "grocery"]
        weights = [self.info[pid]["rate"] for pid in pids]
        meat = [self.p[k] for k in ("boneless", "mince", "liver", "chicken", "breast", "bones", "frozen")]
        meat_w = [30, 25, 10, 15, 12, 8, 6]
        n = 1 + min(6, int(self.rnd.expovariate(0.45)))
        chosen = []
        for _ in range(n):
            if self.rnd.random() < 0.09:
                chosen.append(self.rnd.choices(meat, meat_w)[0])
            else:
                chosen.append(self.rnd.choices(pids, weights)[0])
        return list(dict.fromkeys(chosen))

    def _sale(self, actor, day):
        shop, rnd = self.shop, self.rnd
        lines = []
        for pid in self._pick_products():
            info = self.info[pid]
            have = shop.available(pid)
            if info["kind"] == "raw_meat":
                grams = rnd.choice([250, 500, 500, 750, 1000, 1000, 1500, 2000]) if info["key"] != "bones" else rnd.choice([500, 1000])
                if have >= grams:
                    lines.append({"product_id": pid, "qty": grams})
            else:
                qty = rnd.choice([1, 1, 1, 2, 2, 3]) if info["rate"] < 15 else rnd.choice([1, 2, 3, 4, 6])
                if have >= qty:
                    lines.append({"product_id": pid, "qty": qty})
        if rnd.random() < 0.05:                                    # a labelled kofta tray
            pack = self.store.one("""SELECT p.code FROM packs p WHERE p.status='available' AND p.removed_op IS NULL AND p.expires>=?
                ORDER BY p.expires, p.id LIMIT 1""", (day,))
            if pack:
                lines.append({"pack_code": pack["code"]})
        if not lines:
            return
        payload = {"lines": lines}
        quote = shop.quote(actor, payload)
        total = quote["total"]
        if total > 20000 and rnd.random() < 0.15:                 # round-off discount by the cashier, below the 5 % limit
            payload["invoice_discount"] = (total // 2000) * 100 if (total // 2000) * 100 * 1000 <= total * 40 else 0
            total -= payload["invoice_discount"]
        r, approver = rnd.random(), None
        if r < 0.035 and total > 5000:
            customer = rnd.choice(self.cust)
            owed = shop.customer_balance(customer)
            limit = self.store.scalar("SELECT credit_limit FROM customers WHERE id=?", (customer,))
            if owed + total <= limit and not shop.account_check(customer)["problems"]:
                payload.update(customer_id=customer, payments=[{"method": "credit", "amount": total}])
                approver = {"id": self.owner["id"], "role": "owner"}
        elif r < 0.17 and total > 3000:
            payload["payments"] = [{"method": "card", "amount": total, "reference": "POS%06d" % rnd.randint(1, 999999)}]
        elif r < 0.25 and total > 3000:
            method = rnd.choice(["wallet", "transfer"])
            payload["payments"] = [{"method": method, "amount": total, "reference": "%s-%d" % (method[:2].upper(), rnd.randint(10 ** 7, 10 ** 8))}]
        else:
            payload["cash_tendered"] = ((total + 499) // 500) * 500 if rnd.random() < 0.7 else ((total + 4999) // 5000) * 5000
        try:
            out = shop.sell(actor, self.key(), payload, approver)
        except Conflict:
            return
        self.counts["sales"] += 1
        self.recent_sales.append((out["sale"], out["total"], actor["id"], [(l["line"], l["product"], l["qty"]) for l in out["lines"]], bool(approver)))
        self.recent_sales = self.recent_sales[-40:]

    def _refund(self, actor):
        """A customer brings something back: a small grocery return of the shift's own sales."""
        shop = self.shop
        for sale_id, total, user, lines, on_credit in reversed(self.recent_sales):
            if user != actor["id"] or on_credit:
                continue
            for line_id, pid, qty in lines:
                info = self.info[pid]
                if info["kind"] != "grocery":
                    continue
                try:
                    shop.return_sale(actor, self.key(), {"sale_id": sale_id, "reason": self.rnd.choice(["quality", "change_of_mind", "wrong_item"]),
                                                         "method": "cash", "lines": [{"sale_line_id": line_id, "qty": 1,
                                                                                      "restock": self.rnd.random() < 0.5}]},
                                     {"id": self.owner["id"], "role": "owner"})
                    self.counts["returns"] += 1
                except ShopError:
                    continue
                return

    # --- evening
    def _confirm_pending(self, day):
        n = (dt.date.fromisoformat(day) - dt.date.fromisoformat(add_days(self.today, -DAYS))).days
        for i, p in enumerate(self.shop.pending_payments(self.owner)):
            reject = n == 9 and i == 0                       # a transfer that never arrived
            self.shop.confirm_payment(self.owner, self.key(), p["id"], "rejected" if reject else "confirmed")

    def _collect_and_pay(self, day):
        """Thursday: customers settle part of what they owe; the owner pays suppliers from the safe."""
        shop = self.shop
        actor = self.users["cashier1"]
        self.clock.at(day, 22, 50)
        shop.open_shift(actor, self.key(), egp(100))
        for cid in self.cust:
            bal = shop.customer_balance(cid)
            if bal > 0 and self.rnd.random() < 0.75:
                part = bal if self.rnd.random() < 0.4 else (bal // 2 // 100) * 100
                if part > 0:
                    try:
                        shop.customer_payment(actor, self.key(), cid, part, "cash")
                    except ShopError:
                        pass
        shop.close_shift(actor, self.key(), shop.shift_expected(shop._open_shift_of(actor)["id"]))
        self.clock.at(day, 22, 55)
        week_cash = self.store.scalar("""SELECT COALESCE(SUM(c.amount),0) FROM cash_entries c JOIN ops o ON o.id=c.op_id
            WHERE c.kind IN ('sale','debt_collect') AND o.business_day>?""", (add_days(day, -7),)) or 0
        if week_cash > 0:                                                  # the owner banks the week's takings in the safe
            shop.safe_move(self.owner, self.key(), "deposit", (week_cash * 7 // 10 // 100) * 100, "توريد إيراد الأسبوع — عيّنة")
        for key, sid in self.sup.items():
            bal = shop.supplier_balance(sid)
            if bal > egp(500) and self.rnd.random() < 0.8:
                pay = bal if self.rnd.random() < 0.45 else (bal * 6 // 10 // 100) * 100
                safe = shop.safe_balance()
                pay = min(pay, safe)
                if pay > 0:
                    shop.supplier_payment(self.owner, self.key(), sid, pay, "دفعة للمورد — عيّنة", "safe")

    def _waste(self, day, before=False):
        """Expired goods leave the shelf and are written off, with the reason, every evening."""
        rows = self.store.all("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            JOIN locations c ON c.id=b.location_id WHERE b.qty>0 AND l.expires IS NOT NULL AND l.expires<=? AND c.kind NOT IN ('waste','quarantine')
            AND l.status='released' ORDER BY b.lot_id""", (add_days(day, -1) if before else day,))
        for r in rows:
            try:
                self.shop.waste(self.owner, self.key(), r["lot_id"], r["location_id"], r["qty"], "expired", "انتهت الصلاحية — عيّنة")
                self.counts["waste"] += 1
            except ShopError:
                continue

    def _try(self, label, fn):
        """Rare events are decoration: a business refusal skips one of them (and is remembered), it never stops the month."""
        try:
            return fn()
        except ShopError as exc:
            self.meta.setdefault("skipped", []).append("%s: %s" % (label, exc.code))
            return None

    def _lot_of(self, key, minimum=1, newest=False):
        return self.store.one("""SELECT b.lot_id, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            JOIN locations c ON c.id=b.location_id WHERE l.product_id=? AND b.qty>=? AND l.status='released' AND c.sellable=1
            ORDER BY l.id""" + (" DESC" if newest else ""), (self.p[key], minimum))

    def _extras(self, day):
        """Rare events, on fixed days of the month so every screen has something to show."""
        n = (dt.date.fromisoformat(day) - dt.date.fromisoformat(add_days(self.today, -DAYS))).days
        shop, owner = self.shop, self.owner
        if n == 4:        # an invoice that arrives before the goods
            self._try("invoice", lambda: shop.register_supplier_invoice(self.users["manager"], self.key(), {
                "supplier_id": self.sup["dry"], "number": "PRE-0001", "invoice_date": day, "note": "فاتورة قبل وصول البضاعة — عيّنة",
                "lines": [{"product_id": self.p["rice"], "qty": 120, "line_cost": 120 * self._cost(self.p["rice"], day)},
                          {"product_id": self.p["oil"], "qty": 48, "free_qty": 4, "line_cost": 48 * self._cost(self.p["oil"], day)}]}))
        if n == 6:        # half of those goods arrive
            inv = self.store.scalar("SELECT id FROM supplier_invoices ORDER BY id DESC LIMIT 1")
            if inv:
                self.clock.at(day, 23, 10)
                self._try("receive-invoice", lambda: shop.receive(self.users["receiver"], self.key(), {"invoice_id": inv, "lines": [
                    {"product_id": self.p["rice"], "qty": 60, "location_id": self.loc["SH"], "expires": add_days(day, 300)},
                    {"product_id": self.p["oil"], "qty": 24, "free_qty": 2, "location_id": self.loc["SH"], "expires": add_days(day, 400)}]}))
        if n == 8:        # a damaged carton on the shelf
            lot = self._lot_of("juice", 4)
            if lot:
                shop.waste(self.users["manager"], self.key(), lot["lot_id"], lot["location_id"], 3, "damaged", "علب مبعوجة من الكرتونة")
        if n == 14:       # the supplier recalls a lot; two days later the recall is lifted after a check
            lot = self._lot_of("choco", 3, True)
            if lot:
                self.meta["recall"] = self._try("recall", lambda: shop.recall(owner, self.key(), lot["lot_id"], "سحب احترازي من المورد — عيّنة"))
        if n == 16 and self.meta.get("recall") is not None:
            rec = self.store.scalar("SELECT id FROM recalls ORDER BY id DESC LIMIT 1")
            self._try("lift", lambda: shop.lift_recall(owner, self.key(), rec, "تم الفحص والمورد أكد سلامة التشغيلة — عيّنة"))
        if n == 18:       # goods back to the supplier; his credit note comes two days later
            lot = self._lot_of("cola", 6, True)
            if lot:
                r = self._try("supplier-return", lambda: shop.supplier_return(owner, self.key(), self.sup["drinks"], lot["lot_id"],
                                                                              lot["location_id"], 5, "damaged", "انتفاخ في العبوات"))
                self.meta["supplier_return"] = r and r.get("supplier_return")
        if n == 20 and self.meta.get("supplier_return"):
            self._try("credit-note", lambda: shop.supplier_credit_note(owner, self.key(), self.meta["supplier_return"], egp(5 * 17), "CN-0001"))
        if n == 22:       # a rebate the drinks supplier gives for the month
            rec = self.store.scalar("SELECT id FROM receipts WHERE supplier_id=? ORDER BY id DESC LIMIT 1", (self.sup["drinks"],))
            if rec:
                self._try("rebate", lambda: shop.supplier_adjustment(owner, self.key(), {
                    "kind": "rebate", "supplier_id": self.sup["drinks"], "receipt_id": rec, "amount": egp(120), "reference": "REB-0001",
                    "note": "خصم كمية نهاية الشهر — عيّنة"}))
        if n == 24:       # a lot held for a cold-chain check
            lot = self._lot_of("milk", 1, True)
            if lot:
                self._try("hold", lambda: shop.set_lot_status(owner, self.key(), lot["lot_id"], "held", "فحص حرارة الثلاجة — عيّنة"))
        if n == 26:       # the supplier's final invoice differs from what arrived
            rec = self.store.scalar("SELECT id FROM receipts WHERE supplier_id=? ORDER BY id DESC LIMIT 1", (self.sup["home"],))
            if rec:
                self._try("correction", lambda: shop.supplier_adjustment(owner, self.key(), {
                    "kind": "invoice_correction", "supplier_id": self.sup["home"], "receipt_id": rec, "amount": egp(75),
                    "reference": "COR-0001", "note": "فرق سعر في الفاتورة النهائية — عيّنة"}))

    def _maybe_count(self, day):
        """The counter counts the grocery shelf; the owner approves small differences."""
        shop, counter = self.shop, self.users["counter"]
        self.clock.at(day, 21, 0)
        opened = shop.open_count(counter, self.loc["SH"])
        cid = opened["count"]
        rows = self.store.all("""SELECT b.lot_id, b.qty FROM stock_balances b WHERE b.location_id=? AND b.qty>0 ORDER BY b.lot_id""", (self.loc["SH"],))
        for r in rows:
            counted = r["qty"]
            if self.rnd.random() < 0.06 and counted > 6:
                counted -= self.rnd.choice([1, 2])              # a piece nobody can explain
            shop.record_count(counter, cid, r["lot_id"], counted)
        self.clock.at(day, 21, 30)
        shop.approve_count(self.owner, self.key(), cid, None, True)
        self.counts["counts"] += 1

    def _today(self, today_s):
        """This morning: the first shift is open and has sold a little, so the home screen is alive."""
        core.set_clock(None)
        now = core.now()
        self.clock.tz = now.tzinfo
        if now.hour < 9:
            return
        # stock delivered and counted, then a morning of sales, all earlier than 'now'
        t_end = (now.hour * 60 + now.minute) - 5
        if t_end < 8 * 60 + 20:
            return
        self._restock(today_s)
        if dt.date.fromisoformat(today_s).weekday() in (1, 4):
            self.clock.at(today_s, 7, 40)
            self._receive_meat(today_s, 42000, 6500, 10000, 8000, 9000 if dt.date.fromisoformat(today_s).weekday() == 1 else 0)
        self._butcher(today_s)
        self._waste(today_s, before=True)
        actor = self.users["cashier1"]
        self.clock.at(today_s, 8, 0)
        self.shop.open_shift(actor, self.key(), egp(500))
        k = max(3, min(30, (t_end - 8 * 60) // 12))
        for minute in sorted(self.rnd.randint(8 * 60 + 5, t_end) for _ in range(k)):
            self.clock.at(today_s, minute // 60, minute % 60, self.rnd.randint(0, 59))
            self._sale(actor, today_s)
        core.set_clock(None)


# ---------------------------------------------------------------- remove
def clear(shop, actor, backup_folder):
    """Remove the sample, and anything made on top of it, in one step. A verified copy comes first.
    Accounts, the shop name and other settings, and the five standard places stay."""
    store = shop.store
    if store.mode != "shop":
        raise ShopError("sample_shop_only")
    with store.lock:
        meta = read_meta(store)
        if not meta:
            raise ShopError("sample_not_loaded")
        copy = backup.create_backup(store, backup_folder, "before-clear-sample")
        db = store.db
        db.execute("PRAGMA foreign_keys=OFF")                   # cannot change inside a transaction
        try:
            db.execute("BEGIN IMMEDIATE")
            triggers = db.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name NOT LIKE 'sync_cap_%'").fetchall()
            for name, _sql in triggers:
                db.execute('DROP TRIGGER "%s"' % name)
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            wiped = [t for t in tables if t not in KEEP_TABLES]
            for t in wiped:
                db.execute('DELETE FROM "%s"' % t)
            if wiped:
                db.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)" % ",".join("?" * len(wiped)), wiped)
            db.execute("DELETE FROM users WHERE username LIKE ? ESCAPE '\\'", (USER_PREFIX.replace("_", "\\_") + "%",))
            for lid in meta.get("locations", []):
                db.execute("DELETE FROM locations WHERE id=?", (lid,))
            for tid in meta.get("label_templates", []):
                db.execute("DELETE FROM label_templates WHERE id=?", (tid,))
            db.execute("DELETE FROM audit WHERE id>?", (meta.get("audit_from", 0),))
            for key, old in (meta.get("prev_settings") or {}).items():
                if old == "__none__":
                    db.execute("DELETE FROM settings WHERE key=?", (key,))
                else:
                    db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(old)))
            db.execute("DELETE FROM settings WHERE key=?", (META_KEY,))
            for _name, sql in triggers:
                db.execute(sql)
            bad = db.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise ShopError("sample_clear_failed", {"why": "foreign_key", "rows": len(bad)})
            store.audit(actor, "sample.clear", None, {"copy": copy["folder"]})
            db.execute("COMMIT")
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
    return {"ok": True, "copy": copy["folder"]}
