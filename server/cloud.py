"""Cloud backup of this shop PC (owner order, 31 October 2026): a copy every hour while
something changed, and one nightly copy a day, to the owner's own Cloudflare account.

* One-time setup with no typing: on its first start the PC asks the cloud for its own
  space (`/pc/register`) and keeps the answer in `cloud.json` beside the data folder.
  Every later start reuses it: the same PC is always the same space; another PC gets
  another space, so two shops never mix.
* Local first: selling never waits for the internet. A copy is first made on this PC
  (SQLite online backup, integrity check, gzip) into `cloud-queue/`, then sent. With no
  internet it waits there and is sent when the internet is back.
* Every call is signed with HMAC-SHA256 (Python's `hmac`, no home-made crypto); the
  upload's SHA-256 is part of the signature and the cloud's storage checks it.
* The copy itself is not encrypted by this program (the standard library has no audited
  cipher); it is protected by HTTPS on the way and by the owner's Cloudflare account at
  rest. See docs/CLOUD_BACKUP.md.
* `cloud.json` holds this PC's secret. It is never logged, never sent to the browser and
  never put in a backup. `recovery_code()` shows it only on the shop PC itself (launcher).
"""

import base64
import gzip
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import secrets
import shutil
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import backup
from .core import ShopError, now
from .db import DB_NAME
from .version import PRODUCT_ID, VERSION

log = logging.getLogger("supermarket.cloud")

# The owner's cloud, built in: nobody types an address. Public, not a secret.
# SM_CLOUD_URL may point a test elsewhere; SM_CLOUD_URL=off (or empty) turns the cloud off.
BUILT_IN_URL = "https://super-market-backup.mflma2040.workers.dev"
PC_RE = re.compile(r"^[0-9a-f]{32}$")
HOUR = 3600
NIGHT_FROM_HOUR = 2          # the nightly copy is taken from 2 a.m. (or at the first start after it)
KEEP_QUEUED_NIGHTLY = 14     # with no internet for weeks, the queue keeps two weeks of nightly copies
RETRY = (60, 300, 900, 1800)
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
CODE_PREFIX = "SMC1-"


