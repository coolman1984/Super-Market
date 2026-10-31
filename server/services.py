"""Shop operations. Every method that moves goods or money runs through
Store.command (one transaction, idempotency key bound to the request
fingerprint, audit). Permissions are checked here, never only in the UI.
"""

import hashlib
import json
import re

from . import barcode as bc
from .auth import ROLES, Auth, can, require
from .core import (Conflict, Forbidden, ShopError, add_days, allocate, cumulative_share, integer,
                   iso_date, now_iso, text, today, weighed_amount)

LOCATION_KINDS = ("shelf", "store", "display_fridge", "prep_fridge", "freezer", "quarantine", "waste")
PRODUCT_KINDS = ("grocery", "raw_meat", "prepared", "ingredient", "packaging")
DEFAULT_SETTINGS = {
    "shop_name": "", "currency": "EGP", "near_expiry_days": 2,
    "cashier_discount_permille": 50, "block_below_cost": True,
    "cutting_loss_tolerance_permille": None, "pack_prefix": "28",
    "price_label_policy": "block", "pack_price_policy": "label",
    "gtin_equivalence": False, "tax_mode": "unverified", "recipe_component_tolerance_permille": 50,
    "receipt_footer": "", "receipt_width_mm": 80,
}


class Shop:
    def __init__(self, store):
        self.store = store
        self.auth = Auth(store)

    # =============== settings & master data ===============
    def settings(self):
        out = dict(DEFAULT_SETTINGS)
        for row in self.store.all("SELECT key, value FROM settings WHERE key<>'mode'"):
            out[row["key"]] = json.loads(row["value"])
        out["mode"] = self.store.mode
        return out

    def update_settings(self, actor, changes):
        require(actor, "settings")
        if not isinstance(changes, dict):
            raise ShopError("bad_request")

        def work():
            for key, value in changes.items():
                if key not in DEFAULT_SETTINGS:
                    raise ShopError("unknown_setting", {"key": key})
                self._check_setting(key, value)
                self.store.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                      (key, json.dumps(value)))
            self.store.audit(actor, "settings", None, changes)
            return self.settings()
        return self.store.tx(work)

    SETTING_RULES = {
        "near_expiry_days": (0, 60), "cashier_discount_permille": (0, 1000), "receipt_width_mm": (58, 80),
        "cutting_loss_tolerance_permille": (0, 500), "recipe_component_tolerance_permille": (0, 500),
    }

    def _check_setting(self, key, value):
        bad = ShopError("bad_setting", {"key": key})
        if key in self.SETTING_RULES:
            low, high = self.SETTING_RULES[key]
            if value is None and key == "cutting_loss_tolerance_permille":
                return
            if type(value) is not int or not low <= value <= high:
                raise bad
            if key == "receipt_width_mm" and value not in (58, 80):
                raise bad
        elif key in ("block_below_cost", "gtin_equivalence"):
            if type(value) is not bool:
                raise bad
        elif key == "pack_prefix":
            if not isinstance(value, str) or not re.fullmatch(r"2[0-9]", value):
                raise bad  # GS1 restricted-circulation 20-29 only, never a real GTIN prefix
            if value != self.settings()["pack_prefix"] and self.store.scalar("SELECT 1 FROM packs"):
                raise ShopError("pack_prefix_in_use")
        elif key in ("price_label_policy", "pack_price_policy", "tax_mode"):
            allowed = {"price_label_policy": ("block",), "pack_price_policy": ("label", "current"),
                       "tax_mode": ("unverified",)}[key]
            if value not in allowed:
                raise bad
        elif not isinstance(value, str) or len(value) > 200:
            raise bad

    def add_location(self, actor, code, name_ar, name_en, kind, sellable):
        require(actor, "settings")
        if kind not in LOCATION_KINDS or sellable not in (0, 1, True, False):
            raise ShopError("bad_location")
        if kind in ("quarantine", "waste") and sellable:
            raise ShopError("quarantine_not_sellable")

        code = text(code, "bad_code", 20)

        def work():
            if self.store.scalar("SELECT 1 FROM locations WHERE code=?", (code,)):
                raise ShopError("code_taken")
            cur = self.store.db.execute("INSERT INTO locations(code,name_ar,name_en,kind,sellable) VALUES(?,?,?,?,?)",
                                        (code, text(name_ar), text(name_en), kind, int(sellable)))
            self.store.audit(actor, "location.add", str(cur.lastrowid))
            return cur.lastrowid
        return self.store.tx(work)

    def locations(self):
        return self.store.all("SELECT * FROM locations ORDER BY id")

    def add_product(self, actor, p):
        require(actor, "catalog")
        if not isinstance(p, dict):
            raise ShopError("bad_request")
        kind, measure = p.get("kind"), p.get("measure")
        if kind not in PRODUCT_KINDS or measure not in ("unit", "g"):
            raise ShopError("bad_product")
        if kind in ("raw_meat", "prepared") and measure != "g":
            raise ShopError("meat_is_weighed")
        needs_expiry = 1 if kind in ("raw_meat", "prepared", "ingredient") else int(bool(p.get("needs_expiry")))
        meat_share = p.get("meat_permille")
        if meat_share is not None:
            integer(meat_share, 0, "bad_meat_share", 1000)
            if kind not in ("prepared", "ingredient"):
                raise ShopError("bad_meat_share")   # raw meat is meat; groceries carry no meat claim

        def work():
            code = text(p.get("code"), "bad_code", 30)
            if self.store.scalar("SELECT 1 FROM products WHERE code=?", (code,)):
                raise ShopError("code_taken")
            cur = self.store.db.execute(
                """INSERT INTO products(code,name_ar,name_en,kind,measure,needs_expiry,reorder_level,allergens,meat_permille,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (code, text(p.get("name_ar"), "bad_name", 120), text(p.get("name_en"), "bad_name", 120), kind, measure,
                 needs_expiry, integer(p.get("reorder_level", 0)), str(p.get("allergens", ""))[:200], meat_share, now_iso()))
            pid = cur.lastrowid
            for pk in p.get("packagings", []):
                self._add_packaging(pid, pk)
            for code_text in p.get("barcodes", []):
                self._add_barcode(code_text, pid, None)
            if p.get("price") is not None:
                self.store.db.execute("INSERT INTO prices(product_id,price,effective_at,user_id) VALUES(?,?,?,?)",
                                      (pid, integer(p["price"]), now_iso(), actor["id"]))
            self.store.audit(actor, "product.add", str(pid))
            return pid
        return self.store.tx(work)

    def _add_packaging(self, pid, pk):
        num, den = integer(pk.get("factor_num"), 1), integer(pk.get("factor_den", 1), 1)
        cur = self.store.db.execute(
            "INSERT INTO packagings(product_id,name_ar,name_en,factor_num,factor_den,created_at) VALUES(?,?,?,?,?,?)",
            (pid, text(pk.get("name_ar")), text(pk.get("name_en")), num, den, now_iso()))
        for code_text in pk.get("barcodes", []):
            self._add_barcode(code_text, pid, cur.lastrowid)
        return cur.lastrowid

    def add_packaging(self, actor, product_id, pk):
        require(actor, "catalog")
        return self.store.tx(lambda: self._add_packaging(integer(product_id, 1), pk))

    def _add_barcode(self, code_text, pid, packaging_id):
        code_text = text(code_text, "bad_barcode", 64)
        if not all(32 < ord(c) < 127 for c in code_text):
            raise ShopError("bad_barcode")
        if code_text.isdigit() and len(code_text) in (8, 12, 13, 14) and not (
                bc.valid_gs1(code_text) or bc.upce_to_upca(code_text)):
            raise ShopError("bad_check_digit")
        prefix = self.settings()["pack_prefix"]
        if code_text.isdigit() and len(code_text) == 13 and code_text.startswith(prefix):
            raise ShopError("barcode_reserved_for_packs")
        if self.store.scalar("SELECT 1 FROM barcodes WHERE code=?", (code_text,)):
            raise ShopError("barcode_taken")
        if packaging_id is not None and self.store.scalar("SELECT product_id FROM packagings WHERE id=?",
                                                          (integer(packaging_id, 1),)) != pid:
            raise ShopError("packaging_other_product")
        self.store.db.execute("INSERT INTO barcodes VALUES(?,?,?,?)", (code_text, pid, packaging_id, now_iso()))

    def add_barcode(self, actor, code_text, product_id, packaging_id=None):
        require(actor, "catalog")

        def work():
            self._add_barcode(code_text, integer(product_id, 1), packaging_id)
            self.store.audit(actor, "barcode.add", code_text, {"product": product_id})
        return self.store.tx(work)

    def set_price(self, actor, key, product_id, price):
        require(actor, "prices")
        integer(product_id, 1), integer(price)

        def work(op):
            if not self.store.scalar("SELECT 1 FROM products WHERE id=?", (product_id,)):
                raise ShopError("unknown_product")
            old = self.current_price(product_id)
            self.store.db.execute("INSERT INTO prices(product_id,price,effective_at,user_id,op_id) VALUES(?,?,?,?,?)",
                                  (product_id, price, now_iso(), actor["id"], op))
            return {"product": product_id, "old": old, "price": price}
        return self.store.command("price", key, [product_id, price], actor, work)

    def current_price(self, product_id):
        return self.store.scalar("SELECT price FROM prices WHERE product_id=? ORDER BY id DESC LIMIT 1", (product_id,))

    def add_party(self, actor, kind, name, phone=None, credit_limit=0):
        require(actor, "catalog" if kind == "customer" else "receive")
        name = text(name, "bad_name", 120)

        def work():
            if kind == "supplier":
                cur = self.store.db.execute("INSERT INTO suppliers(name,phone,created_at) VALUES(?,?,?)",
                                            (name, phone, now_iso()))
            elif kind == "customer":
                cur = self.store.db.execute("INSERT INTO customers(name,phone,credit_limit,created_at) VALUES(?,?,?,?)",
                                            (name, phone, integer(credit_limit), now_iso()))
            else:
                raise ShopError("bad_request")
            self.store.audit(actor, kind + ".add", str(cur.lastrowid))
            return cur.lastrowid
        return self.store.tx(work)

    def save_label_template(self, actor, template):
        require(actor, "settings")
        try:
            clean = bc.check_template(template)
        except bc.ScanError as exc:
            raise ShopError(exc.code) from exc
        if clean["prefix"].startswith(self.settings()["pack_prefix"]) or self.settings()["pack_prefix"].startswith(clean["prefix"]):
            raise ShopError("template_overlaps_pack_prefix")

        def work():
            cur = self.store.db.execute("INSERT INTO label_templates(name,config,created_at) VALUES(?,?,?)",
                                        (clean["name"], json.dumps(clean), now_iso()))
            self.store.audit(actor, "label_template.add", str(cur.lastrowid), clean)
            return cur.lastrowid
        return self.store.tx(work)

    def label_templates(self):
        return [json.loads(r["config"]) for r in self.store.all("SELECT config FROM label_templates WHERE active=1 ORDER BY id")]

    # =============== catalog queries ===============
    def product(self, product_id, actor=None):
        row = self.store.one("SELECT * FROM products WHERE id=?", (product_id,))
        if not row:
            raise ShopError("unknown_product")
        row["price"] = self.current_price(product_id)
        row["barcodes"] = self.store.all("SELECT code, packaging_id FROM barcodes WHERE product_id=?", (product_id,))
        row["packagings"] = self.store.all("SELECT * FROM packagings WHERE product_id=?", (product_id,))
        row["available"] = self.available(product_id) if actor is None or self.can_see_qty(actor) else None
        return row

    def search_products(self, query="", limit=30, actor=None):
        like = "%" + (query or "").strip() + "%"
        rows = self.store.all("""SELECT p.*, (SELECT price FROM prices WHERE product_id=p.id ORDER BY id DESC LIMIT 1) price
            FROM products p WHERE p.name_ar LIKE ? OR p.name_en LIKE ? OR p.code LIKE ?
              OR p.id IN (SELECT product_id FROM barcodes WHERE code LIKE ?)
            ORDER BY p.name_ar LIMIT ?""", (like, like, like, like, integer(limit, 1, maximum=500)))
        show = actor is None or self.can_see_qty(actor)
        for r in rows:
            r["available"] = self.available(r["id"]) if show else None
        return rows

    def sellable_lots(self, product_id, on_date=None):
        """FEFO candidates: released, not expired, expiry present when required,
        sellable location, positive balance."""
        day = on_date or today()
        rows = self.store.all("""SELECT b.lot_id, b.location_id, b.qty, b.cost, l.expires, l.lot_no, l.supplier_lot
            FROM stock_balances b JOIN lots l ON l.id=b.lot_id JOIN locations c ON c.id=b.location_id
            JOIN products p ON p.id=l.product_id
            WHERE l.product_id=? AND l.status='released' AND c.sellable=1 AND b.qty>0
              AND ((l.expires IS NULL AND p.needs_expiry=0) OR l.expires>=?)
            ORDER BY l.expires IS NULL, l.expires, l.id, c.id""", (product_id, day))
        blocked = self.recall_blocked()
        return [r for r in rows if r["lot_id"] not in blocked]

    def available(self, product_id):
        return sum(r["qty"] for r in self.sellable_lots(product_id))

    def recall_blocked(self):
        """Every lot under an ACTIVE recall: the recalled lot and everything made
        or returned from it, including lots created after the recall."""
        return {r[0] for r in self.store.db.execute("""WITH RECURSIVE t(id) AS (
            SELECT lot_id FROM recalls WHERE id NOT IN (SELECT recall_id FROM recall_lifts)
            UNION SELECT e.child FROM lot_edges e JOIN t ON e.parent=t.id) SELECT id FROM t""")}

    def resolve_scan(self, actor, raw, profile=None):
        """Scan text -> what it is and what product/qty it proposes. Never
        posts anything; the sale/receipt request is a separate command."""
        require(actor, "sell") if not can(actor, "receive") and not can(actor, "count") else None
        settings = self.settings()
        try:
            info = bc.classify(raw, profile, self.label_templates(), settings["pack_prefix"])
        except bc.ScanError as exc:
            return {"ok": False, "error": exc.code, "detail": exc.detail, "seen": bc.visible(str(raw)[:bc.MAX_SCAN])}
        out = {"ok": True, "scan": info}
        kind = info["kind"]
        if kind == "url":
            out.update(ok=False, error="scan_is_link")
            return out
        if kind == "pack":
            pack = self.store.one("SELECT * FROM packs WHERE code=?", (info["pack_code"],))
            if not pack:
                out.update(ok=False, error="unknown_pack")
                return out
            out["pack"] = pack
            out["product"] = self._brief(pack["product_id"])
            out["qty"] = pack["net_g"]
            if pack["status"] != "available":
                out.update(ok=False, error="pack_already_sold" if pack["status"] == "sold" else "pack_returned")
            current = self.current_price(pack["product_id"])
            if current is not None and pack["price_per_kg"] is not None and current != pack["price_per_kg"]:
                out["price_changed"] = {"packed": pack["price_per_kg"], "current": current,
                                        "policy": settings["pack_price_policy"]}
            return out
        if kind == "label":
            # scale PLU: exact barcode, exact product code, or the code without
            # the scale's zero padding (documented per template)
            row = self.store.one("SELECT product_id FROM barcodes WHERE code=?", (info["item_code"],)) or \
                self.store.one("SELECT id product_id FROM products WHERE code IN (?, ?)",
                               (info["item_code"], info["item_code"].lstrip("0") or "0"))
            if not row:
                out.update(ok=False, error="unknown_item")
                return out
            out["product"] = self._brief(row["product_id"])
            if "net_g" in info:
                out["qty"] = info["net_g"]
            elif settings["price_label_policy"] == "block":
                out.update(ok=False, error="price_label_needs_weight")
            return out
        code = info.get("gtin") or info["code"]
        row = self.store.one("SELECT * FROM barcodes WHERE code=?", (code,))
        if not row and info.get("upca"):
            row = self.store.one("SELECT * FROM barcodes WHERE code=?", (info["upca"],))
        if not row and kind in ("gtin", "gs1"):
            variants = bc.gtin_variants(code, info.get("upca"))
            matches = self.store.all("SELECT code, product_id FROM barcodes WHERE code IN (%s)" %
                                     ",".join("?" * len(variants)), variants) if variants else []
            if matches and settings["gtin_equivalence"]:
                row = self.store.one("SELECT * FROM barcodes WHERE code=?", (matches[0]["code"],))
                out["equivalent_of"] = matches[0]["code"]
            elif matches:
                out.update(ok=False, error="similar_barcode_needs_link", candidates=matches)
                return out
        if not row:
            out.update(ok=False, error="unknown_barcode")
            return out
        out["product"] = self._brief(row["product_id"])
        qty = 1
        if row["packaging_id"]:
            pk = self.store.one("SELECT * FROM packagings WHERE id=?", (row["packaging_id"],))
            if (pk["factor_num"] % pk["factor_den"]) != 0:
                out.update(ok=False, error="fractional_packaging")
                return out
            qty = pk["factor_num"] // pk["factor_den"]
            out["packaging"] = pk
        if out["product"]["measure"] == "g":
            fields = info.get("fields", {})
            if "net_g" in fields:
                qty = fields["net_g"]
            else:
                qty = None  # weighed item needs a weight
        out["qty"] = qty
        if kind == "gs1":
            out["lot_hint"] = info["fields"].get("batch")
            out["expiry_hint"] = info["fields"].get("expiry")
        return out

    def _brief(self, pid):
        p = self.store.one("SELECT id, code, name_ar, name_en, kind, measure, needs_expiry, allergens FROM products WHERE id=?", (pid,))
        p["price"] = self.current_price(pid)
        return p

    # =============== stock primitives (inside a command) ===============
    def _new_lot(self, op, product_id, origin, expires, status="released", supplier_lot=None, allergens=""):
        prefix = {"receipt": "L", "production": "P", "return": "R", "count": "C"}[origin]
        number = self.store.next_seq("lot")
        lot_no = "%s%06d" % (prefix, number)
        cur = self.store.db.execute(
            """INSERT INTO lots(product_id,lot_no,supplier_lot,origin,origin_op,expires,status,allergens,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (product_id, lot_no, supplier_lot, origin, op, expires, status, allergens, now_iso()))
        self.store.db.execute("INSERT INTO lot_events(lot_id,status,reason,op_id,at) VALUES(?,?,?,?,?)",
                              (cur.lastrowid, status, origin, op, now_iso()))
        return cur.lastrowid, lot_no

    def _entry(self, op, kind, lot, location, qty, cost, reason=None):
        self.store.db.execute(
            "INSERT INTO stock_entries(op_id,kind,reason,lot_id,location_id,qty,cost,at) VALUES(?,?,?,?,?,?,?,?)",
            (op, kind, reason, lot, location, qty, cost, now_iso()))

    def _balance(self, lot, location):
        row = self.store.one("SELECT qty, cost FROM stock_balances WHERE lot_id=? AND location_id=?", (lot, location))
        return (row["qty"], row["cost"]) if row else (0, 0)

    def _usable_lot(self, lot_id, day=None):
        lot = self.store.one("""SELECT l.*, p.needs_expiry, p.measure, p.kind pkind FROM lots l
            JOIN products p ON p.id=l.product_id WHERE l.id=?""", (lot_id,))
        if not lot:
            raise ShopError("unknown_lot")
        if lot["status"] == "recalled" or lot_id in self.recall_blocked():
            raise ShopError("lot_recalled", {"lot": lot["lot_no"]})
        if lot["status"] == "held":
            raise ShopError("lot_held", {"lot": lot["lot_no"]})
        if lot["expires"] is None and lot["needs_expiry"]:
            raise ShopError("lot_expiry_missing", {"lot": lot["lot_no"]})
        if lot["expires"] is not None and lot["expires"] < (day or today()):
            raise ShopError("lot_expired", {"lot": lot["lot_no"]})
        return lot

    def _consume(self, op, kind, lot, location, qty, reason=None, check=True):
        integer(qty, 1)
        if check:
            self._usable_lot(lot)
        have, cost = self._balance(lot, location)
        if qty > have:
            raise ShopError("insufficient_stock", {"lot": lot, "have": have, "need": qty})
        taken = cost * qty // have
        self._entry(op, kind, lot, location, -qty, -taken, reason)
        return taken

    def _location(self, location_id, sellable=None):
        loc = self.store.one("SELECT * FROM locations WHERE id=?", (location_id,))
        if not loc:
            raise ShopError("unknown_location")
        return loc

    def _quarantine_location(self):
        loc = self.store.one("SELECT * FROM locations WHERE kind='quarantine' ORDER BY id LIMIT 1")
        if not loc:
            raise ShopError("quarantine_location_missing")
        return loc["id"]

    # =============== receiving ===============
    def receive(self, actor, key, payload):
        """Physical receipt. Lines: product_id, packaging_id+packs or qty,
        free_qty, rejected_qty, gross_g/tare_g for weighed meat, line_cost,
        line_discount, expires, supplier_lot, location_id, hold."""
        require(actor, "receive")
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list) or not payload["lines"]:
            raise ShopError("lines_required")

        def work(op):
            p = payload
            supplier = p.get("supplier_id")
            if supplier is not None and not self.store.scalar("SELECT 1 FROM suppliers WHERE id=?", (integer(supplier, 1),)):
                raise ShopError("unknown_supplier")
            invoice = text(p.get("supplier_invoice"), "bad_invoice", 60, optional=True)
            invoice_date = iso_date(p.get("invoice_date"), optional=True)
            if invoice and supplier and not p.get("confirm_duplicate"):
                dup = self.store.scalar("SELECT id FROM receipts WHERE supplier_id=? AND supplier_invoice=? AND COALESCE(invoice_date,'')=COALESCE(?, '')",
                                        (supplier, invoice, invoice_date))
                if dup:
                    raise ShopError("duplicate_supplier_invoice", {"receipt": dup})
            lines = []
            for raw in p["lines"]:
                if not isinstance(raw, dict):
                    raise ShopError("bad_line")
                prod = self.store.one("SELECT * FROM products WHERE id=?", (integer(raw.get("product_id"), 1),))
                if not prod:
                    raise ShopError("unknown_product")
                gross = tare = packs = None
                pk_id = raw.get("packaging_id")
                if pk_id is not None:
                    pk = self.store.one("SELECT * FROM packagings WHERE id=? AND product_id=?", (integer(pk_id, 1), prod["id"]))
                    if not pk:
                        raise ShopError("unknown_packaging")
                    packs = integer(raw.get("packs"), 1)
                    if (packs * pk["factor_num"]) % pk["factor_den"]:
                        raise ShopError("fractional_quantity")
                    qty = packs * pk["factor_num"] // pk["factor_den"]
                elif raw.get("gross_g") is not None:
                    if prod["measure"] != "g":
                        raise ShopError("gross_only_for_weighed")
                    gross, tare = integer(raw["gross_g"], 1), integer(raw.get("tare_g", 0))
                    qty = gross - tare
                    if qty <= 0:
                        raise ShopError("tare_exceeds_gross")
                else:
                    qty = integer(raw.get("qty"), 1)
                free = integer(raw.get("free_qty", 0))
                rejected = integer(raw.get("rejected_qty", 0))
                if rejected > qty:
                    raise ShopError("rejected_exceeds_qty")
                paid_qty = qty - rejected
                if paid_qty + free <= 0:
                    raise ShopError("nothing_accepted")
                line_cost = integer(raw.get("line_cost"))
                line_discount = integer(raw.get("line_discount", 0))
                if line_discount > line_cost:
                    raise ShopError("discount_exceeds_cost")
                expires = iso_date(raw.get("expires"), optional=True)
                if prod["needs_expiry"] and not expires and not raw.get("hold"):
                    raise ShopError("expiry_required", {"product": prod["code"]})
                if expires and expires < today() and not raw.get("hold"):
                    raise ShopError("already_expired", {"product": prod["code"]})
                loc = self._location(integer(raw.get("location_id"), 1))
                hold = bool(raw.get("hold")) or loc["kind"] == "quarantine"
                lines.append(dict(prod=prod, pk=pk_id, packs=packs, paid=paid_qty, free=free, rejected=rejected,
                                  gross=gross, tare=tare, cost=line_cost, disc=line_discount, expires=expires,
                                  supplier_lot=text(raw.get("supplier_lot"), "bad_lot", 60, optional=True),
                                  loc=loc["id"], hold=hold, reason=text(raw.get("reject_reason"), "bad_reason", 120, optional=True)))
            goods = sum(l["cost"] - l["disc"] for l in lines)
            inv_disc = integer(p.get("invoice_discount", 0))
            extra = integer(p.get("extra_cost", 0))
            if inv_disc > goods:
                raise ShopError("discount_exceeds_cost")
            weights = [l["cost"] - l["disc"] for l in lines]
            disc_parts = allocate(inv_disc, weights)
            extra_parts = allocate(extra, weights if sum(weights) else [l["paid"] + l["free"] for l in lines])
            total = goods - inv_disc + extra
            paid = integer(p.get("paid_now", 0))
            if paid > total:
                raise ShopError("payment_exceeds_total")
            if paid and not supplier:
                raise ShopError("supplier_required")
            if paid:
                require(actor, "supplier_pay")
            rid = self.store.db.execute(
                """INSERT INTO receipts(op_id,supplier_id,supplier_invoice,invoice_date,goods_total,invoice_discount,
                extra_cost,total,paid,user_id,at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (op, supplier, invoice, invoice_date, goods, inv_disc, extra, total, paid, actor["id"], now_iso(),
                 text(p.get("note"), "bad_note", 300, optional=True))).lastrowid
            out_lines = []
            for line, d, e in zip(lines, disc_parts, extra_parts):
                landed = line["cost"] - line["disc"] - d + e
                qty_in = line["paid"] + line["free"]
                status = "held" if line["hold"] else "released"
                lot, lot_no = self._new_lot(op, line["prod"]["id"], "receipt", line["expires"], status,
                                            line["supplier_lot"], line["prod"]["allergens"])
                self._entry(op, "receipt", lot, line["loc"], qty_in, landed)
                self.store.db.execute(
                    """INSERT INTO receipt_lines(receipt_id,product_id,packaging_id,packs,paid_qty,free_qty,rejected_qty,
                    gross_g,tare_g,line_cost,line_discount,landed_cost,lot_id,reject_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rid, line["prod"]["id"], line["pk"], line["packs"], line["paid"], line["free"], line["rejected"],
                     line["gross"], line["tare"], line["cost"], line["disc"], landed, lot, line["reason"]))
                out_lines.append({"lot": lot, "lot_no": lot_no, "qty": qty_in, "cost": landed, "status": status})
            if supplier:
                self.store.db.execute("INSERT INTO supplier_entries(supplier_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                      (supplier, op, "invoice", total, now_iso()))
                if paid:
                    shift = self._open_shift_of(actor)
                    self._check_drawer(shift["id"], paid)
                    self.store.db.execute("INSERT INTO supplier_entries(supplier_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                          (supplier, op, "payment", -paid, now_iso()))
                    self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                          (shift["id"], op, "supplier_pay", -paid, "receipt %d" % rid, now_iso()))
            return {"receipt": rid, "total": total, "lines": out_lines}  # cost redacted in app layer
        return self.store.command("receipt", key, payload, actor, work)

    def supplier_payment(self, actor, key, supplier_id, amount, note=None, source=None):
        """Pay a supplier from the cashier drawer (own open shift) or from the
        safe (back-office cash fed by drops); never more than the source holds."""
        require(actor, "supplier_pay")
        integer(supplier_id, 1), integer(amount, 1)
        note = text(note, "bad_note", 200, optional=True)
        if source is None:   # back office (no shift of its own) pays from the safe
            source = "drawer" if can(actor, "shift") else "safe"
        if source not in ("drawer", "safe"):
            raise ShopError("bad_request")

        def work(op):
            if not self.store.scalar("SELECT 1 FROM suppliers WHERE id=?", (supplier_id,)):
                raise ShopError("unknown_supplier")
            if source == "safe":
                self._check_safe(amount)
                self.store.db.execute("INSERT INTO safe_entries(op_id,kind,amount,note,at) VALUES(?,?,?,?,?)",
                                      (op, "supplier_pay", -amount, note, now_iso()))
            else:
                shift = self._open_shift_of(actor)
                self._check_drawer(shift["id"], amount)
                self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                      (shift["id"], op, "supplier_pay", -amount, note, now_iso()))
            self.store.db.execute("INSERT INTO supplier_entries(supplier_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                  (supplier_id, op, "payment", -amount, now_iso()))
            return {"supplier": supplier_id, "balance": self.supplier_balance(supplier_id), "source": source}
        return self.store.command("supplier_payment", key, [supplier_id, amount, note, source], actor, work)

    def safe_move(self, actor, key, kind, amount, note=None):
        """Owner puts cash into / takes cash out of the safe (with a note)."""
        require(actor, "safe")
        if kind not in ("deposit", "withdrawal"):
            raise ShopError("bad_request")
        integer(amount, 1)
        note = text(note, "bad_note", 200, optional=True)

        def work(op):
            if kind == "withdrawal":
                self._check_safe(amount)
            self.store.db.execute("INSERT INTO safe_entries(op_id,kind,amount,note,at) VALUES(?,?,?,?,?)",
                                  (op, kind, amount if kind == "deposit" else -amount, note, now_iso()))
            return {"safe": self.safe_balance()}
        return self.store.command("safe_move", key, [kind, amount, note], actor, work)

    def supplier_balance(self, supplier_id):
        return self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM supplier_entries WHERE supplier_id=?", (supplier_id,))

    # =============== shifts & drawer ===============
    def _open_shift_of(self, actor, required=True):
        shift = self.store.one("SELECT * FROM shifts WHERE user_id=? AND closed_at IS NULL", (actor["id"],))
        if not shift and required:
            raise ShopError("no_open_shift")
        return shift

    def open_shift(self, actor, key, opening_float):
        require(actor, "shift")
        integer(opening_float)

        def work(op):
            if self._open_shift_of(actor, False):
                raise ShopError("shift_already_open")
            sid = self.store.db.execute("INSERT INTO shifts(user_id,workstation,opened_at,opening_float) VALUES(?,?,?,?)",
                                        (actor["id"], actor["workstation"], now_iso(), opening_float)).lastrowid
            if opening_float:
                self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                      (sid, op, "float", opening_float, None, now_iso()))
            return {"shift": sid}
        return self.store.command("shift_open", key, [opening_float, actor["workstation"]], actor, work)

    CASH_SIGNS = {"expense": -1, "drop": -1, "deposit": 1, "owner_draw": -1}

    def cash_move(self, actor, key, kind, amount, note=None):
        require(actor, "cash_moves")
        if kind not in self.CASH_SIGNS:
            raise ShopError("bad_cash_kind")
        integer(amount, 1)

        def work(op):
            shift = self._open_shift_of(actor)
            if self.CASH_SIGNS[kind] < 0:
                self._check_drawer(shift["id"], amount)
            self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                  (shift["id"], op, kind, self.CASH_SIGNS[kind] * amount,
                                   text(note, "bad_note", 200, optional=True), now_iso()))
            if kind == "drop":   # the drawer's loss is the safe's gain
                self.store.db.execute("INSERT INTO safe_entries(op_id,kind,amount,note,at) VALUES(?,?,?,?,?)",
                                      (op, "drop", amount, "shift %d" % shift["id"], now_iso()))
            return {"shift": shift["id"]}
        return self.store.command("cash_move", key, [kind, amount, note], actor, work)

    def shift_expected(self, shift_id):
        return self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM cash_entries WHERE shift_id=?", (shift_id,))

    def shift_summary(self, actor, shift_id=None):
        shift = self.store.one("SELECT * FROM shifts WHERE id=?", (shift_id,)) if shift_id else self._open_shift_of(actor, False)
        if not shift:
            return None
        if shift["user_id"] != actor["id"]:
            require(actor, "close_others_shift")
        by_kind = self.store.all("SELECT kind, SUM(amount) amount, COUNT(*) n FROM cash_entries WHERE shift_id=? GROUP BY kind",
                                 (shift["id"],))
        noncash = self.store.all("""SELECT p.method, p.eff_state state, SUM(p.amount) amount FROM payment_states p
            JOIN sales s ON s.id=p.sale_id WHERE s.shift_id=? AND p.method<>'cash' GROUP BY p.method, p.eff_state""", (shift["id"],))
        out = {"shift": shift, "sales": self.store.scalar("SELECT COUNT(*) FROM sales WHERE shift_id=?", (shift["id"],)),
               "noncash": noncash}
        # blind close: the cashier sees the expected drawer only after counting
        if shift["closed_at"] or can(actor, "close_others_shift"):
            out["by_kind"] = by_kind
            out["expected"] = self.shift_expected(shift["id"])
        return out

    def close_shift(self, actor, key, counted, note=None, shift_id=None):
        require(actor, "shift")
        integer(counted)
        note = text(note, "bad_note", 200, optional=True)
        if shift_id is not None:
            integer(shift_id, 1)

        def work(op):
            if shift_id:
                shift = self.store.one("SELECT * FROM shifts WHERE id=? AND closed_at IS NULL", (shift_id,))
                if not shift:
                    raise ShopError("no_open_shift")
                if shift["user_id"] != actor["id"]:
                    require(actor, "close_others_shift")
            else:
                shift = self._open_shift_of(actor)
            expected = self.shift_expected(shift["id"])
            diff = counted - expected  # recorded as counted; never a retry-until-zero loop
            self.store.db.execute("""UPDATE shifts SET closed_at=?, counted=?, expected=?, difference=?, close_note=?, closed_by=?
                WHERE id=?""", (now_iso(), counted, expected, diff, note, actor["id"], shift["id"]))
            return {"shift": shift["id"], "expected": expected, "counted": counted, "difference": diff}
        return self.store.command("shift_close", key, [counted, note, shift_id], actor, work)

    # =============== approvals ===============
    _approval_failures = {}

    def verify_approval(self, approval, actor=None):
        """Check typed manager credentials before the command transaction.
        Failures are limited per REQUESTING user (5 per 5 minutes) and do not
        lock the manager's own login. Returns {'id', 'role'} or None."""
        if not approval:
            return None
        if not isinstance(approval, dict) or not isinstance(approval.get("username"), str) \
                or not isinstance(approval.get("password"), str):
            raise ShopError("bad_approval")
        import time
        who = actor["id"] if actor else 0
        recent = [t for t in self._approval_failures.get(who, []) if time.monotonic() - t < 300]
        if len(recent) >= 5:
            raise ShopError("approval_attempts_exceeded", status=429)
        try:
            with self.store.lock:
                user = self.auth.verify(approval["username"], approval["password"], count_failures=False)
        except ShopError:
            self._approval_failures[who] = recent + [time.monotonic()]
            raise
        return {"id": user["id"], "role": user["role"]}

    @staticmethod
    def _approved(actor, approver, permission):
        if can(actor, permission):
            return actor["id"]
        if approver and permission in ROLES[approver["role"]]:
            return approver["id"]
        raise Forbidden("approval_required", {"permission": permission})

    # =============== selling ===============
    def _price(self, actor, payload, settings, approver=None, check=True):
        """Authoritative prices for a cart (used by quote and by sell). With
        check=False nothing is approved or refused for permissions."""
        approved_by = None
        lines, seen_packs = [], set()
        for raw in payload["lines"]:
            if not isinstance(raw, dict):
                raise ShopError("bad_line")
            pack = None
            if raw.get("pack_code"):
                pack = self.store.one("SELECT * FROM packs WHERE code=?", (str(raw["pack_code"]),))
                if not pack:
                    raise ShopError("unknown_pack")
                if pack["status"] != "available" or pack["removed_op"] is not None or pack["id"] in seen_packs:
                    raise ShopError("pack_already_sold", {"pack": pack["code"]})
                seen_packs.add(pack["id"])
                prod = self.store.one("SELECT * FROM products WHERE id=?", (pack["product_id"],))
                qty = pack["net_g"]
            else:
                prod = self.store.one("SELECT * FROM products WHERE id=?", (integer(raw.get("product_id"), 1),))
                if not prod:
                    raise ShopError("unknown_product")
                qty = integer(raw.get("qty"), 1, "bad_quantity", 10_000_000)
            if prod["kind"] in ("ingredient", "packaging"):
                raise ShopError("not_for_sale", {"product": prod["code"]})
            current = self.current_price(prod["id"])
            if pack and settings["pack_price_policy"] == "label":
                gross, unit_price, basis = pack["price"], pack["price_per_kg"] or pack["price"], "pack_label"
            else:
                if current is None:
                    raise ShopError("no_price", {"product": prod["code"]})
                unit_price = current
                if raw.get("unit_price") is not None and integer(raw["unit_price"]) != current:
                    approved_by = None if not check else self._approved(actor, approver, "price_override")
                    unit_price = raw["unit_price"]
                basis = "per_kg" if prod["measure"] == "g" else "per_unit"
                gross = weighed_amount(unit_price, qty) if prod["measure"] == "g" else unit_price * qty
            line_discount = integer(raw.get("line_discount", 0))
            if line_discount > gross:
                raise ShopError("discount_exceeds_price")
            lines.append(dict(prod=prod, qty=qty, pack=pack, unit_price=unit_price, basis=basis, gross=gross,
                              line_discount=line_discount, scanned=str(raw.get("scanned") or "")[:128] or None))
        gross_total = sum(l["gross"] for l in lines)
        line_disc = sum(l["line_discount"] for l in lines)
        inv_disc = integer(payload.get("invoice_discount", 0))
        if inv_disc > gross_total - line_disc:
            raise ShopError("discount_exceeds_price")
        discount = line_disc + inv_disc
        if discount:
            require(actor, "discount") if (check and not approver) else None
            if discount * 1000 > gross_total * settings["cashier_discount_permille"]:
                approved_by = None if not check else self._approved(actor, approver, "discount_large")
            elif not can(actor, "discount"):
                approved_by = None if not check else self._approved(actor, approver, "discount")
        shares = allocate(inv_disc, [l["gross"] - l["line_discount"] for l in lines])
        total = gross_total - discount
        return dict(lines=lines, gross_total=gross_total, line_disc=line_disc, inv_disc=inv_disc, discount=discount,
                    shares=shares, total=total, approved_by=approved_by)

    @staticmethod
    def _quote_hash(priced):
        """Fingerprint of everything the cashier saw: every line (product,
        quantity, price, basis, discount, pack) and the invoice discount/total."""
        rows = [[l["prod"]["id"], l["qty"], l["unit_price"], l["basis"], l["gross"], l["line_discount"],
                 l["pack"]["code"] if l["pack"] else None] for l in priced["lines"]]
        blob = json.dumps([rows, priced["inv_disc"], priced["total"]], separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def quote(self, actor, payload):
        """Price the cart now, from the server, without posting anything (R14).
        The till shows this total and sends it back as expected_total."""
        require(actor, "sell")
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list) or not payload["lines"]:
            raise ShopError("lines_required")
        priced = self._price(actor, payload, self.settings(), check=False)
        return {"total": priced["total"], "gross": priced["gross_total"], "discount": priced["discount"],
                "quote_hash": self._quote_hash(priced),
                "lines": [{"product": l["prod"]["id"], "qty": l["qty"], "unit_price": l["unit_price"], "gross": l["gross"],
                           "basis": l["basis"], "pack": l["pack"]["code"] if l["pack"] else None} for l in priced["lines"]]}

    def sell(self, actor, key, payload, approver=None):
        """payload: lines [{product_id, qty, pack_code?, scanned?, line_discount?,
        unit_price?}], invoice_discount, customer_id, payments [{method, amount,
        reference}] (non-cash), cash_tendered. Prices/costs come from the server."""
        require(actor, "sell")
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list) or not payload["lines"]:
            raise ShopError("lines_required")
        if len(payload["lines"]) > 500:
            raise ShopError("too_many_lines")

        def work(op):
            shift = self._open_shift_of(actor)
            settings = self.settings()
            day = today()
            approved_by = None
            customer = payload.get("customer_id")
            if customer is not None:
                if not self.store.scalar("SELECT 1 FROM customers WHERE id=?", (integer(customer, 1),)):
                    raise ShopError("unknown_customer")
            priced = self._price(actor, payload, settings, approver)
            lines, gross_total, line_disc, inv_disc = priced["lines"], priced["gross_total"], priced["line_disc"], priced["inv_disc"]
            discount, shares, total, approved_by = priced["discount"], priced["shares"], priced["total"], priced["approved_by"]
            expected = payload.get("expected_total")
            shown_hash = payload.get("quote_hash")
            if (expected is not None and integer(expected) != total) or \
                    (shown_hash is not None and shown_hash != self._quote_hash(priced)):
                # the till showed another total: tell the cashier, change nothing
                raise Conflict("price_changed", {"total": total, "shown": expected, "quote_hash": self._quote_hash(priced),
                                                 "lines": [{"product": l["prod"]["id"], "unit_price": l["unit_price"],
                                                            "gross": l["gross"]} for l in lines]})
            # ---- payments ----
            payments = payload.get("payments", [])
            if not isinstance(payments, list):
                raise ShopError("bad_payment")
            noncash = 0
            credit_in_sale = 0
            clean_payments = []
            for pay in payments:
                method = pay.get("method") if isinstance(pay, dict) else None
                if method not in ("card", "wallet", "transfer", "credit"):
                    raise ShopError("bad_payment")
                amount = integer(pay.get("amount"), 1)
                noncash += amount
                ref = text(pay.get("reference"), "bad_reference", 80, optional=True)
                state = "pending" if method in ("wallet", "transfer") else "settled"
                if method == "credit":
                    if not customer:
                        raise ShopError("credit_needs_customer")
                    credit_ok = self._approved(actor, approver, "credit_sale")
                    approved_by = approved_by or credit_ok
                    credit_in_sale += amount
                    balance = self.customer_balance(customer)
                    limit = self.store.scalar("SELECT credit_limit FROM customers WHERE id=?", (customer,))
                    if balance + credit_in_sale > limit:
                        raise ShopError("credit_limit", {"balance": balance, "limit": limit})
                clean_payments.append((method, amount, ref, state))
            if noncash > total:
                raise ShopError("payment_exceeds_total")
            cash_due = total - noncash
            tendered = integer(payload.get("cash_tendered", cash_due))
            if tendered < cash_due:
                raise ShopError("cash_short", {"due": cash_due})
            change = tendered - cash_due
            if change > 0 and cash_due == 0:
                raise ShopError("change_without_cash")
            # ---- allocate stock (FEFO) and check margins before the sale row ----
            planned = []
            for line, share in zip(lines, shares):
                net = line["gross"] - line["line_discount"] - share
                allocations = self._allocate_sale(op, line, day)
                cost = sum(a[3] for a in allocations)
                if settings["block_below_cost"] and net < cost:
                    below_ok = self._approved(actor, approver, "discount_large")
                    approved_by = approved_by or below_ok
                planned.append((line, share, net, allocations, cost))
            number = self.store.next_seq("sale")
            no = ("TR-%06d" if self.store.mode == "training" else "S-%06d") % number
            sid = self.store.db.execute(
                """INSERT INTO sales(op_id,no,shift_id,user_id,customer_id,at,gross,line_discounts,invoice_discount,total,
                cash_tendered,change_given,approved_by,training) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (op, no, shift["id"], actor["id"], customer, now_iso(), gross_total, line_disc, inv_disc, total,
                 tendered, change, approved_by, int(self.store.mode == "training"))).lastrowid
            total_cost, out_lines = 0, []
            for line, share, net, allocations, cost in planned:
                lid = self.store.db.execute(
                    """INSERT INTO sale_lines(sale_id,product_id,qty,unit_price,price_basis,gross,line_discount,
                    invoice_discount_share,net,cost,pack_id,scanned) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid, line["prod"]["id"], line["qty"], line["unit_price"], line["basis"], line["gross"],
                     line["line_discount"], share, net, cost, line["pack"]["id"] if line["pack"] else None,
                     line["scanned"])).lastrowid
                for lot, loc, qty, c in allocations:
                    self.store.db.execute("INSERT INTO sale_allocations VALUES(?,?,?,?,?)", (lid, lot, loc, qty, c))
                if line["pack"]:
                    updated = self.store.db.execute(
                        "UPDATE packs SET status='sold', sale_line_id=? WHERE id=? AND status='available'",
                        (lid, line["pack"]["id"])).rowcount
                    if updated != 1:
                        raise ShopError("pack_already_sold")
                total_cost += cost
                out_lines.append({"line": lid, "product": line["prod"]["id"], "name_ar": line["prod"]["name_ar"],
                                  "name_en": line["prod"]["name_en"], "measure": line["prod"]["measure"],
                                  "qty": line["qty"], "unit_price": line["unit_price"], "gross": line["gross"],
                                  "discount": line["line_discount"] + share, "net": net})
            for method, amount, ref, state in clean_payments:
                pid = self.store.db.execute(
                    "INSERT INTO payments(op_id,sale_id,method,amount,reference,state,at) VALUES(?,?,?,?,?,?,?)",
                    (op, sid, method, amount, ref, state, now_iso())).lastrowid
                if method == "credit":
                    self.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                          (customer, op, "sale", amount, now_iso()))
            if cash_due:
                self.store.db.execute("INSERT INTO payments(op_id,sale_id,method,amount,reference,state,at) VALUES(?,?,?,?,?,?,?)",
                                      (op, sid, "cash", cash_due, None, "settled", now_iso()))
                self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                      (shift["id"], op, "sale", cash_due, no, now_iso()))
            self.store.db.execute("INSERT INTO outbox(kind,ref,payload,status,created_at) VALUES('receipt',?,'{}','pending',?)",
                                  (str(sid), now_iso()))
            result = {"sale": sid, "no": no, "total": total, "gross": gross_total, "discount": discount,
                      "cash_due": cash_due, "tendered": tendered, "change": change, "lines": out_lines,
                      "payments": [{"method": m, "amount": a, "state": s} for m, a, _, s in clean_payments],
                      "training": self.store.mode == "training", "approved_by": approved_by}
            if can(actor, "see_cost"):
                result["cost"] = total_cost
            result["receipt"] = self.receipt_model(sid)
            return result

        fingerprint_payload = {k: v for k, v in payload.items() if k != "approval"}
        return self.store.command("sale", key, fingerprint_payload, actor, work)

    def _label_constraint(self, prod, scanned, day):
        """Lot/expiry printed on the scanned label (GS1 AI 10/17). The sale must
        take exactly that stock; FEFO never hides a different physical piece."""
        if not scanned:
            return None
        try:
            info = bc.classify(scanned, {}, self.label_templates(), self.settings()["pack_prefix"])
        except bc.ScanError as exc:
            raise ShopError(exc.code) from exc
        fields = info.get("fields") or {}
        expiry, batch = fields.get("expiry"), fields.get("batch")
        if not expiry and not batch:
            return None
        if expiry and expiry < day:
            raise ShopError("scanned_label_expired", {"expiry": expiry})
        gtin = fields.get("gtin")
        if gtin and self.store.scalar("SELECT product_id FROM barcodes WHERE code=?", (gtin,)) not in (None, prod["id"]):
            raise ShopError("scanned_label_other_product")
        return {"expiry": expiry, "batch": batch}

    @staticmethod
    def _matches_label(cand, constraint):
        if constraint["batch"] and cand.get("supplier_lot") != constraint["batch"]:
            return False
        if constraint["expiry"] and cand.get("expires") != constraint["expiry"]:
            return False
        return True

    def _allocate_sale(self, op, line, day):
        prod, qty = line["prod"], line["qty"]
        constraint = None if line["pack"] else self._label_constraint(prod, line.get("scanned"), day)
        if line["pack"]:
            lot = line["pack"]["lot_id"]
            self._usable_lot(lot, day)
            rows = self.store.all("""SELECT b.location_id, b.qty FROM stock_balances b JOIN locations c ON c.id=b.location_id
                WHERE b.lot_id=? AND c.sellable=1 AND b.qty>0 ORDER BY b.location_id IS NOT ?, c.id""",
                                  (lot, line["pack"]["location_id"]))
            left, out = qty, []
            for r in rows:
                take = min(left, r["qty"])
                out.append((lot, r["location_id"], take, self._consume(op, "sale", lot, r["location_id"], take, check=False)))
                left -= take
                if not left:
                    break
            if left:
                raise ShopError("insufficient_stock", {"product": prod["code"]})
            return out
        left, out = qty, []
        for cand in self.sellable_lots(prod["id"], day):
            if constraint and not self._matches_label(cand, constraint):
                continue
            # stock promised to labelled packs at this place is not loose stock
            free = cand["qty"] - self._packed(cand["lot_id"], cand["location_id"])
            take = min(left, free)
            if take <= 0:
                continue
            out.append((cand["lot_id"], cand["location_id"], take,
                        self._consume(op, "sale", cand["lot_id"], cand["location_id"], take, check=False)))
            left -= take
            if not left:
                break
        if left:
            if constraint:
                raise ShopError("scanned_lot_not_in_stock", {"batch": constraint["batch"], "expiry": constraint["expiry"]})
            raise ShopError("insufficient_stock", {"product": prod["code"], "short": left})
        return out

    def confirm_payment(self, actor, key, payment_id, outcome):
        require(actor, "confirm_payments")
        if outcome not in ("confirmed", "rejected"):
            raise ShopError("bad_request")

        def work(op):
            pay = self.store.one("SELECT * FROM payments WHERE id=?", (integer(payment_id, 1),))
            if not pay or pay["state"] != "pending":
                raise ShopError("payment_not_pending")
            if self.store.scalar("SELECT 1 FROM payment_confirmations WHERE payment_id=?", (payment_id,)):
                raise ShopError("payment_already_decided")
            self.store.db.execute("INSERT INTO payment_confirmations VALUES(?,?,?,?,?)",
                                  (payment_id, op, outcome, actor["id"], now_iso()))
            follow_up = None
            if outcome == "rejected":
                customer = self.store.scalar("SELECT customer_id FROM sales WHERE id=?", (pay["sale_id"],))
                if customer:   # the money never arrived: the known customer owes it
                    self.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                          (customer, op, "rejected_payment", pay["amount"], now_iso()))
                    follow_up = "customer_debt"
                else:
                    follow_up = "uncollected"   # listed in the day report until resolved
            return {"payment": payment_id, "outcome": outcome, "follow_up": follow_up}
        return self.store.command("payment_confirm", key, [payment_id, outcome], actor, work)

    def pending_payments(self, actor):
        require(actor, "confirm_payments")
        return self.store.all("""SELECT p.*, s.no FROM payments p JOIN sales s ON s.id=p.sale_id
            WHERE p.state='pending' AND p.id NOT IN (SELECT payment_id FROM payment_confirmations) ORDER BY p.id""")

    # =============== customers' accounts ===============
    def customer_balance(self, customer_id):
        return self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM customer_entries WHERE customer_id=?", (customer_id,))

    def customer_payment(self, actor, key, customer_id, amount):
        require(actor, "shift")
        integer(customer_id, 1), integer(amount, 1)

        def work(op):
            shift = self._open_shift_of(actor)
            balance = self.customer_balance(customer_id)
            if amount > balance:
                raise ShopError("payment_exceeds_debt", {"balance": balance})
            eid = self.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                        (customer_id, op, "payment", -amount, now_iso())).lastrowid
            left = amount
            for sale in self.store.all("SELECT id FROM sales WHERE customer_id=? ORDER BY id", (customer_id,)):
                due = self._sale_outstanding(sale["id"])
                take = min(left, due)
                if take > 0:   # oldest open sale first; any rest stays unallocated (opening balance)
                    self.store.db.execute("INSERT INTO credit_allocations VALUES(?,?,?)", (eid, sale["id"], take))
                    left -= take
                if not left:
                    break
            self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                  (shift["id"], op, "debt_collect", amount, "customer %d" % customer_id, now_iso()))
            return {"entry": eid, "balance": balance - amount}
        return self.store.command("customer_payment", key, [customer_id, amount], actor, work)

    def reverse_customer_payment(self, actor, key, entry_id, reason):
        require(actor, "cash_moves")
        reason = text(reason, "reason_required", 200)

        def work(op):
            entry = self.store.one("SELECT * FROM customer_entries WHERE id=?", (integer(entry_id, 1),))
            if not entry or entry["kind"] != "payment":
                raise ShopError("not_a_payment")
            if self.store.scalar("SELECT 1 FROM customer_entries WHERE reverses=?", (entry_id,)):
                raise ShopError("already_reversed")
            self._require_account_clear(entry["customer_id"])
            for a in self.store.all("SELECT sale_id, amount FROM credit_allocations WHERE entry_id=?", (entry_id,)):
                if a["amount"] > self._collected_free(a["sale_id"]):
                    # a refund already paid this money back: reversing now would pay it twice
                    raise ShopError("collection_already_refunded", {"sale": a["sale_id"]})
            shift = self._open_shift_of(actor)
            self._check_drawer(shift["id"], -entry["amount"])
            rev = self.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at,reverses) VALUES(?,?,?,?,?,?)",
                                        (entry["customer_id"], op, "payment_reversal", -entry["amount"], now_iso(), entry_id)).lastrowid
            for a in self.store.all("SELECT sale_id, amount FROM credit_allocations WHERE entry_id=?", (entry_id,)):
                self.store.db.execute("INSERT INTO credit_allocations VALUES(?,?,?)", (rev, a["sale_id"], -a["amount"]))
            self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                  (shift["id"], op, "debt_collect", entry["amount"], reason, now_iso()))
            self.store.audit(actor, "customer.payment_reversal", str(entry_id), {"reason": reason})
            return {"balance": self.customer_balance(entry["customer_id"])}
        return self.store.command("customer_payment_reverse", key, [entry_id, reason], actor, work)

    def customer_statement(self, actor, customer_id):
        require(actor, "shift") if not can(actor, "reports") else None
        customer = self.store.one("SELECT * FROM customers WHERE id=?", (integer(customer_id, 1),))
        if not customer:
            raise ShopError("unknown_customer")
        lines = self.store.all("""SELECT e.id, e.kind, e.amount, e.at, e.reverses, s.no sale_no,
            (SELECT 1 FROM customer_entries r WHERE r.reverses=e.id) reversed
            FROM customer_entries e LEFT JOIN sales s ON s.op_id=e.op_id WHERE e.customer_id=? ORDER BY e.id""", (customer_id,))
        running = 0
        for line in lines:
            running += line["amount"]
            line["balance"] = running
        return {"customer": customer, "lines": lines, "balance": running}

    def supplier_statement(self, actor, supplier_id):
        if not (can(actor, "supplier_pay") or can(actor, "receive")):
            raise Forbidden("forbidden", {"permission": "supplier_pay"})
        supplier = self.store.one("SELECT * FROM suppliers WHERE id=?", (integer(supplier_id, 1),))
        if not supplier:
            raise ShopError("unknown_supplier")
        lines = self.store.all("""SELECT e.id, e.kind, e.amount, e.at, r.supplier_invoice FROM supplier_entries e
            LEFT JOIN receipts r ON r.op_id=e.op_id WHERE e.supplier_id=? ORDER BY e.id""", (supplier_id,))
        running = 0
        for line in lines:
            running += line["amount"]
            line["balance"] = running
        return {"supplier": supplier, "lines": lines, "balance": running}

    # =============== returns ===============
    def sale_detail(self, actor, sale_id):
        sale = self.store.one("SELECT * FROM sales WHERE id=? OR no=?", (sale_id, str(sale_id)))
        if not sale:
            raise ShopError("unknown_sale")
        if sale["user_id"] != actor["id"]:
            require(actor, "reports") if not can(actor, "refund") else None
        lines = self.store.all("""SELECT sl.*, p.name_ar, p.name_en, p.measure, p.kind,
            (SELECT COALESCE(SUM(qty),0) FROM return_lines WHERE sale_line_id=sl.id) returned_qty,
            (SELECT COALESCE(SUM(amount),0) FROM return_lines WHERE sale_line_id=sl.id) returned_amount
            FROM sale_lines sl JOIN products p ON p.id=sl.product_id WHERE sl.sale_id=?""", (sale["id"],))
        if not can(actor, "see_cost"):
            for l in lines:
                l.pop("cost", None)
        sale["lines"] = lines
        sale["payments"] = self.store.all("SELECT id, method, amount, eff_state state, reference FROM payment_states WHERE sale_id=?",
                                          (sale["id"],))
        sale["cost_total"] = sum(l.get("cost", 0) for l in lines) if can(actor, "see_cost") else None
        return sale

    def return_sale(self, actor, key, payload, approver=None):
        """payload: sale_id, lines [{sale_line_id, qty, restock?}], reason,
        method cash|credit_note. Meat and prepared food always go to quarantine."""
        require(actor, "refund")
        if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list) or not payload["lines"]:
            raise ShopError("lines_required")
        reason, method = payload.get("reason"), payload.get("method", "original")
        if reason not in ("quality", "change_of_mind", "wrong_item", "other") or method not in ("cash", "credit_note", "original"):
            raise ShopError("bad_request")

        def work(op):
            sale = self.store.one("SELECT * FROM sales WHERE id=?", (integer(payload.get("sale_id"), 1),))
            if not sale:
                raise ShopError("unknown_sale")
            if method == "credit_note" and not sale["customer_id"]:
                raise ShopError("credit_needs_customer")
            if self.store.scalar("SELECT 1 FROM payment_states WHERE sale_id=? AND eff_state='pending'", (sale["id"],)):
                raise ShopError("sale_has_unconfirmed_payment")
            seen, total, rows = set(), 0, []
            for raw in payload["lines"]:
                line = self.store.one("SELECT sl.*, p.kind pkind, p.measure FROM sale_lines sl JOIN products p ON p.id=sl.product_id WHERE sl.id=? AND sl.sale_id=?",
                                      (integer(raw.get("sale_line_id"), 1), sale["id"]))
                if not line or line["id"] in seen:
                    raise ShopError("bad_line")
                seen.add(line["id"])
                qty = integer(raw.get("qty"), 1)
                if line["pack_id"] and qty != line["qty"]:
                    raise ShopError("pack_returns_whole")
                prev = self.store.one("SELECT COALESCE(SUM(qty),0) q, COALESCE(SUM(amount),0) a, COALESCE(SUM(cost),0) c FROM return_lines WHERE sale_line_id=?",
                                      (line["id"],))
                if prev["q"] + qty > line["qty"]:
                    raise ShopError("return_exceeds_sold", {"left": line["qty"] - prev["q"]})
                amount = cumulative_share(line["net"], line["qty"], prev["q"], prev["a"], qty)
                cost = cumulative_share(line["cost"], line["qty"], prev["q"], prev["c"], qty)
                restock = bool(raw.get("restock")) and line["pkind"] == "grocery" and reason in ("change_of_mind", "wrong_item")
                rows.append((line, qty, amount, cost, restock))
                total += amount
            refunds = self._refund_plan(sale, total, method)
            cash_out = refunds.get("cash", 0)
            if sale["customer_id"]:
                self._require_account_clear(sale["customer_id"])
            shift = self._open_shift_of(actor) if cash_out else self._open_shift_of(actor, False)
            if cash_out:
                self._check_drawer(shift["id"], cash_out)
            approved_by = None
            if reason == "change_of_mind" and any(r[0]["pkind"] in ("raw_meat", "prepared") for r in rows):
                approved_by = self._approved(actor, approver, "release_hold")
            rid = self.store.db.execute(
                """INSERT INTO returns(op_id,sale_id,shift_id,reason,method,amount,user_id,approved_by,at,note)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (op, sale["id"], shift["id"] if shift else None, reason, method, total, actor["id"], approved_by, now_iso(),
                 text(payload.get("note"), "bad_note", 300, optional=True))).lastrowid
            blocked = self.recall_blocked()
            out = []
            for line, qty, amount, cost, restock in rows:
                allocations = self.store.all("SELECT sa.*, l.expires FROM sale_allocations sa JOIN lots l ON l.id=sa.lot_id WHERE sale_line_id=?",
                                             (line["id"],))
                if any(a["lot_id"] in blocked for a in allocations):
                    restock = False   # goods from a recalled lot never go back on the shelf
                expiries = [a["expires"] for a in allocations if a["expires"]]
                expires = min(expiries) if expiries else None
                allergens = ",".join(sorted({x for a in allocations for x in (self.store.scalar("SELECT allergens FROM lots WHERE id=?", (a["lot_id"],)) or "").split(",") if x}))
                status = "released" if restock else "held"
                lot, lot_no = self._new_lot(op, line["product_id"], "return", expires, status, allergens=allergens)
                for a in allocations:
                    self.store.db.execute("INSERT OR IGNORE INTO lot_edges VALUES(?,?)", (a["lot_id"], lot))
                location = allocations[0]["location_id"] if restock else self._quarantine_location()
                self._entry(op, "return", lot, location, qty, cost)
                self.store.db.execute("INSERT INTO return_lines VALUES(?,?,?,?,?,?,?)",
                                      (rid, line["id"], qty, amount, cost, lot, "restock" if restock else "quarantine"))
                if line["pack_id"]:
                    self.store.db.execute("UPDATE packs SET status='returned' WHERE id=?", (line["pack_id"],))
                out.append({"sale_line": line["id"], "qty": qty, "amount": amount, "lot": lot_no,
                            "disposition": "restock" if restock else "quarantine"})
            for channel, amount in refunds.items():
                if channel == "uncollected_cancel":
                    self.store.db.execute("INSERT INTO refund_cancellations VALUES(?,?,?)", (rid, "uncollected", amount))
                else:
                    self.store.db.execute("INSERT INTO refund_allocations VALUES(?,?,?)", (rid, channel, amount))
            refunds_paid = {k: v for k, v in refunds.items() if k != "uncollected_cancel"}
            if cash_out:
                self.store.db.execute("INSERT INTO cash_entries(shift_id,op_id,kind,amount,note,at) VALUES(?,?,?,?,?,?)",
                                      (shift["id"], op, "refund", -cash_out, sale["no"], now_iso()))
            customer_credit = refunds.get("credit_cancel", 0) + refunds.get("store_credit", 0)
            if customer_credit:
                eid = self.store.db.execute("INSERT INTO customer_entries(customer_id,op_id,kind,amount,at) VALUES(?,?,?,?,?)",
                                            (sale["customer_id"], op, "return", -customer_credit, now_iso())).lastrowid
                if refunds.get("credit_cancel"):   # a cancelled debt SETTLES the sale: positive, like a collection
                    self.store.db.execute("INSERT INTO credit_allocations VALUES(?,?,?)", (eid, sale["id"], refunds["credit_cancel"]))
            return {"return": rid, "amount": total, "lines": out, "reference_receipt": sale["no"],
                    "cancelled_uncollected": refunds.get("uncollected_cancel", 0),
                    "refunds": [{"method": m, "amount": a} for m, a in refunds_paid.items()]}
        fingerprint_payload = {k: v for k, v in payload.items() if k != "approval"}
        return self.store.command("return", key, fingerprint_payload, actor, work)

    def receipt_model(self, sale_id, reprint=False):
        """One receipt shape for the first print and every reprint (R18):
        the SAVED sale time, effective payment states, discounts, training mark."""
        sale = self.store.one("""SELECT s.*, u.display_name cashier FROM sales s JOIN users u ON u.id=s.user_id
            WHERE s.id=?""", (sale_id,))
        if not sale:
            raise ShopError("unknown_sale")
        lines = self.store.all("""SELECT sl.qty, sl.unit_price, sl.price_basis, sl.gross, sl.line_discount + sl.invoice_discount_share
            discount, sl.net, p.name_ar, p.name_en, p.measure, k.code pack FROM sale_lines sl JOIN products p ON p.id=sl.product_id
            LEFT JOIN packs k ON k.id=sl.pack_id WHERE sl.sale_id=? ORDER BY sl.id""", (sale_id,))
        payments = self.store.all("SELECT method, amount, eff_state state FROM payment_states WHERE sale_id=? ORDER BY id",
                                  (sale_id,))
        return {"no": sale["no"], "at": sale["at"], "cashier": sale["cashier"], "training": bool(sale["training"]),
                "shop_name": self.settings()["shop_name"], "footer": self.settings()["receipt_footer"],
                "lines": lines, "gross": sale["gross"], "discount": sale["line_discounts"] + sale["invoice_discount"],
                "total": sale["total"], "payments": payments, "cash_tendered": sale["cash_tendered"],
                "change": sale["change_given"], "reprint": reprint, "kind": "sales_receipt_not_tax_invoice"}

    def reprint(self, actor, sale_id):
        sale = self.store.one("SELECT id, user_id FROM sales WHERE id=? OR no=?", (sale_id, str(sale_id)))
        if not sale:
            raise ShopError("unknown_sale")
        if sale["user_id"] != actor["id"] and not (can(actor, "refund") or can(actor, "reports")):
            raise Forbidden("forbidden", {"permission": "reports"})
        self.store.tx(lambda: self.store.audit(actor, "receipt.reprint", str(sale["id"])))
        return self.receipt_model(sale["id"], reprint=True)

    # =============== refund and credit bookkeeping ===============
    CHANNELS = ("cash", "card", "wallet", "transfer")

    def _settled_on_sale(self, sale_id):
        """Debt settled on a sale: collections add, reversals subtract, and a
        return that cancelled debt adds (rows written by the first version of
        the return code carry a negative sign for it, so kind 'return' is
        read by absolute value)."""
        return self.store.scalar("""SELECT COALESCE(SUM(CASE e.kind WHEN 'return' THEN ABS(a.amount) ELSE a.amount END),0)
            FROM credit_allocations a JOIN customer_entries e ON e.id=a.entry_id WHERE a.sale_id=?""", (sale_id,))

    def _owed_on_sale(self, sale_id):
        return self.store.scalar("""SELECT COALESCE(SUM(amount),0) FROM payment_states WHERE sale_id=?
            AND ((method='credit' AND eff_state='settled') OR eff_state='rejected')""", (sale_id,))

    def _sale_outstanding(self, sale_id):
        """Customer debt still open on this sale: on-account amounts (and
        rejected transfers of a known customer) minus collections and
        cancellations allocated to the sale."""
        if not self.store.scalar("SELECT customer_id FROM sales WHERE id=?", (sale_id,)):
            return 0
        return self._owed_on_sale(sale_id) - self._settled_on_sale(sale_id)

    def _sale_uncollected(self, sale_id):
        """A rejected transfer/wallet payment of an UNREGISTERED customer: money
        that never arrived. Returned goods cancel this claim instead of paying."""
        if self.store.scalar("SELECT customer_id FROM sales WHERE id=?", (sale_id,)):
            return 0
        rejected = self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM payment_states WHERE sale_id=? AND eff_state='rejected'", (sale_id,))
        done = self.store.scalar("""SELECT COALESCE(SUM(c.amount),0) FROM refund_cancellations c JOIN returns r ON r.id=c.return_id
            WHERE r.sale_id=? AND c.kind='uncollected'""", (sale_id,))
        return rejected - done

    def _cash_refunded(self, sale_id):
        new = self.store.scalar("""SELECT COALESCE(SUM(a.amount),0) FROM refund_allocations a JOIN returns r ON r.id=a.return_id
            WHERE r.sale_id=? AND a.method='cash'""", (sale_id,))
        legacy = self.store.scalar("""SELECT COALESCE(SUM(amount),0) FROM returns r WHERE sale_id=? AND method='cash'
            AND NOT EXISTS(SELECT 1 FROM refund_allocations a WHERE a.return_id=r.id)""", (sale_id,))
        return new + legacy

    def _collected_free(self, sale_id):
        """Collected on-account money of this sale that no refund has used yet.
        Refunds pay direct cash first; only the rest came out of collections."""
        collected = self.store.scalar("""SELECT COALESCE(SUM(a.amount),0) FROM credit_allocations a
            JOIN customer_entries e ON e.id=a.entry_id WHERE a.sale_id=? AND e.kind IN ('payment','payment_reversal')""", (sale_id,))
        direct = self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM payment_states WHERE sale_id=? AND method='cash' AND eff_state='settled'",
                                   (sale_id,))
        consumed = max(0, self._cash_refunded(sale_id) - direct)
        return max(0, collected - consumed)

    # ---- customer account integrity (migration reviews and ambiguous collections)
    def _unallocated_collections(self, customer_id):
        """Collection money (not reversed) that is not tied to any invoice."""
        total = 0
        for e in self.store.all("""SELECT e.id, e.amount FROM customer_entries e WHERE e.customer_id=? AND e.kind='payment'
                AND NOT EXISTS(SELECT 1 FROM customer_entries r WHERE r.reverses=e.id)""", (customer_id,)):
            allocated = self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM credit_allocations WHERE entry_id=?", (e["id"],))
            total += max(0, -e["amount"] - allocated)
        return total

    def account_check(self, customer_id):
        problems = []
        if self.store.scalar("SELECT 1 FROM migration_reviews WHERE kind='customer_allocation' AND ref=? AND resolved_at IS NULL",
                             (str(customer_id),)):
            problems.append("account_needs_review")
        open_total = 0
        for s in self.store.all("SELECT id FROM sales WHERE customer_id=?", (customer_id,)):
            left = self._owed_on_sale(s["id"]) - self._settled_on_sale(s["id"])
            if left < 0:
                problems.append("sale_over_collected:%d" % s["id"])
            open_total += max(left, 0)
        if open_total > 0 and self._unallocated_collections(customer_id) > 0:
            problems.append("collections_unallocated")
        return {"ok": not problems, "problems": problems, "open_on_sales": open_total}

    def _require_account_clear(self, customer_id):
        check = self.account_check(customer_id)
        if "account_needs_review" in check["problems"]:
            raise ShopError("account_needs_review", {"customer": customer_id})
        if "collections_unallocated" in check["problems"]:
            raise ShopError("collections_unallocated", {"customer": customer_id})

    def review_items(self, actor):
        require(actor, "users")
        return self.store.all("SELECT * FROM migration_reviews ORDER BY resolved_at IS NOT NULL, id")

    def allocate_collection(self, actor, key, entry_id, sale_id, amount, note):
        """Owner action for a review item: tie unallocated collection money to
        one invoice. Adds a row; nothing posted is changed."""
        require(actor, "users")
        integer(entry_id, 1), integer(sale_id, 1), integer(amount, 1)
        note = text(note, "reason_required", 300)

        def work(op):
            e = self.store.one("SELECT * FROM customer_entries WHERE id=? AND kind='payment'", (entry_id,))
            s = self.store.one("SELECT * FROM sales WHERE id=?", (sale_id,))
            if not e or not s or s["customer_id"] != e["customer_id"]:
                raise ShopError("bad_request")
            free = -e["amount"] - self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM credit_allocations WHERE entry_id=?", (entry_id,))
            if amount > free or amount > self._sale_outstanding(sale_id):
                raise ShopError("allocation_too_large", {"free": free, "open": self._sale_outstanding(sale_id)})
            self.store.db.execute("INSERT INTO credit_allocations VALUES(?,?,?)", (entry_id, sale_id, amount))
            self.store.audit(actor, "customer.allocation", str(entry_id), {"sale": sale_id, "amount": amount, "note": note})
            return {"free_left": free - amount}
        return self.store.command("allocate_collection", key, [entry_id, sale_id, amount, note], actor, work)

    def resolve_review(self, actor, key, review_id, resolution):
        require(actor, "users")
        resolution = text(resolution, "reason_required", 300)

        def work(op):
            r = self.store.one("SELECT * FROM migration_reviews WHERE id=?", (integer(review_id, 1),))
            if not r:
                raise ShopError("bad_request")
            if r["resolved_at"]:
                raise ShopError("review_already_resolved")
            if r["kind"] == "customer_allocation" and self._unallocated_collections(int(r["ref"])) > 0 \
                    and self.account_check(int(r["ref"]))["open_on_sales"] > 0:
                raise ShopError("collections_unallocated", {"customer": int(r["ref"])})
            self.store.db.execute("UPDATE migration_reviews SET resolved_at=?, resolved_by=?, resolution=? WHERE id=?",
                                  (now_iso(), actor["id"], resolution, review_id))
            return {"resolved": review_id}
        return self.store.command("resolve_review", key, [review_id, resolution], actor, work)

    def _refund_caps(self, sale_id):
        """Money that can still go back per channel for this sale."""
        caps = {c: 0 for c in self.CHANNELS}
        for r in self.store.all("""SELECT method, SUM(amount) a FROM payment_states WHERE sale_id=? AND eff_state='settled'
                AND method<>'credit' GROUP BY method""", (sale_id,)):
            caps[r["method"]] += r["a"]
        # on-account money the customer has since paid (in cash) can come back as cash
        caps["cash"] += self.store.scalar("""SELECT COALESCE(SUM(a.amount),0) FROM credit_allocations a
            JOIN customer_entries e ON e.id=a.entry_id WHERE a.sale_id=? AND e.kind IN ('payment','payment_reversal')""",
                                          (sale_id,))
        refunded = {}
        for r in self.store.all("""SELECT a.method, SUM(a.amount) amount FROM refund_allocations a JOIN returns r
                ON r.id=a.return_id WHERE r.sale_id=? GROUP BY a.method""", (sale_id,)):
            refunded[r["method"]] = r["amount"]
        for r in self.store.all("""SELECT method, SUM(amount) amount FROM returns r WHERE sale_id=? AND NOT EXISTS
                (SELECT 1 FROM refund_allocations a WHERE a.return_id=r.id) GROUP BY method""", (sale_id,)):
            legacy = {"cash": "cash", "credit_note": "store_credit", "original": "card"}[r["method"]]   # before migration 2
            refunded[legacy] = refunded.get(legacy, 0) + r["amount"]
        for c in self.CHANNELS:
            caps[c] -= refunded.get(c, 0)
        left = refunded.get("store_credit", 0)        # store credit used money in this order
        for c in self.CHANNELS:
            used = min(left, max(caps[c], 0))
            caps[c] -= used
            left -= used
        return {c: max(v, 0) for c, v in caps.items()}

    def _refund_plan(self, sale, amount, method):
        """Split a refund: first cancel what the customer still owes on this
        sale, then give money back the way it was paid (or as store credit
        when explicitly chosen). Never more than was really received."""
        plan = {}
        cancel = min(amount, max(self._sale_outstanding(sale["id"]), 0))
        if cancel:
            plan["credit_cancel"] = cancel
        rest = amount - cancel
        # money that never arrived (rejected transfer, unregistered customer): the goods may come
        # back, the claim is cancelled, nothing is paid out for it (audit E05)
        lost = min(rest, max(self._sale_uncollected(sale["id"]), 0))
        if lost:
            plan["uncollected_cancel"] = lost
            rest -= lost
        if not rest:
            return plan
        caps = self._refund_caps(sale["id"])
        if rest > sum(caps.values()):
            raise ShopError("refund_exceeds_paid_by_method", {"left": sum(caps.values())})
        if method == "credit_note":
            plan["store_credit"] = rest
        elif method == "cash":
            if rest > caps["cash"]:
                raise ShopError("refund_exceeds_paid_by_method", {"method": "cash", "left": caps["cash"]})
            plan["cash"] = rest
        else:
            for channel, part in zip(self.CHANNELS, allocate(rest, [caps[c] for c in self.CHANNELS])):
                if part:
                    plan[channel] = part
        return plan

    def _check_drawer(self, shift_id, amount):
        """Cash leaving the drawer must be there (R06): no silent negative drawer."""
        have = self.shift_expected(shift_id)
        if amount > have:
            raise ShopError("drawer_insufficient", {"drawer": have, "needed": amount})

    def safe_balance(self):
        return self.store.scalar("SELECT COALESCE(SUM(amount),0) FROM safe_entries")

    def _check_safe(self, amount):
        have = self.safe_balance()
        if amount > have:
            raise ShopError("safe_insufficient", {"safe": have, "needed": amount})

    # =============== stock moves: transfer, waste, holds, recall ===============
    def transfer(self, actor, key, lot_id, from_location, to_location, qty):
        require(actor, "transfer")

        def work(op):
            integer(lot_id, 1), integer(qty, 1)
            src, dst = self._location(integer(from_location, 1)), self._location(integer(to_location, 1))
            if src["id"] == dst["id"]:
                raise ShopError("same_location")
            if not self.store.scalar("SELECT 1 FROM lots WHERE id=?", (lot_id,)):
                raise ShopError("unknown_lot")
            self._check_free(lot_id, src["id"], qty)
            cost = self._consume(op, "transfer_out", lot_id, src["id"], qty, check=False)
            self._entry(op, "transfer_in", lot_id, dst["id"], qty, cost)
            return {"lot": lot_id, "qty": qty}
        return self.store.command("transfer", key, [lot_id, from_location, to_location, qty], actor, work)

    WASTE_REASONS = ("expired", "damaged", "cold_chain", "internal_use", "sample", "owner_draw", "abnormal_loss", "unknown")

    def waste(self, actor, key, lot_id, location_id, qty, reason, note=None):
        require(actor, "waste")
        note = text(note, "bad_note", 200, optional=True)
        if reason not in self.WASTE_REASONS:
            raise ShopError("bad_reason")

        def work(op):
            integer(lot_id, 1)
            location = self._location(integer(location_id, 1))["id"]
            self._check_free(lot_id, location, integer(qty, 1))
            cost = self._consume(op, "waste", lot_id, location, qty, reason, check=False)
            return {"lot": lot_id, "qty": qty, "cost": cost, "reason": reason}
        return self.store.command("waste", key, [lot_id, location_id, qty, reason, note], actor, work)

    def _packed(self, lot_id, location_id):
        return self.store.scalar("""SELECT COALESCE(SUM(net_g),0) FROM packs WHERE lot_id=? AND location_id IS ?
            AND status='available' AND removed_op IS NULL""", (lot_id, location_id))

    def _check_free(self, lot_id, location_id, qty):
        """Stock promised to labelled packs can only leave WITH its pack (R12)."""
        free = self._balance(lot_id, location_id)[0] - self._packed(lot_id, location_id)
        if qty > free:
            raise ShopError("stock_reserved_by_packs", {"free": max(free, 0)})

    def waste_pack(self, actor, key, code, reason, note=None):
        """Write off one labelled pack: its stock leaves and the pack can never sell."""
        require(actor, "waste")
        if reason not in self.WASTE_REASONS:
            raise ShopError("bad_reason")
        note = text(note, "bad_note", 200, optional=True)

        def work(op):
            pack = self.store.one("SELECT * FROM packs WHERE code=?", (text(code, "bad_code", 20),))
            if not pack:
                raise ShopError("unknown_pack")
            if pack["status"] != "available" or pack["removed_op"] is not None:
                raise ShopError("pack_not_available")
            cost = self._consume(op, "waste", pack["lot_id"], pack["location_id"], pack["net_g"], reason, check=False)
            self.store.db.execute("UPDATE packs SET removed_op=? WHERE id=?", (op, pack["id"]))
            return {"pack": pack["code"], "qty": pack["net_g"], "cost": cost, "reason": reason}
        return self.store.command("waste_pack", key, [code, reason, note], actor, work)

    def set_lot_status(self, actor, key, lot_id, status, reason):
        """Hold / release a lot. Releasing a recalled lot needs the recall
        permission; plain releases need release_hold."""
        if status not in ("held", "released"):
            raise ShopError("bad_request")
        reason = text(reason, "reason_required", 200)

        def work(op):
            lot = self.store.one("SELECT * FROM lots WHERE id=?", (integer(lot_id, 1),))
            if not lot:
                raise ShopError("unknown_lot")
            if lot["status"] == "recalled":
                require(actor, "recall")
            if status == "released":
                require(actor, "release_hold")
                if lot["origin"] == "return" and lot["status"] == "held":
                    require(actor, "release_hold")
            else:
                require(actor, "transfer") if not can(actor, "release_hold") else None
            if lot["status"] == status:
                raise ShopError("status_unchanged")
            if status == "released" and lot_id in self.recall_blocked():
                # only lifting the recall itself (separate, documented act) can end it
                raise ShopError("lot_under_recall", {"lot": lot["lot_no"]})
            self.store.db.execute("UPDATE lots SET status=? WHERE id=?", (status, lot_id))
            self.store.db.execute("INSERT INTO lot_events(lot_id,status,reason,op_id,at) VALUES(?,?,?,?,?)",
                                  (lot_id, status, reason, op, now_iso()))
            return {"lot": lot_id, "status": status}
        return self.store.command("lot_status", key, [lot_id, status, reason], actor, work)

    def descendants(self, lot_id):
        return [r["id"] for r in self.store.all("""WITH RECURSIVE tree(id) AS (
            SELECT ? UNION SELECT e.child FROM lot_edges e JOIN tree ON e.parent=tree.id) SELECT id FROM tree ORDER BY id""",
                                                (lot_id,))]

    def ancestors(self, lot_id):
        return [r["id"] for r in self.store.all("""WITH RECURSIVE tree(id) AS (
            SELECT ? UNION SELECT e.parent FROM lot_edges e JOIN tree ON e.child=tree.id) SELECT id FROM tree ORDER BY id""",
                                                (lot_id,))]

    def recall(self, actor, key, lot_id, reason):
        require(actor, "recall")
        reason = text(reason, "reason_required", 200)

        def work(op):
            if not self.store.scalar("SELECT 1 FROM lots WHERE id=?", (integer(lot_id, 1),)):
                raise ShopError("unknown_lot")
            affected = self.descendants(lot_id)
            for lid in affected:
                if self.store.scalar("SELECT status FROM lots WHERE id=?", (lid,)) != "recalled":
                    self.store.db.execute("UPDATE lots SET status='recalled' WHERE id=?", (lid,))
                    self.store.db.execute("INSERT INTO lot_events(lot_id,status,reason,op_id,at) VALUES(?,?,?,?,?)",
                                          (lid, "recalled", reason, op, now_iso()))
            self.store.db.execute("INSERT INTO recalls(op_id,lot_id,reason,user_id,at) VALUES(?,?,?,?,?)",
                                  (op, lot_id, reason, actor["id"], now_iso()))
            return {"lots": affected, **self._impact(affected)}
        return self.store.command("recall", key, [lot_id, reason], actor, work)

    def lift_recall(self, actor, key, recall_id, reason):
        """End an active recall with a written reason (evidence such as a
        supplier/authority letter). Lots stay recalled/held until each one is
        inspected and released one by one."""
        require(actor, "recall")
        reason = text(reason, "reason_required", 300)

        def work(op):
            recall = self.store.one("SELECT * FROM recalls WHERE id=?", (integer(recall_id, 1),))
            if not recall:
                raise ShopError("unknown_recall")
            if self.store.scalar("SELECT 1 FROM recall_lifts WHERE recall_id=?", (recall_id,)):
                raise ShopError("recall_already_lifted")
            self.store.db.execute("INSERT INTO recall_lifts VALUES(?,?,?,?,?)", (recall_id, op, reason, actor["id"], now_iso()))
            return {"recall": recall_id, "still_blocked": sorted(self.recall_blocked() & set(self.descendants(recall["lot_id"])))}
        return self.store.command("recall_lift", key, [recall_id, reason], actor, work)

    def recalls(self, actor):
        require(actor, "recall") if not can(actor, "reports") else None
        return self.store.all("""SELECT r.*, l.lot_no, p.name_ar, p.name_en, f.reason lift_reason, f.at lifted_at
            FROM recalls r JOIN lots l ON l.id=r.lot_id JOIN products p ON p.id=l.product_id
            LEFT JOIN recall_lifts f ON f.recall_id=r.id ORDER BY r.id DESC""")

    def _impact(self, lots):
        marks = ",".join("?" * len(lots))
        sales = self.store.all(f"""SELECT DISTINCT s.id, s.no, s.at, s.customer_id FROM sale_allocations a
            JOIN sale_lines sl ON sl.id=a.sale_line_id JOIN sales s ON s.id=sl.sale_id WHERE a.lot_id IN ({marks})
            ORDER BY s.id""", lots)
        stock = self.store.all(f"""SELECT b.lot_id, l.lot_no, b.location_id, b.qty FROM stock_balances b JOIN lots l ON l.id=b.lot_id
            WHERE b.lot_id IN ({marks}) AND b.qty>0""", lots)
        packs = self.store.all(f"SELECT code, status FROM packs WHERE lot_id IN ({marks})", lots)
        return {"sales": sales, "stock": stock, "packs": packs}

    def trace(self, actor, lot_id):
        require(actor, "reports") if not can(actor, "recall") else None
        lot = self.store.one("SELECT l.*, p.name_ar, p.name_en, p.code product_code, p.measure FROM lots l JOIN products p ON p.id=l.product_id WHERE l.id=? OR l.lot_no=?",
                             (lot_id, str(lot_id)))
        if not lot:
            raise ShopError("unknown_lot")
        down = self.descendants(lot["id"])
        up = self.ancestors(lot["id"])
        names = {r["id"]: r for r in self.store.all(
            "SELECT l.id, l.lot_no, l.status, l.expires, p.name_ar, p.name_en FROM lots l JOIN products p ON p.id=l.product_id WHERE l.id IN (%s)"
            % ",".join("?" * len(set(up + down))), sorted(set(up + down)))}
        events = self.store.all("SELECT status, reason, at FROM lot_events WHERE lot_id=? ORDER BY id", (lot["id"],))
        moves = self.store.all("SELECT kind, reason, location_id, qty, at FROM stock_entries WHERE lot_id=? ORDER BY id", (lot["id"],))
        return {"lot": lot, "parents": [names[i] for i in up if i != lot["id"]],
                "children": [names[i] for i in down if i != lot["id"]], "events": events, "moves": moves,
                **self._impact(down)}

    # =============== recipes & production ===============
    def save_recipe(self, actor, product_id, components, shelf_life_days, allergens, loss_tolerance_permille,
                    target_pack_g=None, note=None):
        """Create the next immutable recipe version for a prepared product."""
        require(actor, "recipes")

        def work():
            prod = self.store.one("SELECT * FROM products WHERE id=?", (integer(product_id, 1),))
            if not prod or prod["kind"] != "prepared":
                raise ShopError("recipe_needs_prepared_product")
            if not isinstance(components, list) or not components:
                raise ShopError("components_required")
            integer(shelf_life_days), integer(loss_tolerance_permille, 0, maximum=1000)
            recipe = self.store.one("SELECT * FROM recipes WHERE product_id=?", (product_id,))
            rid = recipe["id"] if recipe else self.store.db.execute(
                "INSERT INTO recipes(product_id,created_at) VALUES(?,?)", (product_id, now_iso())).lastrowid
            version = (self.store.scalar("SELECT MAX(version) FROM recipe_versions WHERE recipe_id=?", (rid,)) or 0) + 1
            vid = self.store.db.execute(
                """INSERT INTO recipe_versions(recipe_id,version,shelf_life_days,allergens,target_pack_g,
                loss_tolerance_permille,note,approved_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (rid, version, shelf_life_days, str(allergens or "")[:200],
                 integer(target_pack_g, 1) if target_pack_g else None, loss_tolerance_permille,
                 text(note, "bad_note", 300, optional=True), actor["id"], now_iso())).lastrowid
            seen = set()
            for c in components:
                comp = self.store.one("SELECT * FROM products WHERE id=?", (integer(c.get("product_id"), 1),))
                if not comp or comp["id"] in seen or comp["id"] == product_id:
                    raise ShopError("bad_component")
                seen.add(comp["id"])
                role = c.get("role")
                if role not in ("meat", "food", "packaging") or (role == "packaging") != (comp["kind"] == "packaging"):
                    raise ShopError("bad_component_role")
                if role == "meat" and comp["kind"] != "raw_meat":
                    # the role a user types never makes a product meat: the approved
                    # classification does (audit E03). Composites declare a documented share instead.
                    raise ShopError("meat_role_needs_meat_product", {"product": comp["code"]})
                self.store.db.execute("INSERT INTO recipe_components VALUES(?,?,?,?)",
                                      (vid, comp["id"], integer(c.get("qty"), 1), role))
                for alt_id in c.get("alternatives") or []:
                    alt = self.store.one("SELECT * FROM products WHERE id=?", (integer(alt_id, 1),))
                    if not alt or alt["id"] in seen or (alt["kind"] == "packaging") != (role == "packaging") \
                            or alt["measure"] != comp["measure"]:
                        raise ShopError("bad_alternative")
                    if role == "meat" and alt["kind"] != "raw_meat":
                        raise ShopError("meat_role_needs_meat_product", {"product": alt["code"]})
                    seen.add(alt["id"])   # an approved substitute counts toward this component
                    self.store.db.execute("INSERT INTO recipe_alternatives VALUES(?,?,?)", (vid, comp["id"], alt["id"]))
            self.store.audit(actor, "recipe.version", str(vid), {"product": product_id, "version": version})
            return {"recipe_version": vid, "version": version}
        return self.store.tx(work)

    def recipe(self, product_id, actor=None):
        if actor is not None and not any(can(actor, p) for p in ("produce", "cutting", "recipes")):
            raise Forbidden("forbidden", {"permission": "produce"})
        versions = self.store.all("""SELECT v.* FROM recipe_versions v JOIN recipes r ON r.id=v.recipe_id
            WHERE r.product_id=? ORDER BY v.version DESC""", (product_id,))
        for v in versions:
            v["components"] = self.store.all("""SELECT c.*, p.name_ar, p.name_en, p.measure FROM recipe_components c
                JOIN products p ON p.id=c.product_id WHERE version_id=?""", (v["id"],))
            for c in v["components"]:
                c["alternatives"] = [r["product_id"] for r in self.store.all(
                    "SELECT product_id FROM recipe_alternatives WHERE version_id=? AND component_product_id=?",
                    (v["id"], c["product_id"]))]
        return versions

    @staticmethod
    def _meat_grams(product_kind, meat_permille, qty):
        """Meat inside `qty` grams of a product, from its approved classification
        only: raw meat is meat, a composite counts its documented share, anything
        else counts nothing."""
        if product_kind == "raw_meat":
            return qty
        if meat_permille is not None:
            return qty * meat_permille // 1000
        return 0

    def _unpack(self, op, codes, inputs):
        """Explicit un-packing: the listed labelled packs stop existing and their
        net weight becomes loose raw stock of the same lot and place, consumed by
        this very production. Each pack, quantity and lot is recorded."""
        if not codes:
            return []
        if not isinstance(codes, list) or len(codes) > 200:
            raise ShopError("bad_request")
        wanted = {(i.get("lot_id"), i.get("location_id")) for i in inputs if isinstance(i, dict)}
        out = []
        for code in codes:
            pack = self.store.one("SELECT * FROM packs WHERE code=?", (str(code),))
            if not pack:
                raise ShopError("unknown_pack")
            if pack["status"] != "available" or pack["removed_op"] is not None:
                raise ShopError("pack_not_available")
            if (pack["lot_id"], pack["location_id"]) not in wanted:
                raise ShopError("unpack_pack_not_in_inputs", {"pack": pack["code"]})
            self.store.db.execute("UPDATE packs SET removed_op=? WHERE id=?", (op, pack["id"]))
            self.store.db.execute("INSERT INTO pack_events(pack_id,op_id,kind,qty,lot_id,at) VALUES(?,?,'unpack',?,?,?)",
                                  (pack["id"], op, pack["net_g"], pack["lot_id"], now_iso()))
            out.append(pack["code"])
        return out

    def _check_recipe_use(self, actor, approver, version, components, used, packs, units, output_g):
        """Every component must be used, in the version's quantity per unit
        (basis: packs made, declared units, or output / target pack weight).
        Off by more than the tolerance -> manager approval; missing -> refused."""
        if packs:
            n = len(packs)
        elif units is not None:
            n = integer(units, 1)
        elif version["target_pack_g"] and output_g % version["target_pack_g"] == 0:
            n = output_g // version["target_pack_g"]
        else:
            raise ShopError("recipe_units_required")
        tolerance = self.settings()["recipe_component_tolerance_permille"]
        approved_by = None
        for c in components:
            expected, actual = c["qty"] * n, used.get(c["product_id"], 0)
            if not actual:
                raise ShopError("recipe_component_missing", {"product": c["product_id"], "expected": expected})
            if c["role"] == "packaging":
                off = actual != expected
            else:
                off = abs(actual - expected) * 1000 > expected * tolerance
            if off:
                approved_by = self._approved(actor, approver, "approve_variance")
        return approved_by

    def produce(self, actor, key, payload, approver=None):
        """Cutting or recipe production. inputs [{lot_id, location_id, qty}];
        outputs [{product_id, qty, location_id, expires?, value_weight?}];
        natural_loss_g, abnormal_loss_g, cost_method weight|value; for a recipe:
        recipe_version_id and optional packs [{net_g, tare_g, meat_g}] for the
        single output. Mass: food grams in = output grams + losses."""
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if kind not in ("cutting", "recipe"):
            raise ShopError("bad_request")
        require(actor, "cutting" if kind == "cutting" else "produce")

        def work(op):
            p = payload
            day = today()
            inputs, outputs = p.get("inputs"), p.get("outputs")
            if not isinstance(inputs, list) or not inputs or not isinstance(outputs, list) or not outputs:
                raise ShopError("inputs_outputs_required")
            natural = integer(p.get("natural_loss_g", 0))
            abnormal = integer(p.get("abnormal_loss_g", 0))
            method = p.get("cost_method", "weight")
            if method not in ("weight", "value"):
                raise ShopError("bad_cost_method")
            version = None
            if kind == "recipe":
                version = self.store.one("""SELECT v.*, r.product_id FROM recipe_versions v JOIN recipes r ON r.id=v.recipe_id
                    WHERE v.id=?""", (integer(p.get("recipe_version_id"), 1),))
                if not version:
                    raise ShopError("unknown_recipe_version")
                components = self.store.all("SELECT * FROM recipe_components WHERE version_id=?", (version["id"],))
                allowed = {c["product_id"]: c["product_id"] for c in components}
                allowed.update({r["product_id"]: r["component_product_id"] for r in self.store.all(
                    "SELECT * FROM recipe_alternatives WHERE version_id=?", (version["id"],))})
                if len(outputs) != 1 or outputs[0].get("product_id") != version["product_id"]:
                    raise ShopError("recipe_single_output")
            seen, food_in, food_cost, pack_cost, expiries, parents, allergens = set(), 0, 0, 0, [], [], set()
            used = {}   # recipe component -> quantity actually consumed
            meat_in = 0  # grams of meat in the inputs, from approved classification only
            unpacked = self._unpack(op, p.get("unpack"), inputs)
            for raw in inputs:
                if not isinstance(raw, dict):
                    raise ShopError("bad_line")
                lot_id, loc_id, qty = integer(raw.get("lot_id"), 1), integer(raw.get("location_id"), 1), integer(raw.get("qty"), 1)
                if (lot_id, loc_id) in seen:
                    raise ShopError("duplicate_input")
                seen.add((lot_id, loc_id))
                lot = self._usable_lot(lot_id, day)
                self._check_free(lot_id, loc_id, qty)   # stock promised to labelled packs stays (audit E02)
                if kind == "recipe":
                    if lot["product_id"] not in allowed:
                        raise ShopError("input_not_in_recipe", {"lot": lot["lot_no"]})
                    component = allowed[lot["product_id"]]
                    used[component] = used.get(component, 0) + qty
                if lot["pkind"] == "packaging":
                    pack_cost += self._consume(op, "production_in", lot_id, loc_id, qty, check=False)
                else:
                    if lot["measure"] != "g":
                        raise ShopError("food_input_must_be_weighed")
                    if not lot["expires"]:
                        raise ShopError("input_expiry_missing", {"lot": lot["lot_no"]})
                    expiries.append(lot["expires"])
                    meat_in += self._meat_grams(lot["pkind"], self.store.scalar(
                        "SELECT meat_permille FROM products WHERE id=?", (lot["product_id"],)), qty)
                    food_in += qty
                    food_cost += self._consume(op, "production_in", lot_id, loc_id, qty, check=False)
                parents.append(lot_id)
                allergens.update(x for x in (lot["allergens"] or "").split(",") if x)
            if not food_in:
                raise ShopError("food_input_required")
            clean_out = []
            for raw in outputs:
                if not isinstance(raw, dict):
                    raise ShopError("bad_line")
                prod = self.store.one("SELECT * FROM products WHERE id=?", (integer(raw.get("product_id"), 1),))
                if not prod or prod["measure"] != "g" or prod["kind"] in ("packaging",):
                    raise ShopError("bad_output_product")
                if kind == "cutting" and prod["kind"] == "prepared":
                    raise ShopError("prepared_needs_recipe")
                weight = integer(raw.get("value_weight", 1), 1) if method == "value" else 1
                clean_out.append(dict(prod=prod, qty=integer(raw.get("qty"), 1), loc=self._location(integer(raw.get("location_id"), 1))["id"],
                                      expires=iso_date(raw.get("expires"), optional=True), weight=weight))
            output_g = sum(o["qty"] for o in clean_out)
            if food_in != output_g + natural + abnormal:
                raise ShopError("mass_not_balanced", {"in": food_in, "out": output_g, "loss": natural + abnormal})
            approved_by = None
            if kind == "recipe":
                tolerance = version["loss_tolerance_permille"]
            else:
                tolerance = self.settings()["cutting_loss_tolerance_permille"]
            if abnormal or (natural and (tolerance is None or natural * 1000 > food_in * tolerance)):
                approved_by = self._approved(actor, approver, "approve_variance")
            packs = p.get("packs") or []
            if version:
                approved_by = self._check_recipe_use(actor, approver, version, components, used, packs, p.get("units"),
                                                     output_g) or approved_by
            limit = min(expiries)
            if version:
                limit = min(limit, add_days(day, version["shelf_life_days"]))
            abnormal_cost = food_cost * abnormal // food_in
            to_outputs = food_cost - abnormal_cost + pack_cost
            parts = allocate(to_outputs, [o["qty"] * o["weight"] for o in clean_out])
            prod_id = self.store.db.execute(
                """INSERT INTO productions(op_id,kind,recipe_version_id,input_cost,food_in_g,output_g,natural_loss_g,
                abnormal_loss_g,abnormal_loss_cost,cost_method,approved_by,user_id,at,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (op, kind, version["id"] if version else None, food_cost + pack_cost, food_in, output_g, natural, abnormal,
                 abnormal_cost, method, approved_by, actor["id"], now_iso(),
                 text(p.get("note"), "bad_note", 300, optional=True))).lastrowid
            if version:
                allergens.update(x for x in version["allergens"].split(",") if x)
            out = []
            for o, cost in zip(clean_out, parts):
                expires = o["expires"] or limit
                if expires < day or expires > limit:
                    raise ShopError("output_expiry_out_of_bounds", {"limit": limit})
                lot_allergens = ",".join(sorted(allergens | {x for x in o["prod"]["allergens"].split(",") if x}))
                lot, lot_no = self._new_lot(op, o["prod"]["id"], "production", expires, "released", allergens=lot_allergens)
                for parent in sorted(set(parents)):
                    self.store.db.execute("INSERT OR IGNORE INTO lot_edges VALUES(?,?)", (parent, lot))
                self._entry(op, "production_out", lot, o["loc"], o["qty"], cost)
                out.append({"lot": lot, "lot_no": lot_no, "product": o["prod"]["id"], "name_ar": o["prod"]["name_ar"],
                            "name_en": o["prod"]["name_en"], "qty": o["qty"], "cost": cost,
                            "expires": expires, "allergens": lot_allergens})
            if packs:
                if len(out) != 1:
                    raise ShopError("packs_need_single_output")
                total_net = 0
                price_kg = self.current_price(out[0]["product"])
                if price_kg is None:
                    raise ShopError("no_price")
                made = []
                for pk in packs:
                    net, tare = integer(pk.get("net_g"), 1), integer(pk.get("tare_g", 0))
                    basis = None
                    meat = integer(pk["meat_g"], 1) if pk.get("meat_g") is not None else None
                    if meat is not None and meat > net:
                        raise ShopError("meat_exceeds_net")
                    if version:
                        # meat on the label comes from meat really used, shared by weight;
                        # losses are assumed to fall evenly on every ingredient
                        most = meat_in * output_g // food_in * net // output_g
                        if meat is None:
                            meat = most or None
                            basis = "estimated" if meat else None   # pro-rata share of the MEASURED meat; losses assumed even
                        elif meat > most:
                            raise ShopError("meat_claim_exceeds_input", {"max_g": most})
                        else:
                            basis = "declared"
                    total_net += net
                    code = bc.pack_code(self.settings()["pack_prefix"], self.store.next_seq("pack"))
                    self.store.db.execute(
                        """INSERT INTO packs(code,product_id,lot_id,net_g,tare_g,meat_g,price,price_per_kg,packed_at,expires,
                        status,location_id,meat_basis) VALUES(?,?,?,?,?,?,?,?,?,?,'available',?,?)""",
                        (code, out[0]["product"], out[0]["lot"], net, tare, meat, weighed_amount(price_kg, net), price_kg,
                         now_iso(), out[0]["expires"], clean_out[0]["loc"], basis))
                    made.append({"code": code, "net_g": net, "tare_g": tare, "meat_g": meat, "meat_basis": basis,
                                 "price": weighed_amount(price_kg, net)})
                if total_net != out[0]["qty"]:
                    raise ShopError("packs_do_not_match_output", {"packs": total_net, "output": out[0]["qty"]})
                out[0]["packs"] = made
            result = {"production": prod_id, "outputs": out, "abnormal_loss_cost": abnormal_cost, "approved_by": approved_by}
            if not can(actor, "see_cost"):
                for o in out:
                    o.pop("cost")
                result.pop("abnormal_loss_cost")
            return result
        fingerprint_payload = {k: v for k, v in payload.items() if k != "approval"}
        return self.store.command("production", key, fingerprint_payload, actor, work)

    # =============== counting ===============
    def open_count(self, actor, location_id):
        require(actor, "count")

        def work():
            self._location(integer(location_id, 1))
            if self.store.scalar("SELECT 1 FROM counts WHERE location_id=? AND status='open'", (location_id,)):
                raise ShopError("count_already_open")
            cutoff = self.store.scalar("SELECT COALESCE(MAX(id),0) FROM stock_entries")
            cid = self.store.db.execute("INSERT INTO counts(location_id,cutoff_entry,opened_by,opened_at,status) VALUES(?,?,?,?,'open')",
                                        (location_id, cutoff, actor["id"], now_iso())).lastrowid
            self.store.audit(actor, "count.open", str(cid))
            return {"count": cid}
        return self.store.tx(work)

    def count_sheet(self, actor, count_id):
        """Blind sheet: lots at the location, no expected quantities."""
        require(actor, "count")
        count = self.store.one("SELECT * FROM counts WHERE id=?", (count_id,))
        if not count:
            raise ShopError("unknown_count")
        lots = self.store.all("""SELECT DISTINCT l.id lot_id, l.lot_no, l.expires, p.name_ar, p.name_en, p.measure, p.code
            FROM stock_entries e JOIN lots l ON l.id=e.lot_id JOIN products p ON p.id=l.product_id
            WHERE e.location_id=? AND e.id<=? ORDER BY p.name_ar, l.expires""", (count["location_id"], count["cutoff_entry"]))
        counted = {r["lot_id"]: r["counted"] for r in self.store.all(
            "SELECT lot_id, counted FROM count_lines WHERE id IN (SELECT MAX(id) FROM count_lines WHERE count_id=? GROUP BY lot_id)",
            (count_id,))}
        for l in lots:
            l["counted"] = counted.get(l["lot_id"])
        return {"count": count, "lots": lots}

    def record_count(self, actor, count_id, lot_id, counted):
        require(actor, "count")

        def work():
            count = self.store.one("SELECT * FROM counts WHERE id=?", (integer(count_id, 1),))
            if not count or count["status"] != "open":
                raise ShopError("count_not_open")
            if not self.store.scalar("SELECT 1 FROM lots WHERE id=?", (integer(lot_id, 1),)):
                raise ShopError("unknown_lot")
            # the shelf was counted NOW: later approval compares with the ledger at
            # this position, so sales between opening and counting are not lost twice
            seen = self.store.scalar("SELECT COALESCE(MAX(id),0) FROM stock_entries")
            self.store.db.execute("INSERT INTO count_lines(count_id,lot_id,counted,seen_entry,user_id,at) VALUES(?,?,?,?,?,?)",
                                  (count_id, lot_id, integer(counted), seen, actor["id"], now_iso()))
            return {"ok": True}
        return self.store.tx(work)

    def approve_count(self, actor, key, count_id, owner_reason=None):
        require(actor, "count_approve")

        def work(op):
            count = self.store.one("SELECT * FROM counts WHERE id=?", (integer(count_id, 1),))
            if not count or count["status"] != "open":
                raise ShopError("count_not_open")
            counters = {r["user_id"] for r in self.store.all("SELECT DISTINCT user_id FROM count_lines WHERE count_id=?", (count_id,))}
            if actor["id"] in counters and not (actor["role"] == "owner" and owner_reason):
                raise Forbidden("approver_counted")  # owner exception only with a written reason
            latest = self.store.all("""SELECT lot_id, counted, seen_entry FROM count_lines WHERE id IN
                (SELECT MAX(id) FROM count_lines WHERE count_id=? GROUP BY lot_id)""", (count_id,))
            diffs = []
            for row in latest:
                expected = self.store.scalar("""SELECT COALESCE(SUM(qty),0) FROM stock_entries
                    WHERE lot_id=? AND location_id=? AND id<=?""", (row["lot_id"], count["location_id"], row["seen_entry"]))
                diff = row["counted"] - expected
                if not diff:
                    continue
                if diff < 0:
                    try:
                        self._check_free(row["lot_id"], count["location_id"], -diff)
                    except ShopError as exc:   # missing labelled packs: write off the packs themselves first
                        raise ShopError("count_affects_packs", {"lot": row["lot_id"], **exc.detail}) from exc
                    cost = self._consume(op, "count", row["lot_id"], count["location_id"], -diff, "count_difference", check=False)
                    diffs.append({"lot": row["lot_id"], "diff": diff, "cost": -cost})
                else:
                    qty, cost = self._balance(row["lot_id"], count["location_id"])
                    if not qty:
                        first = self.store.one("SELECT qty, cost FROM stock_entries WHERE lot_id=? AND qty>0 ORDER BY id LIMIT 1",
                                               (row["lot_id"],))
                        qty, cost = first["qty"], first["cost"]
                    add = cost * diff // qty
                    self._entry(op, "count", row["lot_id"], count["location_id"], diff, add, "count_difference")
                    diffs.append({"lot": row["lot_id"], "diff": diff, "cost": add})
            self.store.db.execute("UPDATE counts SET status='approved', approved_by=?, approved_at=?, approve_op=? WHERE id=?",
                                  (actor["id"], now_iso(), op, count_id))
            if actor["id"] in counters:
                self.store.audit(actor, "count.owner_self_approval", str(count_id), {"reason": text(owner_reason, "reason_required", 200)})
            return {"count": count_id, "differences": diffs, "self_approved_owner": actor["id"] in counters}
        return self.store.command("count_approve", key, [count_id, owner_reason], actor, work)

    # =============== reports ===============
    @staticmethod
    def can_see_qty(actor):
        """Quantities on hand. A pure counter must not see them (blind count)."""
        return any(can(actor, p) for p in ("reports", "receive", "transfer", "waste", "cutting", "produce", "sell"))

    def stock_report(self, actor, product_id=None):
        if not self.can_see_qty(actor):
            raise Forbidden("forbidden", {"permission": "reports"})
        near = self.settings()["near_expiry_days"]
        day = today()
        rows = self.store.all("""SELECT l.id lot_id, l.lot_no, l.status, l.expires, l.origin, p.id product_id, p.code, p.name_ar,
            p.name_en, p.measure, p.kind, p.needs_expiry, c.id location_id, c.name_ar loc_ar, c.name_en loc_en, c.sellable, b.qty, b.cost
            FROM stock_balances b JOIN lots l ON l.id=b.lot_id JOIN products p ON p.id=l.product_id JOIN locations c ON c.id=b.location_id
            WHERE b.qty>0 AND (? IS NULL OR p.id=?) ORDER BY p.name_ar, l.expires""", (product_id, product_id))
        for r in rows:
            if r["status"] != "released":
                r["state"] = r["status"]
            elif r["expires"] is None and r["needs_expiry"]:
                r["state"] = "expiry_missing"
            elif r["expires"] and r["expires"] < day:
                r["state"] = "expired"
            elif r["expires"] and r["expires"] <= add_days(day, near):
                r["state"] = "near_expiry"
            else:
                r["state"] = "available"
            if not can(actor, "see_cost"):
                r.pop("cost")
        return rows

    def day_report(self, actor, day=None):
        require(actor, "reports")
        day = iso_date(day) if day else today()
        like = day + "%"
        sales = self.store.one("""SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(line_discounts+invoice_discount),0) discounts
            FROM sales WHERE at LIKE ?""", (like,))
        cost = self.store.scalar("SELECT COALESCE(SUM(sl.cost),0) FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id WHERE s.at LIKE ?", (like,))
        returns = self.store.one("""SELECT COALESCE(SUM(r.amount),0) amount,
            COALESCE((SELECT SUM(cost) FROM return_lines rl JOIN returns r2 ON r2.id=rl.return_id WHERE r2.at LIKE ?),0) cost
            FROM returns r WHERE r.at LIKE ?""", (like, like))
        by_method = self.store.all("""SELECT p.method, p.eff_state state, COALESCE(SUM(p.amount),0) amount FROM payment_states p
            JOIN sales s ON s.id=p.sale_id WHERE s.at LIKE ? GROUP BY p.method, p.eff_state""", (like,))
        refunds = self.store.all("""SELECT a.method, SUM(a.amount) amount FROM refund_allocations a JOIN returns r
            ON r.id=a.return_id WHERE r.at LIKE ? GROUP BY a.method""", (like,))
        waste = self.store.all("""SELECT reason, COALESCE(SUM(-qty),0) qty, COALESCE(SUM(-cost),0) cost FROM stock_entries
            WHERE kind='waste' AND at LIKE ? GROUP BY reason""", (like,))
        abnormal = self.store.scalar("SELECT COALESCE(SUM(abnormal_loss_cost),0) FROM productions WHERE at LIKE ?", (like,))
        count_diff = self.store.scalar("SELECT COALESCE(SUM(cost),0) FROM stock_entries WHERE kind='count' AND at LIKE ?", (like,))
        shifts = self.store.all("SELECT id, user_id, opened_at, closed_at, expected, counted, difference FROM shifts WHERE opened_at LIKE ? OR closed_at LIKE ?",
                                (like, like))
        net_sales = sales["total"] - returns["amount"]
        out = {"day": day, "sales_count": sales["n"], "sales_total": sales["total"], "discounts": sales["discounts"],
               "returns": returns["amount"], "net_sales": net_sales, "by_method": by_method, "refunds_by_method": refunds,
               "safe": self.safe_balance() if can(actor, "safe") else None, "shifts": shifts,
               "waste": waste, "near_expiry": [r for r in self.stock_report(actor) if r["state"] in ("near_expiry", "expired")],
               "held": [r for r in self.stock_report(actor) if r["state"] in ("held", "recalled")],
               "pending_payments": self.store.scalar("""SELECT COUNT(*) FROM payments WHERE state='pending'
                    AND id NOT IN (SELECT payment_id FROM payment_confirmations)"""),
               # a rejected transfer/wallet is money NOT collected: shown, never hidden
               "rejected_payments": self.store.all("""SELECT s.no, p.method, p.amount, c.at FROM payment_confirmations c
                    JOIN payments p ON p.id=c.payment_id JOIN sales s ON s.id=p.sale_id
                    WHERE c.outcome='rejected' AND c.at LIKE ?""", (like,)),
               "notes": ["sales_not_cash", "margin_before_expenses", "tax_unverified"]}
        if can(actor, "see_cost"):
            waste_cost = sum(w["cost"] for w in waste) + abnormal - count_diff
            out.update(cost_of_sales=cost - returns["cost"], gross_margin=net_sales - (cost - returns["cost"]),
                       losses=waste_cost, margin_after_recorded_losses=net_sales - (cost - returns["cost"]) - waste_cost)
        return out

    def reconcile(self):
        """Derived balances must equal the append-only entries (rebuildable)."""
        bad = self.store.all("""SELECT b.lot_id, b.location_id FROM stock_balances b
            LEFT JOIN (SELECT lot_id, location_id, SUM(qty) q, SUM(cost) c FROM stock_entries GROUP BY lot_id, location_id) e
            ON e.lot_id=b.lot_id AND e.location_id=b.location_id WHERE e.q IS NOT b.qty OR e.c IS NOT b.cost""")
        packs = self.store.all("""SELECT p.code FROM packs p JOIN sale_lines sl ON sl.id=p.sale_line_id
            WHERE p.status='sold' AND sl.pack_id<>p.id""")
        missing = self.store.all("""SELECT e.lot_id, e.location_id FROM stock_entries e LEFT JOIN stock_balances b
            ON b.lot_id=e.lot_id AND b.location_id=e.location_id WHERE b.lot_id IS NULL GROUP BY e.lot_id, e.location_id""")
        pay = self.store.all("""SELECT s.id FROM sales s WHERE s.total <> (SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=s.id)""")
        # labelled packs promise stock: available packs never exceed what is physically there
        over = self.store.all("""SELECT p.lot_id, p.location_id, SUM(p.net_g) packed,
            COALESCE((SELECT qty FROM stock_balances b WHERE b.lot_id=p.lot_id AND b.location_id=p.location_id),0) stock
            FROM packs p WHERE p.status='available' AND p.removed_op IS NULL GROUP BY p.lot_id, p.location_id HAVING packed > stock""")
        unsold = self.store.all("""SELECT p.code FROM packs p WHERE p.status='sold' AND (p.sale_line_id IS NULL OR
            NOT EXISTS(SELECT 1 FROM sale_lines sl WHERE sl.id=p.sale_line_id AND sl.pack_id=p.id))""")
        accounts = []
        for c in self.store.all("SELECT id FROM customers"):
            check = self.account_check(c["id"])
            if not check["ok"]:
                accounts.append({"customer": c["id"], "problems": check["problems"]})
        ok = not (bad or packs or pay or missing or over or unsold or accounts)
        return {"ok": ok, "balances": bad, "missing_balances": missing, "packs": packs + unsold,
                "packs_over_stock": over, "payments": pay, "accounts": accounts}
