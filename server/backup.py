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

from .core import ShopError, now
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
                "created_at": now().isoformat(timespec="seconds"), "label": label, "integrity": check,
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
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
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
    out = []
    for manifest in sorted(pathlib.Path(destination).glob("*/manifest.json"), reverse=True):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["folder"] = str(manifest.parent)
            out.append(data)
        except (OSError, ValueError):
            continue
    return out
