"""Cloud copy (Cloudflare) tool. No packages needed. Full guide: docs/CLOUD_SYNC.md

  Owner / admin (needs the admin key: env SM_ADMIN_KEY, or the file given by --key-file):
    python tools/syncctl.py admin new-store  --url URL --name "Shop name"
    python tools/syncctl.py admin pairing    --url URL --store STORE_ID --role shop|mobile
    python tools/syncctl.py admin devices    --url URL --store STORE_ID
    python tools/syncctl.py admin revoke     --url URL --store STORE_ID --device DEVICE_ID
    python tools/syncctl.py admin recovery   --url URL --store STORE_ID                  (read out a shop's recovery code)
    python tools/syncctl.py admin status     --url URL --store STORE_ID --state pending|active|suspended
    python tools/syncctl.py admin revoke-build --url URL --build v0.1.0-pilot             (cut off one release's build token)
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
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import cloudsync  # noqa: E402
from server.core import ShopError  # noqa: E402


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
        out = cloudsync.admin_call(args.url, key, "POST", "/admin/pairing", {"store_id": args.store, "role": args.role})
        if out.get("code"):      # the one string the shop owner types in «ربط بالسحابة» (valid 15 minutes, once)
            out["connection_code"] = cloudsync.CONNECT_PREFIX + "%s-%s" % (args.store, out["code"])
        return out
    if args.what == "devices":
        return cloudsync.admin_call(args.url, key, "GET", "/admin/stores/" + args.store)
    if args.what == "recovery":
        return cloudsync.admin_call(args.url, key, "POST", "/admin/recovery", {"store_id": args.store})
    if args.what == "status":
        return cloudsync.admin_call(args.url, key, "POST", "/admin/stores/status", {"store_id": args.store, "status": args.state})
    if args.what == "revoke-build":
        return cloudsync.admin_call(args.url, key, "POST", "/admin/builds/revoke", {"build_id": args.build})
    return cloudsync.admin_call(args.url, key, "POST", "/admin/revoke", {"store_id": args.store, "device_id": args.device})


def ids(args):
    if args.store:
        return args.store, args.code
    return cloudsync.parse_connect_code(args.code)


def run_join(args):
    store, code = ids(args)
    out = cloudsync.join(args.data, args.url, store, code)
    return {"connected": True, **out,
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
    cloudsync.leave(args.data)
    return {"left": True, "note": "the cloud keeps its copy; revoke this PC with 'admin revoke' if it is lost"}


def run_restore(args):
    store, code = ids(args)
    config = cloudsync.pair(args.url, store, code, cloudsync.device_name())
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
    a.add_argument("what", choices=["new-store", "pairing", "devices", "revoke", "recovery", "status", "revoke-build"])
    a.add_argument("--url", default=cloudsync.BUILT_IN_URL)
    a.add_argument("--name")
    a.add_argument("--store")
    a.add_argument("--role", choices=["shop", "mobile"], default="mobile")
    a.add_argument("--device")
    a.add_argument("--state", choices=["pending", "active", "suspended"])
    a.add_argument("--build")
    a.add_argument("--key-file")
    for name in ("join", "restore"):
        j = sub.add_parser(name)
        j.add_argument("--url", default=cloudsync.BUILT_IN_URL)
        j.add_argument("--store", help="not needed when --code is the whole SMC2-... connection code")
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
