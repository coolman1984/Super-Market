"""Backups through the SQLite online backup API (safe with WAL and while the
shop is selling), each with a manifest: schema version, app version, SHA-256
and integrity check. Restore always goes to a NEW folder and is verified
before anyone points the program at it; the original is never overwritten.
"""

import hashlib
import json
import pathlib
import shutil
import sqlite3

from .core import ShopError, now, now_iso
from .db import DB_NAME
from .version import PRODUCT_ID, VERSION


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def create_backup(store, destination, label="manual"):
    destination = pathlib.Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime("%Y%m%d-%H%M%S")
    folder = destination / ("%s-%s-%s" % (store.mode, stamp, label))
    n = 1
    while folder.exists():
        n += 1
        folder = destination / ("%s-%s-%s-%d" % (store.mode, stamp, label, n))
    folder.mkdir()
    seq = max([b.get("seq", 0) for b in list_backups(destination)] or [0]) + 1     # order never depends on the PC clock (M6)
    target_path = folder / DB_NAME
    target = sqlite3.connect(str(target_path))
    try:
        with store.lock:
            store.db.backup(target)
        check = target.execute("PRAGMA integrity_check").fetchone()[0]
        version = target.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    finally:
        target.close()
    manifest = {"product": PRODUCT_ID, "app_version": VERSION, "schema": version, "mode": store.mode,
                "created_at": now().isoformat(timespec="seconds"), "label": label, "integrity": check, "seq": seq,
                "sha256": _sha256(target_path), "size": target_path.stat().st_size}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if check != "ok":
        raise ShopError("backup_integrity_failed")
    return {"folder": str(folder), **manifest}


def verify_backup(folder):
    folder = pathlib.Path(folder)
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShopError("backup_manifest_missing") from exc
    path = folder / DB_NAME
    if not path.exists() or _sha256(path) != manifest.get("sha256"):
        raise ShopError("backup_checksum_mismatch")
    if manifest.get("product") != PRODUCT_ID:
        raise ShopError("backup_other_product")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ShopError("backup_integrity_failed")
    finally:
        conn.close()
    return manifest


def restore_backup(folder, new_data_folder):
    """Copy a verified backup into an empty new data folder."""
    manifest = verify_backup(folder)
    target = pathlib.Path(new_data_folder)
    if target.exists() and any(target.iterdir()):
        raise ShopError("restore_target_not_empty")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pathlib.Path(folder) / DB_NAME, target / DB_NAME)
    return manifest


def list_backups(destination):
    """Newest first by the sequence number inside each manifest (copies made before it existed sort by name)."""
    out = []
    for manifest in pathlib.Path(destination).glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["folder"] = str(manifest.parent)
            out.append(data)
        except (OSError, ValueError):
            continue
    out.sort(key=lambda b: (b.get("seq", 0), pathlib.Path(b["folder"]).name), reverse=True)
    return out


# ---------- retention, free space, a second place for the copies (review H5, M7) ----------
KEEP = {"daily": 30, "before": 5}          # manual copies are the owner's own: never deleted by the program
LOW_DISK_BYTES = 2 * 1024 ** 3
EXTERNAL_DIR = "SuperMarket-backups"


def _kind(label):
    return "before" if str(label or "").startswith("before-") else label


def prune(destination, keep=None):
    """Keep the newest N copies of each automatic kind. Returns the folders removed."""
    keep = keep or KEEP
    seen, removed = {}, []
    for b in list_backups(destination):
        kind = _kind(b.get("label"))
        if kind not in keep:
            continue
        seen[kind] = seen.get(kind, 0) + 1
        if seen[kind] > keep[kind]:
            shutil.rmtree(b["folder"], ignore_errors=True)
            removed.append(b["folder"])
    return removed


def disk_free(path):
    probe = pathlib.Path(path)
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(str(probe)).free


def check_external(root, home=None):
    """The second place must be an existing folder (a drive that is not plugged in is never 'created'
    on the shop disk), writable, and not inside the program's own data folder."""
    root = pathlib.Path(str(root))
    try:
        if not root.is_absolute() or not root.is_dir():
            raise ShopError("backup2_unusable", {"why": "missing"})
        if home is not None and pathlib.Path(home).resolve() in (root.resolve(), *root.resolve().parents):
            raise ShopError("backup2_unusable", {"why": "inside_data"})
        probe = root / ".sm-write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ShopError("backup2_unusable", {"why": "not_writable"}) from exc
    return root


def copy_external(folder, root, home=None):
    """Copy one verified backup into <root>/SuperMarket-backups and verify the copy by its hash."""
    root = check_external(root, home)
    folder = pathlib.Path(folder)
    verify_backup(folder)
    target_root = root / EXTERNAL_DIR
    target_root.mkdir(exist_ok=True)
    target = target_root / folder.name
    if target.exists():
        try:
            verify_backup(target)
            return str(target)
        except ShopError:
            shutil.rmtree(target, ignore_errors=True)
    partial = target_root / (folder.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    try:
        shutil.copytree(folder, partial)
        verify_backup(partial)
        partial.rename(target)
    except (OSError, ShopError):
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return str(target)


def sync_external(store, local_folder, home=None):
    """Put the newest local copy into settings.backup_folder_2 (if set). Never raises: the result is
    remembered for the yellow banner (backup2_last_ok / backup2_error) and returned."""
    def remember(key, value):
        store.tx(lambda: store.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                          (key, json.dumps(value))))

    row = store.scalar("SELECT value FROM settings WHERE key='backup_folder_2'")
    root = json.loads(row) if row else ""
    if not root:
        return {"configured": False}
    newest = (list_backups(local_folder) or [None])[0]
    if newest is None:
        return {"configured": True, "copied": None}
    try:
        copy_external(newest["folder"], root, home)
    except (ShopError, OSError) as exc:
        remember("backup2_error", getattr(exc, "code", None) or "copy_failed")
        return {"configured": True, "error": getattr(exc, "code", None) or "copy_failed"}
    remember("backup2_last_ok", now_iso())
    remember("backup2_error", None)
    return {"configured": True, "copied": newest["folder"]}


def status(store, local_folder, cloud_ok_at=None):
    """What the yellow banner and the settings card show. Pure read."""
    def setting(key):
        row = store.scalar("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row) if row else None

    def days_since(iso):
        if not iso:
            return None
        import datetime as dt
        try:
            then = dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        if then.tzinfo is None:
            then = then.replace(tzinfo=now().tzinfo)
        return max(0, (now() - then).days)

    configured = bool(setting("backup_folder_2"))
    last_ok = setting("backup2_last_ok")
    newest = (list_backups(local_folder) or [None])[0]
    try:
        free = disk_free(local_folder)
    except OSError:
        free = None
    external_days = days_since(max(filter(None, [last_ok if configured else None, cloud_ok_at]), default=None))
    return {"has_data": bool(store.scalar("SELECT COUNT(*) FROM ops")), "configured": configured, "external_ok_at": last_ok,
            "external_days": external_days, "external_error": setting("backup2_error") if configured else None,
            "last_backup_at": newest.get("created_at") if newest else None,
            "free_mb": None if free is None else free // (1024 * 1024), "low_disk": free is not None and free < LOW_DISK_BYTES}
