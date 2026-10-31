"""Training shop: fictional data in its own folder (mode='training').
Known training passwords exist ONLY here; the real shop has none.
Shelf lives and tolerances below are teaching examples, not legal values.
"""

import uuid

from .barcode import gs1_check_digit
from .core import add_days, today

TRAINING_USERS = [("owner", "صاحب المحل (تدريب)", "owner"), ("manager", "المدير (تدريب)", "manager"),
                  ("cashier", "أمين الصندوق (تدريب)", "cashier"), ("butcher", "الجزار (تدريب)", "butcher"),
                  ("counter", "مسؤول الجرد (تدريب)", "counter")]
TRAINING_PASSWORD = "training-only"


def ean(body):
    return body + gs1_check_digit(body)


def seed(shop):
    if shop.auth.has_users():
        return False
    store = shop.store
    store.tx(lambda: [shop.auth.create_user(u, n, r, TRAINING_PASSWORD) for u, n, r in TRAINING_USERS])
    owner = shop.auth.public(store.one("SELECT * FROM users WHERE username='owner'"))
    owner["workstation"] = "training"
    k = lambda: uuid.uuid4().hex  # noqa: E731
    shop.update_settings(owner, {"shop_name": "سوبر ماركت التدريب", "cutting_loss_tolerance_permille": 80})
    shelf = shop.add_location(owner, "SH", "رفوف البقالة", "Grocery shelves", "shelf", 1)
    fridge = shop.add_location(owner, "FR", "ثلاجة العرض", "Display fridge", "display_fridge", 1)
    prep = shop.add_location(owner, "PR", "ثلاجة التجهيز", "Prep fridge", "prep_fridge", 0)
    shop.add_location(owner, "QA", "حجر الفحص", "Quarantine", "quarantine", 0)
    shop.add_location(owner, "WS", "الهالك", "Waste", "waste", 0)
    supplier = shop.add_party(owner, "supplier", "مورد تجريبي")
    shop.add_party(owner, "customer", "عميل آجل تجريبي", "01000000000", 200000)
    p = {}
    p["juice"] = shop.add_product(owner, {"code": "1001", "name_ar": "عصير مانجو ١ لتر", "name_en": "Mango juice 1L",
                                          "kind": "grocery", "measure": "unit", "needs_expiry": True, "price": 3500,
                                          "barcodes": [ean("622000000001")],
                                          "packagings": [{"name_ar": "كرتونة ١٢", "name_en": "Carton of 12",
                                                          "factor_num": 12, "barcodes": [ean("1622000000001")]}]})
    p["rice"] = shop.add_product(owner, {"code": "1002", "name_ar": "أرز ١ كجم", "name_en": "Rice 1kg", "kind": "grocery",
                                         "measure": "unit", "needs_expiry": True, "price": 3200,
                                         "barcodes": [ean("622000000002")]})
    p["beef"] = shop.add_product(owner, {"code": "2001", "name_ar": "لحم بقري بالعظم", "name_en": "Beef bone-in",
                                         "kind": "raw_meat", "measure": "g", "price": 42000})
    p["cuts"] = shop.add_product(owner, {"code": "2002", "name_ar": "لحم بقري صافي", "name_en": "Beef boneless",
                                         "kind": "raw_meat", "measure": "g", "price": 48000})
    p["mince"] = shop.add_product(owner, {"code": "2003", "name_ar": "لحم مفروم", "name_en": "Minced beef",
                                          "kind": "raw_meat", "measure": "g", "price": 44000})
    p["bone"] = shop.add_product(owner, {"code": "2004", "name_ar": "عظم", "name_en": "Bones", "kind": "raw_meat",
                                         "measure": "g", "price": 6000})
    p["marinade"] = shop.add_product(owner, {"code": "3001", "name_ar": "تتبيلة طاجن", "name_en": "Tagine marinade",
                                             "kind": "ingredient", "measure": "g", "allergens": "celery"})
    p["tray"] = shop.add_product(owner, {"code": "3002", "name_ar": "صينية ألومنيوم", "name_en": "Aluminium tray",
                                         "kind": "packaging", "measure": "unit"})
    p["tagine"] = shop.add_product(owner, {"code": "4001", "name_ar": "طاجن لحمة بالبصل (نيء)", "name_en": "Raw beef & onion tagine",
                                           "kind": "prepared", "measure": "g", "price": 36000})
    day = today()
    shop.receive(owner, k(), {"supplier_id": supplier, "supplier_invoice": "TR-1", "invoice_date": day, "lines": [
        {"product_id": p["juice"], "packaging_id": store.scalar("SELECT id FROM packagings WHERE product_id=?", (p["juice"],)),
         "packs": 2, "line_cost": 60000, "location_id": shelf, "expires": add_days(day, 90)},
        {"product_id": p["rice"], "qty": 30, "line_cost": 75000, "location_id": shelf, "expires": add_days(day, 300)},
        {"product_id": p["beef"], "gross_g": 10400, "tare_g": 400, "line_cost": 300000, "location_id": prep,
         "expires": add_days(day, 3)},
        {"product_id": p["mince"], "qty": 3000, "line_cost": 105000, "location_id": fridge, "expires": add_days(day, 1)},
        {"product_id": p["marinade"], "qty": 2000, "line_cost": 20000, "location_id": prep, "expires": add_days(day, 5)},
        {"product_id": p["tray"], "qty": 50, "line_cost": 25000, "location_id": prep}]})
    shop.save_recipe(owner, p["tagine"], [{"product_id": p["cuts"], "qty": 400, "role": "meat"},
                                          {"product_id": p["marinade"], "qty": 100, "role": "food"},
                                          {"product_id": p["tray"], "qty": 1, "role": "packaging"}],
                     2, "celery", 30, 500, "مثال تدريبي؛ ليست مدة معتمدة")
    shop.save_label_template(owner, {"name": "ميزان تدريبي (وزن)", "prefix": "21", "length": 13, "item_start": 2,
                                     "item_len": 5, "value_start": 7, "value_len": 5, "value_kind": "weight",
                                     "decimals": 3})
    return True


