"""Start the shop on this PC: checks, daily backup, server, the program's window.

Two ways to run, one code:
  * installed program (SuperMarket.exe, compiled like the Yousef app): the data lives in
    %ProgramData%\\SuperMarket and is never touched by an update or an uninstall; the screen
    opens in its own Edge application window (server/window.py); the server keeps running
    when the window is closed, and "SuperMarket.exe stop" (Start menu) stops it cleanly;
  * portable folder (start.bat with the bundled python\\python.exe): the data lives next to it.
SQLite (WAL, synchronous=FULL) keeps every committed sale either way. Messages for the owner
use a Windows message box so Arabic shows correctly; on other systems they are printed.
"""

import http.client
import json
import os
import pathlib
import secrets
import socket
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
COMPILED = "__compiled__" in globals()       # Nuitka build (SuperMarket.exe)


def home_dir():
    """Where the shop's data, backups and logs live. SM_HOME wins (tests, support);
    the installed program uses %ProgramData%\\SuperMarket; the portable folder its own folder."""
    if os.environ.get("SM_HOME"):
        return pathlib.Path(os.environ["SM_HOME"])
    if COMPILED:
        base = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE") or os.path.expanduser("~")
        return pathlib.Path(base) / "SuperMarket"
    return ROOT


HOME = home_dir()

from server import backup  # noqa: E402
from server.app import serve  # noqa: E402
from server.core import ShopError  # noqa: E402
from server.version import PRODUCT_ID, VERSION  # noqa: E402
from server import window  # noqa: E402
from server import cloud  # noqa: E402

KEEP_DAILY = 30
MESSAGES = {
    "stopped": ("أُغلق البرنامج. كل العمليات المحفوظة موجودة.", "The program was stopped. Every saved operation is kept."),
    "not_running": ("البرنامج لا يعمل الآن.", "The program is not running now."),
    "not_writable": ("لا يمكن الكتابة في مجلد البرنامج:\n{folder}\n\nانقل مجلد البرنامج كله إلى مكان تملكه مثل C:\\SuperMarket ثم شغّله من جديد.",
                     "The program folder is not writable:\n{folder}\n\nMove the whole folder to a place you own, e.g. C:\\SuperMarket, and start again."),
    "no_access_user": ("حساب ويندوز هذا لا يملك صلاحية على بيانات المحل (محمية لحساب التثبيت).\nسجّل الدخول بحساب ويندوز الذي ثبّت البرنامج، أو اطلب من المسؤول تشغيله هناك، أو أعد التثبيت واختر «السماح لكل حسابات ويندوز».",
                       "This Windows account has no access to the shop data (it is protected for the account that installed the program).\nSign in with the Windows account that installed it, or ask the administrator to start it there, or reinstall and choose \"Allow every Windows account\"."),
    "port_busy": ("المنفذ {port} مستخدم من برنامج آخر. أغلق البرنامج الآخر أو اتصل بالدعم.",
                  "Port {port} is used by another program. Close it or call support."),
    "other_shop": ("برنامج سوبر ماركت آخر (مجلد بيانات مختلف) يعمل على المنفذ {port}.\nأغلق نافذته أولًا ثم شغّل هذا المحل.",
                   "Another Super Market (a different data folder) is running on port {port}.\nClose its window first, then start this shop."),
    "restart_for_lan": ("البرنامج يعمل الآن لهذا الجهاز فقط.\nلتشغيله لبقية أمناء الصناديق: أغلق نافذته ثم شغّل start-lan.bat.",
                        "The program is running for this PC only.\nTo serve other tills: close its window, then run start-lan.bat."),
    "reset_done": ("أُعيد تجهيز نسخة التدريب. حُفظت البيانات السابقة في المجلد:\n{folder}\nبيانات المحل الحقيقية لم تُمسّ.",
                   "The training copy was reset. The previous data was kept in:\n{folder}\nThe real shop data was not touched."),
    "reset_running": ("أغلق نافذة التدريب أولًا ثم أعد المحاولة.", "Close the training window first, then try again."),
    "start_failed": ("تعذر تشغيل البرنامج: {error}\n\nسجل التفاصيل في:\n{log}", "The program could not start: {error}\n\nDetails in:\n{log}"),
}


def tell(key, **values):
    ar, en = MESSAGES[key]
    text = ar.format(**values) + "\n\n" + en.format(**values)
    if sys.platform == "win32" and not os.environ.get("SM_QUIET"):    # SM_QUIET: automated checks, nobody to click OK
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


