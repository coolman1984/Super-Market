"""Owner/support tool (no packages): back up, verify and restore safely.

  python tools/shopctl.py backup  [--data shop-data] [--to backups-shop]
  python tools/shopctl.py verify  BACKUP_FOLDER
  python tools/shopctl.py restore BACKUP_FOLDER NEW_DATA_FOLDER

Restore never overwrites: it fills an EMPTY new folder. Point the program at
it (start the server with --data NEW_DATA_FOLDER) after checking the shop.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from server import backup  # noqa: E402
from server.core import ShopError  # noqa: E402
from server.db import Store  # noqa: E402
from server.services import Shop  # noqa: E402


def main(argv=None):
    root = pathlib.Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--data", default=str(root / "shop-data"))
    b.add_argument("--mode", default="shop", choices=["shop", "training"])
    b.add_argument("--to", default=None)
    v = sub.add_parser("verify")
    v.add_argument("folder")
    r = sub.add_parser("restore")
    r.add_argument("folder")
    r.add_argument("target")
    a = p.parse_args(argv)
    try:
        if a.cmd == "backup":
            store = Store(a.data, a.mode)
            try:
                out = backup.create_backup(store, a.to or str(pathlib.Path(a.data).parent / ("backups-" + a.mode)), "cli")
            finally:
                store.close()
        elif a.cmd == "verify":
            out = backup.verify_backup(a.folder)
        else:
            out = backup.restore_backup(a.folder, a.target)
            store = Store(a.target, out["mode"])
            try:
                out["reconcile"] = Shop(store).reconcile()
            finally:
                store.close()
    except ShopError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}))
        return 1
    print(json.dumps({"ok": True, **out}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