# ---------------------------------------------------------------- exercises
# Each exercise is complete only when the LEDGER shows it for this user; the
# evidence returned is the document numbers, so a trainer can check them.
# `role` says who can do it; `needs` lists exercises to finish first.
EXERCISES = [
    ("receive", ("owner", "manager", "receiver"), []),
    ("cutting", ("owner", "manager", "butcher"), ["receive"]),
    ("tagine", ("owner", "manager", "butcher"), ["receive"]),
    ("shift_open", ("owner", "manager", "cashier"), []),
    ("sell", ("owner", "manager", "cashier"), ["shift_open"]),
    ("collect", ("owner", "manager", "cashier"), ["sell"]),
    ("return", ("owner", "manager", "cashier"), ["sell"]),
    ("count", ("owner", "manager", "counter"), []),
    ("shift_close", ("owner", "manager", "cashier"), ["shift_open"]),
    ("backup", ("owner",), []),
]


def progress(store, user_id):
    """Evidence per exercise for one user (training copy only)."""
    def docs(sql, args=()):
        return [r[0] for r in store.db.execute(sql, args).fetchall()]
    ev = {
        "receive": docs("""SELECT 'R'||r.id FROM receipts r WHERE r.user_id=? AND r.id>1""", (user_id,)),
        "cutting": docs("SELECT 'P'||p.id FROM productions p WHERE p.user_id=? AND p.kind='cutting'", (user_id,)),
        "tagine": docs("SELECT 'P'||p.id FROM productions p WHERE p.user_id=? AND p.kind='recipe'", (user_id,)),
        "shift_open": docs("SELECT 'W'||id FROM shifts WHERE user_id=?", (user_id,)),
        "sell": docs("SELECT no FROM sales WHERE user_id=?", (user_id,)),
        "collect": docs("SELECT 'C'||e.id FROM customer_entries e JOIN ops o ON o.id=e.op_id WHERE e.kind='payment' AND o.user_id=?", (user_id,)),
        "return": docs("SELECT 'M'||id FROM returns WHERE user_id=?", (user_id,)),
        "count": docs("SELECT DISTINCT 'J'||count_id FROM count_lines WHERE user_id=?", (user_id,)),
        "shift_close": docs("SELECT 'W'||id FROM shifts WHERE user_id=? AND closed_at IS NOT NULL", (user_id,)),
        "backup": docs("SELECT 'B'||id FROM audit WHERE action='backup' AND user_id=?", (user_id,)),
    }
    done = {k for k, v in ev.items() if v}
    role = store.db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()[0]
    return [{"id": name, "done": name in done, "evidence": ev[name][:5], "for_you": role in roles,
             "waiting_for": [n for n in needs if n not in done]} for name, roles, needs in EXERCISES]