def local_instance(data):
    """Instance id stored in THIS folder's database (read-only), or None."""
    path = data / "shop.db"
    if not path.exists():
        return None
    import sqlite3
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='instance_id'").fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, ValueError):
        return None


def running_here(port, mode, data=None):
    """None: port free. 'same': this very shop answers. 'other': another
    Super Market (other folder/mode). False: some other program."""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/api/state")
        state = json.loads(conn.getresponse().read())
        conn.close()
    except (OSError, ValueError):
        return None if port_free(port) else False
    if "version" not in state or state.get("mode") != mode:
        return "other" if "version" in state else False
    mine = local_instance(data) if data is not None else None
    if mine is not None and state.get("instance") != mine:
        return "other"
    return ("same", bool(state.get("lan")))


def port_free(port):
    with socket.socket() as s:
        if sys.platform != "win32":        # a just-stopped program leaves TIME_WAIT behind; that is not "another program"
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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


def backup_loop(store, folder, stop, interval=1800):
    """A program that stays open for days still gets one verified copy per day
    (the start-up copy alone would miss every day after the first)."""
    while not stop.wait(interval):
        try:
            daily_backup(store, folder)
        except Exception:   # a failed copy must never stop selling; it is logged and retried
            import logging
            logging.getLogger("supermarket").exception("scheduled backup failed")


def owner_recovery(data):
    """New owner password typed ON the shop computer (same as tools/shopctl.py owner-recovery). Nothing else changes."""
    import getpass
    from server.db import Store
    from server.services import Shop
    print("استعادة دخول صاحب المحل — تعمل على جهاز المحل فقط ولا تحذف أي بيانات.")
    username = input("اسم دخول صاحب المحل: ").strip()
    first = getpass.getpass("كلمة المرور الجديدة (8 أحرف على الأقل): ")
    if first != getpass.getpass("اكتبها مرة ثانية: "):
        print("الكلمتان غير متطابقتين. لم يتغير شيء.")
        return 1
    store = Store(data, "shop")
    try:
        Shop(store).auth.recover_owner(username, first)
    except ShopError as exc:
        print("لم يتغير شيء:", exc.code)
        return 1
    finally:
        store.close()
    print("تم. ادخل بكلمة المرور الجديدة.")
    return 0


def stop(port, data):
    """Stop the running program cleanly (Start menu «إيقاف السوبر ماركت»)."""
    try:
        key = (data / "stop.key").read_text(encoding="utf-8").strip()
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/api/local/stop", "{}", {"X-SM": "1", "X-SM-Stop": key, "Content-Type": "application/json"})
        ok = conn.getresponse().status == 200
        conn.close()
        return ok
    except OSError:
        return False