class CloudError(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sign(secret, pc, method, path, body_sha, t=None, nonce=None):
    t = str(int(t if t is not None else time.time()))      # real time: the shop clock override never signs
    nonce = nonce or secrets.token_hex(16)
    msg = "\n".join([method, path, t, nonce, body_sha, pc])
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {"X-SM-PC": pc, "X-SM-Time": t, "X-SM-Nonce": nonce, "X-SM-Sig": sig, "X-SM-Body": body_sha}


def _reason(exc):
    r = getattr(exc, "reason", exc)
    if isinstance(r, ssl.SSLCertVerificationError) or isinstance(exc, ssl.SSLCertVerificationError):
        return "certificate"
    if isinstance(r, (socket.gaierror, TimeoutError, socket.timeout, ConnectionError, OSError)):
        return "offline"
    return "offline"


class Client:
    """The signed calls. Knows nothing about schedules."""

    def __init__(self, url, pc=None, secret=None, timeout=60):
        self.url, self.pc, self.secret, self.timeout = url.rstrip("/"), pc, secret, timeout

    def _open(self, req, timeout=None):
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout, context=ssl.create_default_context())
        except urllib.error.HTTPError as e:
            try:
                info = json.loads(e.read(4096) or b"{}")
            except ValueError:
                info = {}
            code = info.get("error") or "http_%d" % e.code
            raise CloudError(code, "HTTP %d" % e.code) from None
        except (urllib.error.URLError, OSError) as e:
            raise CloudError(_reason(e), e.__class__.__name__) from None

    def register(self, install_id, label=""):
        body = json.dumps({"installId": install_id, "label": label[:60]}).encode()
        req = urllib.request.Request(self.url + "/pc/register", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "SuperMarket/" + VERSION})
        with self._open(req, 20) as r:
            data = json.loads(r.read(8192) or b"{}")
        pc, secret = str(data.get("pcId") or ""), str(data.get("secret") or "")
        if not PC_RE.fullmatch(pc) or len(secret) < 32:
            raise CloudError("rejected")
        return pc, secret

    def _signed(self, method, path, body_sha=EMPTY_SHA, data=None, headers=None):
        h = {"User-Agent": "SuperMarket/" + VERSION, **sign(self.secret, self.pc, method, path, body_sha), **(headers or {})}
        return urllib.request.Request(self.url + path, data=data, method=method, headers=h)

    def copies(self):
        with self._open(self._signed("GET", "/pc/copies")) as r:
            return json.loads(r.read(1 << 20))["copies"]

    def upload(self, name, gz_path, meta):
        size = os.path.getsize(gz_path)
        with open(gz_path, "rb") as fh:
            req = self._signed("PUT", "/pc/copies/" + name, meta["sha256"], fh, {
                "Content-Type": "application/gzip", "Content-Length": str(size),
                "X-SM-Db-Sha256": meta["db_sha256"], "X-SM-Schema": str(meta["schema"]), "X-SM-App": VERSION})
            with self._open(req, max(self.timeout, 60 + size // 50_000)) as r:   # ~50 KB/s is the slowest line we wait for
                return json.loads(r.read(4096))

    def download(self, name, target):
        with self._open(self._signed("GET", "/pc/copies/" + name), 600) as r, open(target, "wb") as out:
            expected = r.headers.get("X-SM-Sha256", "")
            shutil.copyfileobj(r, out, 1 << 20)
        if _sha256_file(target) != expected:
            raise CloudError("download_mismatch")
        return expected


class Cloud:
    """This PC's cloud backup: its identity, its queue and its schedule."""

    def __init__(self, home, store, url, label=""):
        self.home = pathlib.Path(home)
        self.store, self.url, self.label = store, (url or "").rstrip("/"), label
        self.file = self.home / "cloud.json"
        self.queue = self.home / "cloud-queue"
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.failures = 0
        self.next_try = 0.0
        self.status_ = {"last_ok_at": None, "last_copy": None, "error": None, "busy": False}
        self.data = self._load()
        self.status_.update(self.data.get("status") or {})

    @property
    def enabled(self):
        return bool(self.url) and self.store.mode == "shop"

    # ---------- identity ----------
    def _load(self):
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.file)

    def connected(self):
        return bool(PC_RE.fullmatch(str(self.data.get("pc") or "")) and len(str(self.data.get("secret") or "")) >= 32
                    and self.data.get("url") == self.url and self.data.get("host") == socket.gethostname())

    def connect(self):
        """One-time setup, automatic. The install id is saved BEFORE asking, so a lost answer is
        asked again as the same PC and never makes a second space."""
        with self.lock:
            if self.connected():
                return False
            install = str(self.data.get("install") or "")
            if self.data.get("host") not in (None, socket.gethostname()):
                # this folder was copied from another PC: this PC gets its OWN space, never the other's
                log.warning("cloud: the data folder came from another PC; this PC gets its own space")
                self.data = {}
                install = ""
            if not PC_RE.fullmatch(install):
                install = secrets.token_hex(16)
                self.data.update({"install": install, "host": socket.gethostname()})
                self._save()
            pc, secret = Client(self.url).register(install, self.label)
            self.data.update({"url": self.url, "pc": pc, "secret": secret, "connected_at": now().isoformat(timespec="seconds")})
            self._save()
            log.info("cloud: this PC has its own space now (%s…)", pc[:6])
            return True

    def client(self):
        return Client(self.url, self.data["pc"], self.data["secret"])

    # ---------- copies on this PC ----------
    def _fingerprint(self):
        return [self.store.scalar("SELECT COALESCE(MAX(id),0) FROM ops"), self.store.scalar("SELECT COALESCE(MAX(id),0) FROM audit")]

    def snapshot(self, kind):
        """A verified, compressed copy into the queue. Never blocks selling for longer than the
        SQLite online copy itself."""
        self.queue.mkdir(parents=True, exist_ok=True)
        name = "%s-%s" % (kind, now().strftime("%Y%m%d-%H%M%S"))
        raw = self.queue / (name + ".db")
        gz = self.queue / (name + ".db.gz")
        if gz.exists():
            return None
        target = sqlite3.connect(str(raw))
        try:
            with self.store.lock:
                fingerprint = self._fingerprint()
                self.store.db.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            schema = target.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        finally:
            target.close()
        if check != "ok":
            raw.unlink(missing_ok=True)
            raise ShopError("backup_integrity_failed")
        db_sha = _sha256_file(raw)
        with open(raw, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        raw.unlink()
        meta = {"name": name, "kind": kind, "db_sha256": db_sha, "sha256": _sha256_file(gz), "size": gz.stat().st_size,
                "schema": schema, "product": PRODUCT_ID, "app_version": VERSION, "created_at": now().isoformat(timespec="seconds")}
        (self.queue / (name + ".json")).write_text(json.dumps(meta), encoding="utf-8")
        self.data["fingerprint"] = fingerprint
        if kind == "nightly":
            self.data["nightly_day"] = now().date().isoformat()
        self.data["hourly_at"] = time.time()
        self._save()
        self._trim_queue()
        return meta

    def queued(self):
        out = []
        for f in sorted(self.queue.glob("*.json")) if self.queue.exists() else []:
            try:
                meta = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (self.queue / (meta["name"] + ".db.gz")).exists():
                out.append(meta)
        return out

    def _drop(self, name):
        for ext in (".db.gz", ".json", ".db"):
            (self.queue / (name + ext)).unlink(missing_ok=True)

    def _trim_queue(self):
        """No internet for long: keep only the newest hourly copy and two weeks of nightly ones."""
        items = self.queued()
        hourly = [m for m in items if m["kind"] == "hourly"]
        nightly = [m for m in items if m["kind"] == "nightly"]
        for m in hourly[:-1] + nightly[:-KEEP_QUEUED_NIGHTLY]:
            self._drop(m["name"])

    # ---------- sending ----------
    def send(self):
        """Send what is waiting, oldest first. Stops at the first failure and keeps the rest."""
        self.connect()
        c = self.client()
        sent = 0
        for meta in self.queued():
            gz = self.queue / (meta["name"] + ".db.gz")
            if _sha256_file(gz) != meta["sha256"]:          # damaged on this disk: never send it
                self._drop(meta["name"])
                continue
            try:
                c.upload(meta["name"], gz, meta)
            except CloudError as e:
                if e.reason == "unknown_pc":                 # the owner removed this PC's space: connect again
                    self.data.pop("pc", None)
                    self.data.pop("secret", None)
                    self._save()
                raise
            self._drop(meta["name"])
            self.status_["last_copy"] = meta["name"]
            sent += 1
        return sent

    def due(self, force=False):
        """Which copy is due now: 'nightly', 'hourly' or None."""
        if force:
            return "hourly"
        moment = now()
        if moment.hour >= NIGHT_FROM_HOUR and self.data.get("nightly_day") != moment.date().isoformat():
            return "nightly"
        if time.time() - float(self.data.get("hourly_at") or 0) >= HOUR and self._fingerprint() != self.data.get("fingerprint"):
            return "hourly"
        return None

    def tick(self, force=False):
        """One round: make the copy that is due, then send the queue. Returns the status."""
        if not self.enabled:
            return self.status()
        with self.lock:
            self.status_["busy"] = True
            try:
                kind = self.due(force)
                if kind:
                    self.snapshot(kind)
                if self.queued() and (force or time.time() >= self.next_try):
                    self.send()
                    self.failures, self.next_try = 0, 0.0
                    self.status_.update({"last_ok_at": now().isoformat(timespec="seconds"), "error": None})
                elif not self.queued() and not self.connected():
                    self.connect()
            except CloudError as e:
                self.failures += 1
                self.next_try = time.time() + RETRY[min(self.failures, len(RETRY)) - 1]
                self.status_["error"] = e.reason
                log.warning("cloud: not sent (%s %s); tries again later", e.reason, e.detail)
            except (ShopError, OSError, sqlite3.Error) as e:
                self.status_["error"] = "copy_failed"
                log.exception("cloud: the copy on this PC failed: %s", e)
            finally:
                self.status_["busy"] = False
                self.data["status"] = {k: self.status_[k] for k in ("last_ok_at", "last_copy", "error")}
                try:
                    self._save()
                except OSError:
                    pass
        return self.status()

    def loop(self, stop, interval=60):
        """Runs beside the server for as long as the program is open."""
        while not stop.is_set():
            force = self.wake.is_set()
            self.wake.clear()
            try:
                self.tick(force)
            except Exception:   # never stop selling because of the cloud
                log.exception("cloud round failed")
            self.wake.wait(interval)

    def now_please(self):
        self.wake.set()
        return {"started": True}

    def status(self):
        """What the owner sees. Never the secret, never the address of the space."""
        return {"enabled": self.enabled, "connected": bool(self.connected()), "queued": len(self.queued()),
                "last_ok_at": self.status_.get("last_ok_at"), "last_copy": self.status_.get("last_copy"),
                "error": self.status_.get("error"), "busy": self.status_.get("busy", False),
                "pc": (self.data.get("pc") or "")[:6]}

    # ---------- restore on a new PC ----------
    def recovery_code(self):
        """Only on this PC (launcher `cloud-code`): what a NEW PC needs to read this PC's copies."""
        if not self.connected():
            return None
        raw = json.dumps({"u": self.url, "p": self.data["pc"], "s": self.data["secret"]}, separators=(",", ":")).encode()
        return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_code(code):
    code = "".join(str(code).split())
    if not code.startswith(CODE_PREFIX):
        raise CloudError("bad_code")
    body = code[len(CODE_PREFIX):]
    try:
        d = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        url, pc, secret = str(d["u"]), str(d["p"]), str(d["s"])
    except (ValueError, KeyError, TypeError):
        raise CloudError("bad_code") from None
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise CloudError("bad_code")
    if not PC_RE.fullmatch(pc) or len(secret) < 32:
        raise CloudError("bad_code")
    return Client(url, pc, secret)


def restore(code, data_folder, work_folder, name=None):
    """Bring the newest copy (or `name`) of another PC to this PC's EMPTY data folder.
    The copy is checked twice (compressed and inside) and by the normal backup check
    before it becomes the data. This PC keeps its own cloud space: it never sends into
    the old PC's space."""
    client = parse_code(code)
    copies = client.copies()
    if not copies:
        raise CloudError("no_copies")
    pick = next((c for c in copies if c["name"] == name), None) if name else max(copies, key=lambda c: c["name"].split("-", 1)[1])
    if not pick:
        raise CloudError("not_found")
    work = pathlib.Path(work_folder)
    folder = work / ("cloud-" + pick["name"])
    folder.mkdir(parents=True, exist_ok=True)
    gz = folder / (DB_NAME + ".gz")
    client.download(pick["name"], gz)
    with gzip.open(gz, "rb") as src, open(folder / DB_NAME, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    gz.unlink()
    if _sha256_file(folder / DB_NAME) != pick["dbSha256"]:
        raise CloudError("download_mismatch")
    manifest = {"product": PRODUCT_ID, "app_version": pick.get("app"), "schema": pick.get("schema"), "mode": "shop",
                "created_at": pick.get("at"), "label": "cloud-" + pick["kind"], "integrity": "ok",
                "sha256": pick["dbSha256"], "size": (folder / DB_NAME).stat().st_size}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    backup.restore_backup(folder, data_folder)
    return pick


def cloud_url(compiled):
    """SM_CLOUD_URL wins (tests, support; empty = off). Otherwise the built-in cloud, but only
    in the installed program: running from the source code never sends a copy anywhere."""
    if "SM_CLOUD_URL" in os.environ:       # "off" too: Windows deletes an empty environment variable
        value = os.environ["SM_CLOUD_URL"].strip()
        return "" if value.lower() in ("", "off") else value
    return BUILT_IN_URL if compiled else ""
