"""Users, roles, permissions and sessions. PBKDF2-SHA256 from the standard
library (hashlib); tokens are random and only their SHA-256 is stored.
No default password exists in shop mode: the first owner is created by the
setup wizard from the shop PC itself.
"""

import datetime as dt
import hashlib
import hmac
import secrets
import threading
import time

from .core import Forbidden, ShopError, now, now_iso, text

PBKDF2_ITER = 600_000
SESSION_HOURS = 14
MAX_FAILED = 5          # wrong passwords from ONE address for ONE name before that pair waits
LOCK_STEPS = (60, 120, 240, 480, 900)   # seconds: the wait doubles with every repeat, never more than 15 minutes
WINDOW_SECONDS = 300
IP_MAX_FAILED = 20      # wrong passwords from one address for any names before it is refused (HTTP 429)
LOOPBACK_LOCK = 30      # the shop computer itself: a short flat wait, never escalating (the owner works there)
_mono = time.monotonic  # tests replace this clock

PERMISSIONS = {
    "sell", "discount", "discount_large", "price_override", "refund", "void_line",
    "shift", "cash_moves", "close_others_shift", "receive", "supplier_pay",
    "produce", "cutting", "recipes", "count", "count_approve", "waste", "transfer",
    "release_hold", "recall", "catalog", "prices", "users", "settings",
    "see_cost", "reports", "export", "backup", "confirm_payments", "credit_sale",
    "scanner_setup", "approve_variance", "safe", "close_day", "approve_refund",
}

ROLES = {
    "owner": set(PERMISSIONS),
    "manager": PERMISSIONS - {"users", "backup"},
    "cashier": {"sell", "shift", "discount", "refund", "scanner_setup"},
    "receiver": {"receive", "transfer", "count", "scanner_setup"},
    "butcher": {"produce", "cutting", "transfer", "waste", "count", "scanner_setup"},
    "counter": {"count", "scanner_setup"},
    "accountant": {"reports", "export", "see_cost", "confirm_payments", "supplier_pay", "safe"},
    "viewer": {"reports"},
}


def hash_password(password, salt=None, iterations=None):
    iterations = iterations or PBKDF2_ITER
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return digest.hex(), salt, iterations


COMMON_PASSWORDS = {"password", "password1", "passw0rd", "qwertyui", "qwerty123", "asdfghjk", "iloveyou", "abcdefgh",
                    "abcd1234", "1234abcd", "admin123", "administrator", "welcome1", "letmein1", "11223344", "12341234",
                    "12121212", "00001111", "1q2w3e4r", "q1w2e3r4", "zxcvbnm1", "aa123456", "123123123", "supermarket"}


def _easy(password):
    """Guessed in seconds: one or two repeated characters, a run like 12345678 / 87654321 / abcdefgh,
    a short repeated block (12121212, abcabcab) or one of the most used passwords (review report M3)."""
    p = password.lower()
    if len(set(p)) <= 2 or p in COMMON_PASSWORDS:
        return True
    steps = {ord(b) - ord(a) for a, b in zip(p, p[1:])}
    if steps <= {1} or steps <= {-1}:
        return True
    return any(p == (p[:n] * len(p))[:len(p)] for n in (2, 3, 4))


def check_password_rules(password):
    if not isinstance(password, str) or len(password) < 8 or len(password) > 128 or _easy(password):
        raise ShopError("weak_password")
    return password


