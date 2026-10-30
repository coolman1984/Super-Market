"""Start the shop on this PC: checks, daily backup, server, browser.

Run by start.bat with the bundled python\\python.exe (no installation).
Closing this window stops the program; SQLite (WAL, synchronous=FULL) keeps
every committed sale. Messages for the owner use a Windows message box so
Arabic shows correctly; on other systems they are printed.
"""

import http.client
import json
import pathlib
import socket
import sys
import threading
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from server import backup  # noqa: E402
from server.app import serve  # noqa: E402
from server.core import ShopError  # noqa: E402
from server.version import PRODUCT_ID, VERSION  # noqa: E402

KEEP_DAILY = 30
MESSAGES = {
    "not_writable": ("لا يمكن الكتابة في مجلد البرنامج:\n{folder}\n\nانقل مجلد البرنامج كله إلى مكان تملكه مثل C:\\SuperMarket ثم شغّله من جديد.",
                     "The program folder is not writable:\n{folder}\n\nMove the whole folder to a place you own, e.g. C:\\SuperMarket, and start again."),
    "port_busy": ("المنفذ {port} مستخدم من برنامج آخر. أغلق البرنامج الآخر أو اتصل بالدعم.",
                  "Port {port} is used by another program. Close it or call support."),
    "start_failed": ("تعذر تشغيل البرنامج: {error}\n\nسجل التفاصيل في:\n{log}", "The program could not start: {error}\n\nDetails in:\n{log}"),
}


def tell(key, **values):
    ar, en = MESSAGES[key]
    text = ar.format(**values) + "\n\n" + en.format(**values)
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "السوبر ماركت / Super Market", 0x10)
    else:
        print(text, file=sys.stderr)


def writable(folder):
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def running_here(port, mode):
    """Our own server already answers on this port -> just open the browser."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/state")
        state = json.loads(conn.getresponse().read())
        conn.close()
        return state.get("mode") == mode and "version" in state
    except (OSError, ValueError):
        return None if port_free(port) else False


def port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def daily_backup(store, folder):
    """One verified copy per day at start-up (and before a new version runs)."""
    today = backup.now().strftime("%Y%m%d")
    existing = backup.list_backups(folder)
    last_version = store.setting("last_app_version")
    label = None
    if last_version is not None and last_version != VERSION:
        label = "before-" + VERSION
    elif not any(today in b["folder"] for b in existing):
        label = "daily"
    if label and store.scalar("SELECT COUNT(*) FROM ops"):
        backup.create_backup(store, folder, label)
    daily = [b for b in backup.list_backups(folder) if b.get("label") == "daily"]
    for old in daily[KEEP_DAILY:]:
        import shutil
        shutil.rmtree(old["folder"], ignore_errors=True)
    store.tx(lambda: store.db.execute(
        "INSERT INTO settings VALUES('last_app_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (json.dumps(VERSION),)))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    training = "training" in argv
    lan = "lan" in argv
    mode = "training" if training else "shop"
    port = 8766 if training else 8765
    data = ROOT / ("training-data" if training else "shop-data")
    if not writable(data):
        tell("not_writable", folder=ROOT)
        return 2
    state = running_here(port, mode)
    url = "http://127.0.0.1:%d/" % port
    if state:
        webbrowser.open(url)
        return 0
    if state is False:
        tell("port_busy", port=port)
        return 3
    import logging
    log_file = data / "server.log"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_file, encoding="utf-8")])
    try:
        httpd = serve(data, mode, port=port, lan=lan)
        daily_backup(httpd.app.store, ROOT / ("backups-" + mode))
    except (ShopError, OSError) as exc:
        logging.exception("start failed")
        tell("start_failed", error=getattr(exc, "code", str(exc)), log=log_file)
        return 4
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("السوبر ماركت %s — لا تغلق هذه النافذة أثناء العمل" % VERSION)
    print("%s %s (%s) %s — close this window to stop." % (PRODUCT_ID, VERSION, mode, url), flush=True)
    threading.Timer(0.8, webbrowser.open, (url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.app.hub.stop()
        httpd.server_close()
        httpd.app.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