def console_utf8():
    """Arabic in a console window or a redirected output on Windows: a cp1252 or OEM code page
    would crash the print (found by CI). UTF-8 always encodes it; an odd byte never stops the command."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def cloud_code(data):
    """Start menu «كود استعادة السحابة»: shown only on this PC, for the owner to keep on paper."""
    from server.db import Store
    store = Store(data, "shop")
    try:
        c = cloud.Cloud(HOME, store, cloud.cloud_url(COMPILED))
        code = c.recovery_code()
    finally:
        store.close()
    if not code:
        print("هذا الجهاز لم يتصل بالسحابة بعد. شغّل البرنامج وهو متصل بالإنترنت ثم أعد المحاولة.")
        return 1
    print("رمز استعادة نسخ هذا الجهاز من السحابة. اكتبه على ورقة واحفظه مع صاحب المحل فقط:")
    print("يستطيع أي شخص يمتلك هذا الرمز قراءة نسخ المحل.\n")
    print(code + "\n")
    return 0


def cloud_restore(data):
    """Start menu «استعادة من السحابة» on a NEW PC: the newest copy of the old PC becomes this
    PC's data. Refused if this PC already has shop data (nothing is ever overwritten)."""
    if running_here(8765, "shop", data) is not None:
        print("أغلق البرنامج أولًا من «إيقاف السوبر ماركت» في قائمة ابدأ، ثم أعد المحاولة.")
        return 1
    if (data / "shop.db").exists():
        import sqlite3
        conn = sqlite3.connect((data / "shop.db").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            used = conn.execute("SELECT (SELECT COUNT(*) FROM users) + (SELECT COUNT(*) FROM ops)").fetchone()[0]
        except sqlite3.Error:
            used = 1
        finally:
            conn.close()
        if used:
            print("يوجد على هذا الجهاز بيانات محل بالفعل. الاستعادة تكون على جهاز جديد فقط؛ لم يتغير شيء.")
            return 1
        import datetime   # a new PC opened the program once after Setup: that empty folder is kept aside, never deleted
        data.rename(data.with_name("shop-data-empty-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    code = input("أدخل رمز استعادة الجهاز القديم: ").strip()
    try:
        pick = cloud.restore(code, data, HOME / "restore-work")
    except (cloud.CloudError, ShopError) as exc:
        print("لم تتم الاستعادة ولم يتغير شيء:", getattr(exc, "reason", None) or getattr(exc, "code", exc))
        return 1
    print("تمت الاستعادة من نسخة", pick["name"], "— شغّل البرنامج الآن. هذا الجهاز سيأخذ مساحة جديدة خاصة به على السحابة.")
    return 0


def start_cloud(app, mode, stop_event):
    """Hourly and nightly copies to the owner's cloud: the real shop only, never training."""
    url = cloud.cloud_url(COMPILED)
    if mode != "shop" or not url:
        return None
    app.cloud = cloud.Cloud(HOME, app.store, url, label=str(app.shop.settings().get("shop_name") or ""))
    threading.Thread(target=app.cloud.loop, args=(stop_event,), daemon=True, name="cloud").start()
    return app.cloud


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] in (["owner-recovery"], ["cloud-code"], ["cloud-restore"]):
        console_utf8()
    if argv[:1] == ["owner-recovery"]:              # Start menu: «استعادة دخول المالك» (opens in a console window)
        return owner_recovery(HOME / "shop-data")
    if argv[:1] == ["cloud-code"]:
        return cloud_code(HOME / "shop-data")
    if argv[:1] == ["cloud-restore"]:
        return cloud_restore(HOME / "shop-data")
    training = "training" in argv
    lan = "lan" in argv
    mode = "training" if training else "shop"
    port = 8766 if training else 8765
    data = HOME / ("training-data" if training else "shop-data")
    if training and "reset" in argv:        # training only: the shop folder can never be reset from here
        if running_here(port, mode, data) is not None:
            tell("reset_running")
            return 6
        if data.exists():
            import datetime
            kept = HOME / ("training-data-previous-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            data.rename(kept)
            tell("reset_done", folder=kept)
            return 0
    if "stop" in argv:
        done = stop(port, data)
        tell("stopped" if done else "not_running")
        return 0 if done else 7
    url = "http://127.0.0.1:%d/" % port
    if not writable(data):
        # H6: another Windows account of this PC. The data is protected for the installing account; when the
        # program already runs there, this account only opens its window; autostart of this account stays silent.
        if COMPILED:
            running = running_here(port, mode, None)
            if isinstance(running, tuple):
                if "--background" not in argv:
                    window.open_ui(url)
                return 0
            if "--background" in argv:
                return 0
            tell("no_access_user")
            return 2
        tell("not_writable", folder=HOME)
        return 2
    state = running_here(port, mode, data)
    if state == "other":
        tell("other_shop", port=port)
        return 3
    if isinstance(state, tuple):
        if lan and not state[1]:
            tell("restart_for_lan")
            return 5
        window.open_ui(url)
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
        daily_backup(httpd.app.store, HOME / ("backups-" + mode))
    except (ShopError, OSError) as exc:
        logging.exception("start failed")
        tell("start_failed", error=getattr(exc, "code", str(exc)), log=log_file)
        return 4
    if sys.platform == "win32" and not COMPILED:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("السوبر ماركت %s — لا تغلق هذه النافذة أثناء العمل" % VERSION)
    print("%s %s (%s) %s — close this window to stop." % (PRODUCT_ID, VERSION, mode, url), flush=True)
    # a stop key only this PC's files can read: "SuperMarket.exe stop" uses it, nothing over the network can
    token = secrets.token_hex(16)
    (data / "stop.key").write_text(token, encoding="utf-8")
    httpd.app.stop_key = token
    if "--background" not in argv:
        threading.Timer(0.8, window.open_ui, (url, logging.getLogger("supermarket").info)).start()
    stop_backups = threading.Event()
    threading.Thread(target=backup_loop, args=(httpd.app.store, HOME / ("backups-" + mode), stop_backups), daemon=True).start()
    start_cloud(httpd.app, mode, stop_backups)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_backups.set()
        if httpd.app.cloud:
            httpd.app.cloud.wake.set()
        httpd.app.hub.stop()
        httpd.server_close()
        httpd.app.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