def _expired(expires_at):
    try:
        return dt.datetime.fromisoformat(expires_at) <= now()
    except (TypeError, ValueError):
        return True             # an unreadable expiry never keeps a session alive


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class LoginGuard:
    """Throttles wrong passwords by (name + client address) and by address (review H2).
    A stranger's wrong guesses can never lock the owner out for the owner's own
    address; the client address is the TCP peer only (X-Forwarded-For is never read)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pairs = {}     # (name, ip) -> {"fails": [t...], "until": t, "level": n}
        self.ips = {}       # ip -> {"fails": [t...], "until": t, "level": n}

    @staticmethod
    def _loopback(ip):
        return ip in ("127.0.0.1", "::1", "localhost", None, "local")

    def _entry(self, table, key):
        return table.setdefault(key, {"fails": [], "until": 0.0, "level": 0})

    def check(self, name, ip):
        now_t = _mono()
        with self.lock:
            e = self.ips.get(ip)
            if e and e["until"] > now_t and not self._loopback(ip):
                raise ShopError("too_many_attempts", {"retry_after": int(e["until"] - now_t) + 1}, status=429)
            e = self.pairs.get((name, ip))
            if e and e["until"] > now_t:
                raise ShopError("account_locked", {"retry_after": int(e["until"] - now_t) + 1}, status=429)

    def _fail(self, entry, limit, now_t, loopback):
        entry["fails"] = [t for t in entry["fails"] if now_t - t < WINDOW_SECONDS] + [now_t]
        if len(entry["fails"]) >= limit:
            wait = LOOPBACK_LOCK if loopback else LOCK_STEPS[min(entry["level"], len(LOCK_STEPS) - 1)]
            entry["until"] = now_t + wait
            entry["level"] += 1
            entry["fails"] = []

    def fail(self, name, ip):
        now_t = _mono()
        with self.lock:
            loop = self._loopback(ip)
            self._fail(self._entry(self.pairs, (name, ip)), MAX_FAILED, now_t, loop)
            if not loop:
                self._fail(self._entry(self.ips, ip), IP_MAX_FAILED, now_t, False)
            if len(self.pairs) > 5000:       # a flood of invented names must not grow memory forever
                self.pairs = {k: v for k, v in self.pairs.items() if v["until"] > now_t}
            if len(self.ips) > 5000:
                self.ips = {k: v for k, v in self.ips.items() if v["until"] > now_t}

    def succeeded(self, name, ip):
        with self.lock:
            self.pairs.pop((name, ip), None)
            if self._loopback(ip):
                # the owner/manager typing the right password ON the shop computer clears every wait for that name
                for k in [k for k in self.pairs if k[0] == name]:
                    self.pairs.pop(k, None)


class Auth:
    def __init__(self, store):
        self.store = store
        self.guard = LoginGuard()
        self._seen = {}     # token hash -> monotonic time of the last real request (idle lock, LAN only)

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

    def verify(self, username, password, count_failures=True, client=None):
        """Return the user row or raise. Wrong passwords are throttled per (name, client
        address) and per address, never by locking the account for everybody (H2)."""
        if not isinstance(username, str) or not isinstance(password, str):
            raise ShopError("bad_login", status=401)
        name = username.strip()
        if count_failures:
            self.guard.check(name, client)
        user = self.store.one("SELECT * FROM users WHERE username=?", (name,))
        if not user or not user["active"]:
            hash_password(password or "x")  # similar timing for unknown names
            if count_failures:
                self.guard.fail(name, client)
            raise ShopError("bad_login", status=401)
        digest, _, _ = hash_password(password, user["pw_salt"], user["pw_iter"])
        if not hmac.compare_digest(digest, user["pw_hash"]):
            if count_failures:
                self.guard.fail(name, client)
            raise ShopError("bad_login", status=401)
        if count_failures:
            self.guard.succeeded(name, client)
        updates = []
        if user["failed"] or user["locked_until"]:     # leftovers of the old per-account lock
            updates.append(("failed=0, locked_until=NULL", ()))
        if user["pw_iter"] < PBKDF2_ITER:              # L6: a hash made with fewer rounds is upgraded at the next good login
            new_hash, new_salt, new_iter = hash_password(password)
            updates.append(("pw_hash=?, pw_salt=?, pw_iter=?", (new_hash, new_salt, new_iter)))
        for sql, args in updates:
            self.store.db.execute("UPDATE users SET " + sql + " WHERE id=?", args + (user["id"],))
        return user

    def login(self, username, password, workstation, client=None):
        workstation = text(workstation, "bad_workstation", 64)
        with self.store.lock:  # autocommit: a failed attempt must stay counted
            user = self.verify(username, password, client=client)
        token = secrets.token_urlsafe(32)
        expires = (now() + dt.timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")

        def work():
            self.store.db.execute("INSERT INTO sessions(token_hash,user_id,workstation,created_at,expires_at) VALUES(?,?,?,?,?)",
                                  (token_hash(token), user["id"], workstation, now_iso(), expires))
            self.store.audit({"id": user["id"], "workstation": workstation}, "login")
        self.store.tx(work)
        return token, self.public(user)

    def logout(self, token):
        self.store.tx(lambda: self.store.db.execute("DELETE FROM sessions WHERE token_hash=?",
                                                    (token_hash(token),)))

    idle_lock_s = None       # set by the server in LAN mode: a till left alone this long locks (review M1)
    idle_clock = staticmethod(__import__("time").monotonic)

    def session(self, token, touch=True):
        """touch=False for background polling (scanner long-poll), which must not keep a till 'in use'."""
        if not token:
            return None
        th = token_hash(token)
        row = self.store.one("""SELECT u.*, s.workstation, s.expires_at, s.locked FROM sessions s
            JOIN users u ON u.id=s.user_id WHERE s.token_hash=?""", (th,))
        # review report L13: compare moments, not text: "+03:00" and "+02:00" strings sort wrongly around the DST change
        if not row or not row["active"] or _expired(row["expires_at"]):
            return None
        locked = bool(row["locked"])
        if self.idle_lock_s:
            moment = self.idle_clock()
            seen = self._seen.get(th)
            if not locked and seen is not None and moment - seen > self.idle_lock_s:
                self.store.tx(lambda: self.store.db.execute("UPDATE sessions SET locked=1 WHERE token_hash=?", (th,)))
                locked = True
            if touch or seen is None:
                self._seen[th] = moment
        actor = self.public(row)
        actor["workstation"] = row["workstation"]
        actor["must_change"] = bool(row["must_change"])
        actor["locked"] = locked
        return actor

    # ---- accounts (gap row 5). A changed or reset password ends the other sessions;
    # a stopped employee loses every session at once; nothing is ever deleted.
    def _set_password(self, user_id, password, must_change):
        check_password_rules(password)
        digest, salt, iterations = hash_password(password)
        self.store.db.execute("UPDATE users SET pw_hash=?, pw_salt=?, pw_iter=?, must_change=?, failed=0, locked_until=NULL WHERE id=?",
                              (digest, salt, iterations, int(must_change), user_id))

    def change_password(self, actor, token, old, new, client=None):
        """The user's own password: the old one is checked (and counted on failure);
        every other session of this user ends, this one stays."""
        if not actor:
            raise ShopError("login_required", status=401)
        with self.store.lock:
            self.verify(actor["username"], old, client=client)
        if old == new:
            raise ShopError("same_password")

        def work():
            self._set_password(actor["id"], new, False)
            self.store.db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?", (actor["id"], token_hash(token)))
            self.store.audit(actor, "user.password_changed", str(actor["id"]))
            return {"ok": True}
        return self.store.tx(work)

    def _target(self, actor, user_id):
        require(actor, "users")
        user = self.store.one("SELECT * FROM users WHERE id=?", (user_id,))
        if not user:
            raise ShopError("unknown_user")
        return user

    def reset_password(self, actor, user_id, new):
        """Owner sets a temporary password for an employee who forgot it: all of
        that employee's sessions end and the new password must be changed at login."""
        user = self._target(actor, user_id)

        def work():
            self._set_password(user["id"], new, user["id"] != actor["id"])
            self.store.db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            self.store.audit(actor, "user.password_reset", str(user["id"]))
            return {"ok": True, "must_change": user["id"] != actor["id"]}
        return self.store.tx(work)

    def set_active(self, actor, user_id, active, reason):
        """Stop or bring back an employee. Stopping ends every session at once.
        The owner cannot stop himself, and the last active owner is never stopped."""
        user = self._target(actor, user_id)
        reason = text(reason, "reason_required", 200)
        if not active and user["id"] == actor["id"]:
            raise ShopError("cannot_stop_self")

        def work():
            if not active and user["role"] == "owner" and self.store.scalar(
                    "SELECT COUNT(*) FROM users WHERE role='owner' AND active=1 AND id<>?", (user["id"],)) == 0:
                raise ShopError("last_owner")
            self.store.db.execute("UPDATE users SET active=? WHERE id=?", (int(bool(active)), user["id"]))
            if not active:
                self.store.db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            self.store.audit(actor, "user.activated" if active else "user.stopped", str(user["id"]), {"reason": reason})
            return {"ok": True, "active": bool(active)}
        return self.store.tx(work)

    def lock_screen(self, actor, token):
        """The till is locked on the server: until the same user types the password,
        every request of this session is refused, even after a page reload."""
        self.store.tx(lambda: self.store.db.execute("UPDATE sessions SET locked=1 WHERE token_hash=?", (token_hash(token),)))
        return {"locked": True}

    def unlock_screen(self, actor, token, password, client=None):
        with self.store.lock:
            self.verify(actor["username"], password, client=client)       # failures count and lock the account like a login
        self.store.tx(lambda: self.store.db.execute("UPDATE sessions SET locked=0 WHERE token_hash=?", (token_hash(token),)))
        return {"locked": False}

    def recover_owner(self, username, new_password):
        """Run ON the shop computer only (tools/shopctl.py owner-recovery): whoever can
        open the data folder already holds the data. Sets a new owner password,
        clears the lock and ends the owner's sessions. Nothing else changes."""
        def work():
            user = self.store.one("SELECT * FROM users WHERE username=? AND role='owner'", (username,))
            if not user:
                raise ShopError("unknown_owner")
            self._set_password(user["id"], new_password, False)
            self.store.db.execute("UPDATE users SET active=1 WHERE id=?", (user["id"],))
            self.store.db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
            self.store.audit({"id": user["id"], "workstation": "shop-pc"}, "owner.recovery", str(user["id"]))
            return {"ok": True, "username": user["username"]}
        return self.store.tx(work)

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
