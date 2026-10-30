"""Users, roles, permissions and sessions. PBKDF2-SHA256 from the standard
library (hashlib); tokens are random and only their SHA-256 is stored.
No default password exists in shop mode: the first owner is created by the
setup wizard from the shop PC itself.
"""

import datetime as dt
import hashlib
import hmac
import secrets

from .core import Forbidden, ShopError, now, now_iso, text

PBKDF2_ITER = 240_000
SESSION_HOURS = 14
MAX_FAILED = 5
LOCK_MINUTES = 5

PERMISSIONS = {
    "sell", "discount", "discount_large", "price_override", "refund", "void_line",
    "shift", "cash_moves", "close_others_shift", "receive", "supplier_pay",
    "produce", "cutting", "recipes", "count", "count_approve", "waste", "transfer",
    "release_hold", "recall", "catalog", "prices", "users", "settings",
    "see_cost", "reports", "export", "backup", "confirm_payments", "credit_sale",
    "scanner_setup", "approve_variance",
}

ROLES = {
    "owner": set(PERMISSIONS),
    "manager": PERMISSIONS - {"users", "backup"},
    "cashier": {"sell", "shift", "discount", "refund", "scanner_setup"},
    "receiver": {"receive", "transfer", "count", "scanner_setup"},
    "butcher": {"produce", "cutting", "transfer", "waste", "count", "scanner_setup"},
    "counter": {"count", "scanner_setup"},
    "accountant": {"reports", "export", "see_cost", "confirm_payments", "supplier_pay"},
    "viewer": {"reports"},
}


def hash_password(password, salt=None, iterations=None):
    iterations = iterations or PBKDF2_ITER
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return digest.hex(), salt, iterations


def check_password_rules(password):
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128:
        raise ShopError("weak_password")
    return password


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    def __init__(self, store):
        self.store = store

    def has_users(self):
        return bool(self.store.scalar("SELECT COUNT(*) FROM users"))

    def create_user(self, username, display_name, role, password):
        username = text(username, "bad_username", 40)
        display_name = text(display_name, "bad_name", 80)
        if role not in ROLES:
            raise ShopError("bad_role")
        check_password_rules(password)
        if self.store.scalar("SELECT 1 FROM users WHERE username=?", (username,)):
            raise ShopError("username_taken")
        digest, salt, iterations = hash_password(password)
        cur = self.store.db.execute(
            "INSERT INTO users(username,display_name,role,pw_hash,pw_salt,pw_iter,created_at) VALUES(?,?,?,?,?,?,?)",
            (username, display_name, role, digest, salt, iterations, now_iso()))
        return cur.lastrowid

    def verify(self, username, password, count_failures=True):
        """Return user row or raise; counts failures and locks briefly."""
        if not isinstance(username, str) or not isinstance(password, str):
            raise ShopError("bad_login", status=401)
        user = self.store.one("SELECT * FROM users WHERE username=?", (username.strip(),))
        if not user or not user["active"]:
            hash_password(password or "x")  # similar timing for unknown names
            raise ShopError("bad_login", status=401)
        if user["locked_until"] and user["locked_until"] > now_iso():
            raise ShopError("account_locked", {"until": user["locked_until"]}, status=401)
        digest, _, _ = hash_password(password, user["pw_salt"], user["pw_iter"])
        if not hmac.compare_digest(digest, user["pw_hash"]):
            if not count_failures:
                raise ShopError("bad_login", status=401)
            failed = user["failed"] + 1
            locked = None
            if failed >= MAX_FAILED:
                locked = (now() + dt.timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds")
                failed = 0
            self.store.db.execute("UPDATE users SET failed=?, locked_until=? WHERE id=?",
                                  (failed, locked, user["id"]))
            raise ShopError("bad_login", status=401)
        if user["failed"] or user["locked_until"]:
            self.store.db.execute("UPDATE users SET failed=0, locked_until=NULL WHERE id=?", (user["id"],))
        return user

    def login(self, username, password, workstation):
        workstation = text(workstation, "bad_workstation", 64)
        with self.store.lock:  # autocommit: a failed attempt must stay counted
            user = self.verify(username, password)
        token = secrets.token_urlsafe(32)
        expires = (now() + dt.timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")

        def work():
            self.store.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                                  (token_hash(token), user["id"], workstation, now_iso(), expires))
            self.store.audit({"id": user["id"], "workstation": workstation}, "login")
        self.store.tx(work)
        return token, self.public(user)

    def logout(self, token):
        self.store.tx(lambda: self.store.db.execute("DELETE FROM sessions WHERE token_hash=?",
                                                    (token_hash(token),)))

    def session(self, token):
        if not token:
            return None
        row = self.store.one("""SELECT u.*, s.workstation, s.expires_at FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""", (token_hash(token),))
        if not row or not row["active"] or row["expires_at"] < now_iso():
            return None
        actor = self.public(row)
        actor["workstation"] = row["workstation"]
        return actor

    @staticmethod
    def public(user):
        return {"id": user["id"], "username": user["username"], "name": user["display_name"],
                "role": user["role"], "permissions": sorted(ROLES[user["role"]])}

    def approve(self, approval, permission, actor):
        """Manager approval typed at the till: username + password of a user who
        holds `permission`. A scanned code can never satisfy this."""
        if not isinstance(approval, dict):
            raise Forbidden("approval_required", {"permission": permission})
        user = self.verify(approval.get("username"), approval.get("password"))
        if permission not in ROLES[user["role"]]:
            raise Forbidden("approver_lacks_permission", {"permission": permission})
        return user["id"]


def require(actor, permission):
    if not actor:
        raise ShopError("login_required", status=401)
    if permission not in ROLES.get(actor["role"], ()):
        raise Forbidden("forbidden", {"permission": permission})
    return actor


def can(actor, permission):
    return bool(actor) and permission in ROLES.get(actor["role"], ())
