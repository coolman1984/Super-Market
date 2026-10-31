"""A short shop journey against the real SuperMarket.exe (CI on a clean Windows machine).

    python tools/journey_exe.py first <home> <exe>     set the shop up, add an item, receive it, sell it, stop
    python tools/journey_exe.py after <home> <exe>     after an update (or a restart): everything is still there

<home> is SM_HOME (the data folder); "installed" means no override: the program uses its real, protected
%ProgramData%\SuperMarket. The program runs in the background (no window, no message boxes).
"""
import http.client
import json
import os
import subprocess
import sys
import time
import uuid

PORT = 8765


def call(method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    headers = {"Content-Type": "application/json", "X-SM": "1"}
    if cookie:
        headers["Cookie"] = "sm_session=" + cookie
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    resp = conn.getresponse()
    data = json.loads(resp.read() or b"null")
    new = resp.getheader("Set-Cookie")
    conn.close()
    if new and "sm_session=" in new:
        cookie = new.split("sm_session=")[1].split(";")[0]
    return resp.status, data, cookie


def env_for(home, **extra):
    env = dict(os.environ, SM_QUIET="1", **extra)
    if home == "installed":
        env.pop("SM_HOME", None)
    else:
        env["SM_HOME"] = home
    return env


def data_folder(home):
    if home == "installed":
        return os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "SuperMarket")
    return home


def start(home, exe):
    env = env_for(home, SM_NO_WINDOW="1")
    cmd = [exe, "--background"] if exe.lower().endswith(".exe") else [exe, "launcher.py", "--background"]
    proc = subprocess.Popen(cmd, env=env)
    for _ in range(90):
        time.sleep(1)
        try:
            status, state, _ = call("GET", "/api/state")
            if status == 200 and "version" in state:
                return proc
        except OSError:
            pass
    raise SystemExit("the program did not answer on port %d" % PORT)


def stop(home, exe):
    env = env_for(home)
    cmd = [exe, "stop"] if exe.lower().endswith(".exe") else [exe, "launcher.py", "stop"]
    if subprocess.call(cmd, env=env) != 0:
        raise SystemExit("stop failed")
    for _ in range(30):
        time.sleep(1)
        try:
            call("GET", "/api/state")
        except OSError:
            return
    raise SystemExit("the program did not stop")


def login():
    status, data, cookie = call("POST", "/api/login", {"username": "owner", "password": "owner-pass-123", "workstation": "ci"})
    assert status == 200, data
    return cookie


def first(home, exe):
    start(home, exe)
    status, data, _ = call("POST", "/api/setup", {"username": "owner", "name": "Owner", "password": "owner-pass-123", "shop_name": "CI Shop"})
    assert status == 200, data
    c = login()
    for path in ("/", "/js/app.js", "/guide/catalogue.json", "/css/af-guide.css"):
        conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
        conn.request("GET", path)
        assert conn.getresponse().status == 200, path
        conn.close()
    status, prod, _ = call("POST", "/api/products", {"code": "CI1", "name_ar": "صنف تجربة", "name_en": "CI item", "kind": "grocery",
                                                     "measure": "unit", "price": 1000}, c)
    assert status == 200, prod
    shelf = [l for l in call("GET", "/api/locations", None, c)[1] if l["code"] == "SH"][0]["id"]
    status, r, _ = call("POST", "/api/receipts", {"key": uuid.uuid4().hex, "payload": {"lines": [
        {"product_id": prod["id"], "qty": 5, "line_cost": 2500, "location_id": shelf}]}}, c)
    assert status == 200, r
    assert call("POST", "/api/shift/open", {"key": uuid.uuid4().hex, "opening_float": 10000}, c)[0] == 200
    cart = {"lines": [{"product_id": prod["id"], "qty": 2}]}
    quote = call("POST", "/api/quote", {"payload": cart}, c)[1]
    status, sale, _ = call("POST", "/api/sales", {"key": uuid.uuid4().hex, "payload": dict(cart, cash_tendered=5000,
                                                   expected_total=quote["total"], quote_hash=quote["quote_hash"])}, c)
    assert status == 200 and sale["change"] == 3000, sale
    stop(home, exe)
    assert os.path.exists(os.path.join(data_folder(home), "shop-data", "shop.db")), "data was not written to " + data_folder(home)
    print("first journey OK: sale", sale["no"])


def after(home, exe):
    start(home, exe)
    c = login()
    products = call("GET", "/api/products?q=CI1", None, c)[1]
    assert products and products[0]["available"] == 3, products
    assert call("GET", "/api/reports/day", None, c)[1]["sales_count"] >= 1
    stop(home, exe)
    print("after OK: the item, its stock and the sale are still there")


if __name__ == "__main__":
    {"first": first, "after": after}[sys.argv[1]](sys.argv[2], sys.argv[3])
