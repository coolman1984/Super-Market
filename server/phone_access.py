"""A phone is reports-only. Detection needs no shop setting.

Two signals, either one is enough:
- the session workstation id starts with ``phone-`` (the browser chooses this)
- a capability header on the request: ``X-SM-Client: phone`` or ``X-SM-Capability: reports``

Count and every other write then answer 403. Reading reports stays open.
The shop PC (a ``till-`` id, no phone header) is unchanged.
"""

import re

from .core import Forbidden

_PHONE_WS = re.compile(r"^phone-", re.IGNORECASE)
# Sign-in and the person's own screen. Not selling, counting, or settings.
_PHONE_POST_OK = {
    "/api/login",
    "/api/logout",
    "/api/account/password",
    "/api/account/pin",
    "/api/account/lock",
    "/api/account/unlock",
    "/api/guide/progress",
}


def is_phone(actor, headers):
    ws = str((actor or {}).get("workstation") or "")
    if _PHONE_WS.match(ws):
        return True
    h = headers or {}
    client = str(h.get("X-SM-Client") or "").strip().lower()
    if client == "phone":
        return True
    cap = str(h.get("X-SM-Capability") or "").strip().lower()
    return cap == "reports"


def reject_if_phone(actor, headers, method, path):
    if not is_phone(actor, headers):
        return
    if path == "/api/counts" or path.startswith("/api/counts/"):
        raise Forbidden("phone_reports_only")
    if method == "POST" and path not in _PHONE_POST_OK:
        raise Forbidden("phone_reports_only")
