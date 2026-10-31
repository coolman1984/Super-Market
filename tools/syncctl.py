"""Cloud copy (Cloudflare) tool. No packages needed. Full guide: docs/CLOUD_SYNC.md

  Owner / admin (needs the admin key: env SM_ADMIN_KEY, or the file given by --key-file):
    python tools/syncctl.py admin new-store  --url URL --name "Shop name"
    python tools/syncctl.py admin pairing    --url URL --store STORE_ID --role shop|mobile
    python tools/syncctl.py admin devices    --url URL --store STORE_ID
    python tools/syncctl.py admin revoke     --url URL --store STORE_ID --device DEVICE_ID
  On the shop PC:
    python tools/syncctl.py join    --url URL --store STORE_ID --code CODE [--data shop-data]
    python tools/syncctl.py status  [--data shop-data]
    python tools/syncctl.py push    [--data shop-data]        (send everything waiting now)
    python tools/syncctl.py leave   [--data shop-data]        (stop; the cloud keeps its copy)
  Disaster recovery (new PC, empty folder; the cloud copy has no passwords):
    python tools/syncctl.py restore --url URL --store STORE_ID --code CODE NEW_DATA_FOLDER
"""

import argparse
import json
import os
import pathlib
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import cloudsync  # noqa: E402
from server.core import ShopError  # noqa: E402
from server.db import Store  # noqa: E402


def admin_key(args):
    if args.key_file:
        return pathlib.Path(args.key_file).read_text(encoding="utf-8").strip()
    key = os.environ.get("SM_ADMIN_KEY", "").strip()
    if not key:
        import getpass
        key = getpass.getpass("Admin key (typing is hidden): ").strip()
    if not key:
        raise ShopError("sync_not_connected")
    return key


def run_admin(args):
    key = admin_key(args)
    if args.what == "new-store":
        return cloudsync.admin_call(args.url, key, "POST", "/admin/stores", {"name": args.name})
    if args.what == "pairing":
        return cloudsync.admin_call(args.url, key, "POST", "/admin/pairing", {"store_id": args.store, "role": args.role})
    if args.what == "devices":
        return cloudsync.admin_call(args.url, key, "GET", "/admin/stores/" + args.store)
    return cloudsync.admin_call(args.url, key, "POST", "/admin/revoke", {"store_id": args.store, "device_id": args.device})


def device_name():
    return "PC " + (socket.gethostname() or "shop")[:40]


def run_join(args):
    config = cloudsync.pair(args.url, args.store, args.code, device_name())
    if config["role"] != "shop":
        raise ShopError("sync_not_connected")
    store = Store(args.data, "shop")
    try:
        cloudsync.enable(store.db, store.mode)
    finally:
        store.close()
    cloudsync.save_config(args.data, config)
    return {"connected": True, "store_id": config["store_id"], "device_id": config["device_id"],
            "next": "restart the program: it now sends a copy of the shop to the cloud by itself"}


def run_status(args):
    sync = cloudsync.CloudSync(args.data)
    try:
        return sync.status()
    finally:
        sync.close()


def run_push(args):
    sync = cloudsync.CloudSync(args.data)
    try:
        return sync.cycle()
    finally:
        sync.close()


def run_leave(args):
    store = Store(args.data, "shop")
    try:
        cloudsync.disable(store.db)
    finally:
        store.close()
    path = pathlib.Path(args.data) / cloudsync.CONFIG_NAME
    if path.exists():
        path.unlink()
    return {"left": True, "note": "the cloud keeps its copy; revoke this PC with 'admin revoke' if it is lost"}


def run_restore(args):
    config = cloudsync.pair(args.url, args.store, args.code, device_name())
    report = cloudsync.restore_from_cloud(cloudsync.Cloud(config), args.target, progress=lambda n: print("  ...%d rows" % n, file=sys.stderr))
    cloudsync.save_config(args.target, config)
    report["next"] = ("start the program on this folder, give every user a new password with owner-recovery.bat, "
                      "and revoke the old PC's device in the cloud")
    return report


def main(argv=None):
    root = pathlib.Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("admin")
    a.add_argument("what", choices=["new-store", "pairing", "devices", "revoke"])
    a.add_argument("--url", required=True)
    a.add_argument("--name")
    a.add_argument("--store")
    a.add_argument("--role", choices=["shop", "mobile"], default="mobile")
    a.add_argument("--device")
    a.add_argument("--key-file")
    for name in ("join", "restore"):
        j = sub.add_parser(name)
        j.add_argument("--url", required=True)
        j.add_argument("--store", required=True)
        j.add_argument("--code", required=True)
        if name == "join":
            j.add_argument("--data", default=str(root / "shop-data"))
        else:
            j.add_argument("target")
    for name in ("status", "push", "leave"):
        s = sub.add_parser(name)
        s.add_argument("--data", default=str(root / "shop-data"))
    args = p.parse_args(argv)
    try:
        out = {"admin": run_admin, "join": run_join, "status": run_status, "push": run_push,
               "leave": run_leave, "restore": run_restore}[args.cmd](args)
    except ShopError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}))
        return 1
    except cloudsync.CloudError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "http": exc.status}))
        return 1
    print(json.dumps({"ok": True, **out}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
