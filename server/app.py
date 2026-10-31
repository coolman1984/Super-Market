"""Local HTTP server: static UI + JSON API. Standard library only.

Security: session token in an HttpOnly SameSite=Strict cookie (only its
hash is stored), every state change is POST with a custom header and a
same-origin check, request bodies are capped, errors never leak paths or
tracebacks, LAN access is limited to private addresses.
"""

import argparse
import contextlib
import ipaddress
import json
import logging
import mimetypes
import pathlib
import re
import sqlite3
import sys
import threading
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import backup, training
from .auth import Auth, can, require
from .core import ShopError, integer, text
from .db import Store
from .scanner import ScannerHub, list_serial_ports
from .services import Shop
from .version import VERSION

ROOT = pathlib.Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MAX_BODY = 1_000_000
COOKIE = "sm_session"
log = logging.getLogger("supermarket")


class App:
    def __init__(self, data_folder, mode="shop", backup_folder=None, lan=False):
        self.lan = lan
        self.store = Store(data_folder, mode)
        self.shop = Shop(self.store)
        self.auth = Auth(self.store)
        self.hub = ScannerHub()
        self.backup_folder = pathlib.Path(backup_folder or pathlib.Path(data_folder).parent / ("backups-" + mode))
        if mode == "training":
            training.seed(self.shop)
        self.hub.configure(self.shop.settings().get("scanner_devices") or [])
        self.routes = []
        r = self.route
        r("GET", r"/api/state", self.state)
        r("POST", r"/api/setup", self.setup, auth=False)
        r("POST", r"/api/login", self.login, auth=False)
        r("POST", r"/api/logout", self.logout)
        r("GET", r"/api/products", lambda a, q, b: self.shop.search_products(q.get("q", ""), _int(q.get("limit"), 30), a))
        r("GET", r"/api/products/(\d+)", lambda a, q, b, pid: self.shop.product(int(pid), a))
        r("POST", r"/api/products", lambda a, q, b: {"id": self.shop.add_product(a, b)})
        r("POST", r"/api/barcodes", lambda a, q, b: self.shop.add_barcode(a, b.get("code"), b.get("product_id"), b.get("packaging_id")) or {"ok": True})
        r("POST", r"/api/prices", lambda a, q, b: self.shop.set_price(a, b.get("key"), b.get("product_id"), b.get("price")))
        r("GET", r"/api/locations", lambda a, q, b: self.shop.locations())
        r("POST", r"/api/locations", lambda a, q, b: {"id": self.shop.add_location(a, b.get("code"), b.get("name_ar"), b.get("name_en"), b.get("kind"), b.get("sellable"))})
        r("GET", r"/api/parties", self.parties)
        r("POST", r"/api/parties", lambda a, q, b: {"id": self.shop.add_party(a, b.get("kind"), b.get("name"), b.get("phone"), b.get("credit_limit", 0))})
        r("POST", r"/api/scan", lambda a, q, b: self.shop.resolve_scan(a, b.get("raw"), b.get("profile") or self.profile_for(a)))
        r("POST", r"/api/receipts", lambda a, q, b: self.shop.receive(a, b.get("key"), b.get("payload")))
        r("GET", r"/api/shift", lambda a, q, b: self.shop.shift_summary(a, _int(q.get("id"), None)))
        r("POST", r"/api/shift/open", lambda a, q, b: self.shop.open_shift(a, b.get("key"), b.get("opening_float")))
        r("POST", r"/api/shift/close", lambda a, q, b: self.shop.close_shift(a, b.get("key"), b.get("counted"), b.get("note"), b.get("shift_id")))
        r("POST", r"/api/cash", lambda a, q, b: self.shop.cash_move(a, b.get("key"), b.get("kind"), b.get("amount"), b.get("note")))
        r("POST", r"/api/quote", lambda a, q, b: self.shop.quote(a, b.get("payload")))
        r("GET", r"/api/sales/([\w-]+)/receipt", lambda a, q, b, sid: self.shop.reprint(a, sid))
        r("POST", r"/api/sales", self.sell)
        r("GET", r"/api/sales/([\w-]+)", lambda a, q, b, sid: self.shop.sale_detail(a, sid))
        r("POST", r"/api/returns", lambda a, q, b: self.shop.return_sale(a, b.get("key"), b.get("payload"), self.shop.verify_approval(b.get("approval"), a)))
        r("GET", r"/api/payments/pending", lambda a, q, b: self.shop.pending_payments(a))
        r("POST", r"/api/payments/confirm", lambda a, q, b: self.shop.confirm_payment(a, b.get("key"), b.get("payment_id"), b.get("outcome")))
        r("POST", r"/api/customers/pay", lambda a, q, b: self.shop.customer_payment(a, b.get("key"), b.get("customer_id"), b.get("amount")))
        r("POST", r"/api/suppliers/pay", lambda a, q, b: self.shop.supplier_payment(a, b.get("key"), b.get("supplier_id"), b.get("amount"), b.get("note"), b.get("source")))
        r("POST", r"/api/customers/reverse", lambda a, q, b: self.shop.reverse_customer_payment(a, b.get("key"), b.get("entry_id"), b.get("reason")))
        r("GET", r"/api/customers/(\d+)/statement", lambda a, q, b, cid: self.shop.customer_statement(a, int(cid)))
        r("GET", r"/api/suppliers/(\d+)/statement", lambda a, q, b, sid: self.shop.supplier_statement(a, int(sid)))
        r("POST", r"/api/supplier-returns", lambda a, q, b: self.shop.supplier_return(a, b.get("key"), b.get("supplier_id"), b.get("lot_id"), b.get("location_id"), b.get("qty"), b.get("reason"), b.get("note")))
        r("POST", r"/api/supplier-credit-notes", lambda a, q, b: self.shop.supplier_credit_note(a, b.get("key"), b.get("return_id"), b.get("amount"), b.get("reference")))
        r("GET", r"/api/aging", lambda a, q, b: self.shop.aging(a))
        r("GET", r"/api/supplier-invoices", lambda a, q, b: self.shop.supplier_invoices(a, _int(q.get("supplier"), None), q.get("all") != "1"))
        r("POST", r"/api/supplier-invoices", lambda a, q, b: self.shop.register_supplier_invoice(a, b.get("key"), b.get("payload")))
        r("POST", r"/api/supplier-invoices/close", lambda a, q, b: self.shop.close_supplier_invoice(a, b.get("key"), b.get("invoice_id"), b.get("reason")))
        r("POST", r"/api/supplier-adjustments", lambda a, q, b: self.shop.supplier_adjustment(a, b.get("key"), b.get("payload")))
        r("POST", r"/api/packagings/replace", lambda a, q, b: self.shop.replace_packaging(a, b.get("key"), b.get("packaging_id"), b.get("factor_num"), b.get("factor_den", 1), b.get("reason"), b.get("move_barcodes", True)))
        r("POST", r"/api/thaw", lambda a, q, b: self.shop.thaw(a, b.get("key"), b.get("lot_id"), b.get("from"), b.get("to"), b.get("qty")))
        r("GET", r"/api/promotions", lambda a, q, b: self.shop.promotions(a))
        r("POST", r"/api/promotions", lambda a, q, b: self.shop.add_promotion(a, b.get("key"), b.get("product_id"), b.get("buy_qty"), b.get("free_qty"), b.get("starts"), b.get("ends")))
        r("POST", r"/api/promotions/end", lambda a, q, b: self.shop.end_promotion(a, b.get("key"), b.get("promotion_id")))
        r("POST", r"/api/days/close", lambda a, q, b: self.shop.close_day(a, b.get("key"), b.get("day")))
        r("GET", r"/api/days/(\d{4}-\d{2}-\d{2})", lambda a, q, b, day: self.shop.day_status(a, day))
        r("GET", r"/api/reports/period", lambda a, q, b: self.shop.period_report(a, q.get("start"), q.get("end")))
        r("GET", r"/api/account-reviews", lambda a, q, b: self.shop.review_items(a))
        r("POST", r"/api/account-reviews/allocate", lambda a, q, b: self.shop.allocate_collection(a, b.get("key"), b.get("entry_id"), b.get("sale_id"), b.get("amount"), b.get("note")))
        r("POST", r"/api/account-reviews/resolve", lambda a, q, b: self.shop.resolve_review(a, b.get("key"), b.get("review_id"), b.get("resolution")))
        r("POST", r"/api/safe", lambda a, q, b: self.shop.safe_move(a, b.get("key"), b.get("kind"), b.get("amount"), b.get("note")))
        r("GET", r"/api/packs/(\d{13})/label", lambda a, q, b, code: self.shop.pack_label(a, code))
        r("GET", r"/api/packs", lambda a, q, b: self.shop.list_packs(a, _int(q.get("lot"), 0) or 0, _int(q.get("location"), None)))
        r("GET", r"/api/training/progress", self.training_progress)
        r("POST", r"/api/packs/waste", lambda a, q, b: self.shop.waste_pack(a, b.get("key"), b.get("code"), b.get("reason"), b.get("note")))
        r("GET", r"/api/recalls", lambda a, q, b: self.shop.recalls(a))
        r("POST", r"/api/recalls/lift", lambda a, q, b: self.shop.lift_recall(a, b.get("key"), b.get("recall_id"), b.get("reason")))
        r("POST", r"/api/transfer", lambda a, q, b: self.shop.transfer(a, b.get("key"), b.get("lot_id"), b.get("from"), b.get("to"), b.get("qty")))
        r("POST", r"/api/waste", lambda a, q, b: self.shop.waste(a, b.get("key"), b.get("lot_id"), b.get("location_id"), b.get("qty"), b.get("reason"), b.get("note")))
        r("POST", r"/api/lots/status", lambda a, q, b: self.shop.set_lot_status(a, b.get("key"), b.get("lot_id"), b.get("status"), b.get("reason")))
        r("POST", r"/api/recall", lambda a, q, b: self.shop.recall(a, b.get("key"), b.get("lot_id"), b.get("reason")))
        r("GET", r"/api/trace/([\w-]+)", lambda a, q, b, lot: self.shop.trace(a, lot))
        r("GET", r"/api/recipes/(\d+)", lambda a, q, b, pid: self.shop.recipe(int(pid), a))
        r("POST", r"/api/recipes", lambda a, q, b: self.shop.save_recipe(a, b.get("product_id"), b.get("components"), b.get("shelf_life_days"), b.get("allergens"), b.get("loss_tolerance_permille"), b.get("target_pack_g"), b.get("note")))
        r("POST", r"/api/production", lambda a, q, b: self.shop.produce(a, b.get("key"), b.get("payload"), self.shop.verify_approval(b.get("approval"), a)))
        r("GET", r"/api/counts", self.counts)
        r("POST", r"/api/counts", lambda a, q, b: self.shop.open_count(a, b.get("location_id")))
        r("GET", r"/api/counts/(\d+)", lambda a, q, b, cid: self.shop.count_sheet(a, int(cid)))
        r("POST", r"/api/counts/(\d+)/line", lambda a, q, b, cid: self.shop.record_count(a, int(cid), b.get("lot_id"), b.get("counted")))
        r("POST", r"/api/counts/(\d+)/cancel", lambda a, q, b, cid: self.shop.cancel_count(a, int(cid), b.get("reason")))
        r("POST", r"/api/counts/(\d+)/approve", lambda a, q, b, cid: self.shop.approve_count(a, b.get("key"), int(cid), b.get("owner_reason")))
        r("GET", r"/api/stock", lambda a, q, b: self.shop.stock_report(a, _int(q.get("product"), None)))
        r("GET", r"/api/reports/day", lambda a, q, b: self.shop.day_report(a, q.get("day")))
        r("GET", r"/api/export/stock\.csv", self.export_stock)
        r("GET", r"/api/settings", lambda a, q, b: self.public_settings(a))
        r("POST", r"/api/settings", lambda a, q, b: self.shop.update_settings(a, b))
        r("GET", r"/api/label-templates", lambda a, q, b: self.shop.label_templates())
        r("POST", r"/api/label-templates", lambda a, q, b: {"id": self.shop.save_label_template(a, b)})
        r("GET", r"/api/scanner/profile", lambda a, q, b: self.profile_for(a))
        r("POST", r"/api/scanner/profile", self.save_profile)
        r("GET", r"/api/scanner/ports", self.ports)
        r("POST", r"/api/scanner/devices", self.save_devices)
        r("GET", r"/api/scanner/events", self.scanner_events, lock=False)
        r("POST", r"/api/scanner/claim", self.scanner_claim)
        r("POST", r"/api/scanner/inspect", self.inspect_scan)
        r("GET", r"/api/users", self.users)
        r("POST", r"/api/users", self.add_user)
        r("POST", r"/api/backup", self.make_backup)
        r("GET", r"/api/backups", lambda a, q, b: require(a, "backup") and backup.list_backups(self.backup_folder))
        r("GET", r"/api/reconcile", lambda a, q, b: require(a, "reports") and self.shop.reconcile())

    def route(self, method, pattern, fn, auth=True, lock=True):
        self.routes.append((method, re.compile("^" + pattern + "$"), fn, auth, lock))

    # ---------- handlers ----------
    def state(self, actor, q, body):
        s = self.shop.settings()
        return {"version": VERSION, "mode": self.store.mode, "setup_needed": not self.auth.has_users(),
                "user": actor, "shop_name": s["shop_name"], "instance": s.get("instance_id"), "lan": self.lan,
                "training_login": {"users": [u for u, _, _ in training.TRAINING_USERS], "password": training.TRAINING_PASSWORD}
                if self.store.mode == "training" else None}

    def setup(self, actor, q, body, client=None):
        if self.auth.has_users():
            raise ShopError("already_set_up", status=409)
        if not client or not ipaddress.ip_address(client).is_loopback:
            raise ShopError("setup_only_on_shop_pc", status=403)

        def work():
            uid = self.auth.create_user(body.get("username"), body.get("name"), "owner", body.get("password"))
            if body.get("shop_name"):
                self.store.db.execute("INSERT INTO settings VALUES('shop_name',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                      (json.dumps(str(body["shop_name"])[:80]),))
            for code, ar, en, kind, sellable in (("SH", "رفوف البقالة", "Grocery shelves", "shelf", 1),
                                                 ("FR", "ثلاجة العرض", "Display fridge", "display_fridge", 1),
                                                 ("PR", "ثلاجة التجهيز", "Prep fridge", "prep_fridge", 0),
                                                 ("QA", "حجر الفحص", "Quarantine", "quarantine", 0),
                                                 ("WS", "الهالك", "Waste", "waste", 0)):
                self.store.db.execute("INSERT INTO locations(code,name_ar,name_en,kind,sellable) VALUES(?,?,?,?,?)",
                                      (code, ar, en, kind, sellable))
            self.store.audit({"id": uid}, "setup")
            return {"ok": True}
        return self.store.tx(work)

    def login(self, actor, q, body):
        token, user = self.auth.login(body.get("username"), body.get("password"), body.get("workstation") or "unknown")
        return {"user": user, "_cookie": token}

    def logout(self, actor, q, body, token=None):
        if token:
            self.auth.logout(token)
        return {"ok": True, "_cookie": ""}

    def parties(self, actor, q, body):
        kind = q.get("kind")
        if kind == "supplier":
            require(actor, "receive") if not can(actor, "supplier_pay") else None
            rows = self.store.all("SELECT * FROM suppliers ORDER BY name")
            for r in rows:
                r["balance"] = self.shop.supplier_balance(r["id"])
            return rows
        require(actor, "sell")
        rows = self.store.all("SELECT * FROM customers ORDER BY name")
        for r in rows:
            r["balance"] = self.shop.customer_balance(r["id"])
        return rows

    def counts(self, actor, q, body):
        require(actor, "count")
        return self.store.all("""SELECT c.*, l.name_ar, l.name_en FROM counts c JOIN locations l ON l.id=c.location_id
            ORDER BY c.id DESC LIMIT 50""")

    def export_stock(self, actor, q, body):
        require(actor, "export")
        rows = self.shop.stock_report(actor)
        cols = ["code", "name_en", "name_ar", "lot_no", "expires", "state", "loc_en", "qty"] + (["cost"] if can(actor, "see_cost") else [])
        lines = [",".join(cols)]
        for r in rows:
            lines.append(",".join(_csv(r.get(c)) for c in cols))
        self.store.tx(lambda: self.store.audit(actor, "export.stock", None, {"rows": len(rows)}))
        return {"_raw": ("﻿" + "\r\n".join(lines)).encode("utf-8"), "_type": "text/csv; charset=utf-8",
                "_name": "stock.csv"}

    def public_settings(self, actor):
        s = self.shop.settings()
        if not can(actor, "settings"):
            s.pop("scanner_devices", None)
        return s

    def profile_for(self, actor):
        row = self.store.one("SELECT config FROM scanner_profiles WHERE workstation=? AND user_id=?",
                             (actor["workstation"], actor["id"])) or \
            self.store.one("SELECT config FROM scanner_profiles WHERE workstation=? AND user_id IS NULL", (actor["workstation"],))
        return json.loads(row["config"]) if row else {}

    PROFILE_KEYS = {"name": str, "prefix": str, "suffix": str, "layout_fix": str, "aim_ids": bool,
                    "gs_substitutes": list, "beep": bool, "min_length": int, "connection": str, "device_id": str,
                    "max_gap_ms": int}

    def save_profile(self, actor, q, body):
        require(actor, "scanner_setup")
        clean = {}
        for k, v in (body.get("config") or {}).items():
            kind = self.PROFILE_KEYS.get(k)
            if kind is None or not isinstance(v, kind):
                raise ShopError("bad_profile", {"key": k})
            if isinstance(v, str) and len(v) > 40:
                raise ShopError("bad_profile", {"key": k})
            clean[k] = v
        if clean.get("layout_fix") not in (None, "", "arabic101"):
            raise ShopError("bad_profile", {"key": "layout_fix"})
        shared = bool(body.get("whole_workstation")) and can(actor, "settings")

        def work():
            uid = None if shared else actor["id"]
            self.store.db.execute("""DELETE FROM scanner_profiles WHERE workstation=? AND user_id IS ?""", (actor["workstation"], uid))
            self.store.db.execute("INSERT INTO scanner_profiles(workstation,user_id,name,config,updated_at) VALUES(?,?,?,?,datetime('now'))",
                                  (actor["workstation"], uid, clean.get("name", "scanner"), json.dumps(clean)))
            self.store.audit(actor, "scanner.profile", actor["workstation"], clean)
            return clean
        return self.store.tx(work)

    def ports(self, actor, q, body):
        require(actor, "scanner_setup")
        return {"serial": list_serial_ports(), "devices": self.hub.status(), "platform": sys.platform}

    def save_devices(self, actor, q, body):
        require(actor, "settings")
        devices = body.get("devices")
        if not isinstance(devices, list) or len(devices) > 16:
            raise ShopError("bad_devices")
        clean = []
        for d in devices:
            if not isinstance(d, dict) or d.get("type") not in ("serial", "tcp") or not re.match(r"^[\w-]{1,20}$", str(d.get("id", ""))):
                raise ShopError("bad_devices")
            ws = d.get("workstation")
            if ws is not None and not re.match(r"^[\w-]{1,64}$", str(ws)):
                raise ShopError("bad_devices")
            item = {"id": d["id"], "type": d["type"], "enabled": bool(d.get("enabled", True)), "workstation": ws,
                    "suffix": d.get("suffix", "CR") if d.get("suffix") in (None, "CR", "LF", "CRLF", "TAB", "ETX", "EOT") else "CR",
                    "prefix": str(d.get("prefix", ""))[:4], "gap_ms": integer(d.get("gap_ms", 80), 10, maximum=2000),
                    "partial_ms": integer(d.get("partial_ms", 1500), 100, maximum=10000)}
            if d["type"] == "serial":
                item.update(port=str(d.get("port", ""))[:40], baud=integer(d.get("baud", 9600), 1200, maximum=115200),
                            bits=integer(d.get("bits", 8), 7, maximum=8), stop=integer(d.get("stop", 1), 1, maximum=2),
                            parity=d.get("parity", "none") if d.get("parity") in ("none", "even", "odd") else "none")
            else:
                host = str(d.get("host", ""))
                try:
                    private = ipaddress.ip_address(host).is_private
                except ValueError:
                    private = False
                if not private:
                    raise ShopError("device_must_be_local")
                item.update(host=host, port=integer(d.get("port"), 1, maximum=65535))
            clean.append(item)
        self.store.tx(lambda: (self.store.db.execute("INSERT INTO settings VALUES('scanner_devices',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                                     (json.dumps(clean),)), self.store.audit(actor, "scanner.devices", None, {"n": len(clean)})))
        self.hub.configure(clean)
        return {"devices": self.hub.status()}

    def training_progress(self, actor, q, body):
        """Exercises of the training copy, proven from the ledger (never self-declared)."""
        if self.store.mode != "training":
            raise ShopError("training_only", status=404)
        return {"exercises": training.progress(self.store, actor["id"])}

    def sell(self, actor, q, body):
        """The till must send back the server-quoted total it showed (R14):
        a price change after scanning is reported, never charged silently."""
        payload = body.get("payload")
        if isinstance(payload, dict) and payload.get("quote_hash") is None:
            raise ShopError("quote_required")
        return self.shop.sell(actor, body.get("key"), payload, self.shop.verify_approval(body.get("approval"), actor))

    def scanner_claim(self, actor, q, body):
        """The selling window of THIS till takes the till's devices (R09)."""
        require(actor, "sell") if not can(actor, "receive") and not can(actor, "count") else None
        window = text(body.get("window"), "bad_window", 64)
        self.hub.claim(actor["workstation"], window)
        return {"epoch": self.hub.epoch, "last": self.hub.next_id - 1,
                "devices": [d for d in self.hub.status() if d["workstation"] == actor["workstation"]]}

    def scanner_events(self, actor, q, body):
        wait = min(_int(q.get("wait"), 0), 20)
        if q.get("diag"):   # setup screen: read-only view of every device, for the shop's admin only
            require(actor, "settings")
            events, last = self.hub.since(_int(q.get("after"), 0), wait, q.get("device") or None)
            return {"events": events, "last": last, "epoch": self.hub.epoch, "devices": self.hub.status()}
        window = text(q.get("window"), "bad_window", 64)
        events, last, info = self.hub.take(actor["workstation"], window, _int(q.get("after"), 0), wait, q.get("epoch"))
        return {"events": events, "last": last, **info,
                "devices": [d for d in self.hub.status() if d["workstation"] == actor["workstation"]]}

    def inspect_scan(self, actor, q, body):
        """Scanner test wizard: show exactly what arrived, safely."""
        require(actor, "scanner_setup")
        from . import barcode as bc
        raw = body.get("raw")
        if not isinstance(raw, str):
            raise ShopError("scan_not_text")
        raw = raw[:bc.MAX_SCAN]
        codes = [{"char": bc.visible(c), "code": "U+%04X" % ord(c)} for c in raw[:80]]
        result = self.shop.resolve_scan(actor, raw, body.get("profile") or self.profile_for(actor)) if can(actor, "sell") or can(actor, "receive") or can(actor, "count") else None
        return {"seen": bc.visible(raw), "length": len(raw), "chars": codes, "result": result,
                "arabic_letters": bool(re.search("[؀-ۿ]", raw)), "has_gs": "\x1d" in raw}

    def users(self, actor, q, body):
        require(actor, "users")
        return self.store.all("SELECT id, username, display_name, role, active, created_at FROM users ORDER BY id")

    def add_user(self, actor, q, body):
        require(actor, "users")
        return {"id": self.store.tx(lambda: self.auth.create_user(body.get("username"), body.get("name"), body.get("role"), body.get("password")))}

    def make_backup(self, actor, q, body):
        require(actor, "backup")
        result = backup.create_backup(self.store, self.backup_folder, "manual")
        self.store.tx(lambda: self.store.audit(actor, "backup", result["folder"]))
        return result


def _int(value, default):
    if value in (None, ""):
        return default
    if not re.fullmatch(r"\d{1,9}", str(value)):
        raise ShopError("bad_number")
    return int(value)


COST_KEYS = {"cost", "cost_total", "abnormal_loss_cost", "cost_of_sales", "gross_margin", "losses",
             "margin_after_recorded_losses", "landed_cost", "line_cost", "input_cost"}


def redact_cost(value):
    """Defence in depth: whatever a service returns, a user without
    see_cost never receives cost fields (including idempotent replays)."""
    if isinstance(value, dict):
        return {k: redact_cost(v) for k, v in value.items() if k not in COST_KEYS}
    if isinstance(value, list):
        return [redact_cost(v) for v in value]
    return value


def _csv(value):
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@") and not re.match(r"^-?\d+$", text):
        text = "'" + text  # spreadsheet formula injection guard
    return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\r\n') else text


def make_handler(app, allowed_lan):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SuperMarket"
        sys_version = ""

        def log_message(self, fmt, *args):  # no tokens or bodies in logs
            log.info("%s %s", self.address_string(), fmt % args)

        def _client_ok(self):
            ip = ipaddress.ip_address(self.client_address[0])
            return ip.is_loopback or (allowed_lan and ip.is_private)

        def _send(self, status, body, ctype="application/json; charset=utf-8", headers=None):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            """DNS-rebinding guard: only literal local/private addresses or
            localhost on our own port may be used as Host."""
            host = self.headers.get("Host", "")
            m = re.fullmatch(r"(?:\[([0-9a-fA-F:]+)\]|([0-9.]+|localhost))(?::(\d{1,5}))?", host)
            if not m or (m.group(3) and int(m.group(3)) != self.server.server_address[1]):
                return False
            name = m.group(1) or m.group(2)
            if name == "localhost":
                return True
            try:
                ip = ipaddress.ip_address(name)
            except ValueError:
                return False
            return ip.is_loopback or (allowed_lan and ip.is_private)

        def _token(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie[COOKIE].value if COOKIE in cookie else None

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method):
            if not self._client_ok():
                return self._send(403, {"error": "client_not_allowed"})
            if not self._host_ok():
                return self._send(421, {"error": "bad_host"})
            parts = urlsplit(self.path)
            if not parts.path.startswith("/api/"):
                return self._static(parts.path) if method == "GET" else self._send(405, {"error": "method"})
            if method == "POST":
                origin = self.headers.get("Origin")
                host = self.headers.get("Host", "")
                if self.headers.get("X-SM") != "1" or (origin and urlsplit(origin).netloc != host):
                    return self._send(403, {"error": "bad_origin"})
            raw_length = self.headers.get("Content-Length") or "0"
            if not raw_length.isdigit():
                return self._send(400, {"error": "bad_length"})
            length = int(raw_length)
            if length > MAX_BODY:
                return self._send(413, {"error": "too_large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if method == "POST" else {}
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "bad_json"})
            query = {k: v[-1] for k, v in parse_qs(parts.query).items()}
            token = self._token()
            for m, pattern, fn, needs_auth, take_lock in app.routes:
                match = pattern.match(parts.path)
                if m != method or not match:
                    continue
                try:
                    with app.store.lock:
                        actor = app.auth.session(token)
                    if needs_auth and not actor and parts.path != "/api/state":
                        raise ShopError("login_required", status=401)
                    # one shared connection: reads take the lock too, so nobody
                    # sees rows of a transaction that may still roll back
                    lock = app.store.lock if take_lock else contextlib.nullcontext()
                    with lock:
                        if fn == app.setup:
                            result = fn(actor, query, body, client=self.client_address[0])
                        elif fn == app.logout:
                            result = fn(actor, query, body, token=token)
                        else:
                            result = fn(actor, query, body, *match.groups())
                    if actor and not can(actor, "see_cost") and not (isinstance(result, dict) and "_raw" in result):
                        result = redact_cost(result)
                except ShopError as exc:
                    return self._send(exc.status, {"error": exc.code, "detail": exc.detail})
                except (TypeError, ValueError, KeyError, AttributeError, sqlite3.InterfaceError):
                    log.warning("bad request on %s\n%s", parts.path, traceback.format_exc())
                    return self._send(400, {"error": "bad_request"})
                except Exception:  # never leak internals to the browser
                    log.error("internal error on %s\n%s", parts.path, traceback.format_exc())
                    return self._send(500, {"error": "internal_error"})
                headers = {}
                if isinstance(result, dict) and "_raw" in result:
                    return self._send(200, result["_raw"], result["_type"],
                                      {"Content-Disposition": 'attachment; filename="%s"' % result["_name"]})
                if isinstance(result, dict) and "_cookie" in result:
                    value = result.pop("_cookie")
                    headers["Set-Cookie"] = "%s=%s; HttpOnly; SameSite=Strict; Path=/%s" % (
                        COOKIE, value, "" if value else "; Max-Age=0")
                return self._send(200, result, headers=headers)  # None -> JSON null
            return self._send(404, {"error": "not_found"})

        def _static(self, path):
            if path == "/" or not path:
                path = "/index.html"
            target = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._send(404, {"error": "not_found"})
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            if target.suffix == ".woff2":
                ctype = "font/woff2"
            return self._send(200, target.read_bytes(), ctype)

    return Handler


def serve(data_folder, mode="shop", host="127.0.0.1", port=8765, lan=False):
    app = App(data_folder, mode, lan=lan)
    bind = "0.0.0.0" if lan else host
    httpd = ThreadingHTTPServer((bind, port), make_handler(app, lan))
    httpd.daemon_threads = True
    httpd.app = app
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(description="Super market local server")
    parser.add_argument("--data", default=None, help="data folder (default: shop-data or training-data next to the app)")
    parser.add_argument("--training", action="store_true", help="open the separate training shop")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--lan", action="store_true", help="allow other tills on the private LAN")
    args = parser.parse_args(argv)
    mode = "training" if args.training else "shop"
    data = args.data or str(ROOT / ("training-data" if args.training else "shop-data"))
    port = args.port or (8766 if args.training else 8765)
    pathlib.Path(data).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(pathlib.Path(data) / "server.log", encoding="utf-8")])
    httpd = serve(data, mode, port=port, lan=args.lan)
    print("Super market %s (%s) on http://127.0.0.1:%d  -  Ctrl+C to stop" % (VERSION, mode, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.app.hub.stop()
        httpd.server_close()
        httpd.app.store.close()


if __name__ == "__main__":
    main()
