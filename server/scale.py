"""Scales (owner request, 31 Oct 2026: support the scales common in Egyptian shops).

Two kinds, both without any vendor software:

1. **Label-printing scales** (the counter scale prints a sticker with an EAN-13 barcode:
   CAS CL-series, DIGI SM-series, Aclas LS-series, Rongta RLS, Mettler Toledo bPlus/Tiger,
   Dibal, Bizerba and many others). The barcode layout is set on the scale itself, so the
   program never assumes one: `detect_label_layout` finds it from ONE real sticker and
   the weight (or price) printed on it. The common layouts are offered as one-tap choices.

2. **Scales connected to the till PC** (RS-232, or USB that Windows shows as a COM port).
   `parse_weight` reads the text that such scales send. Most send the weight by themselves
   all the time ("continuous"); others answer a request character ("W" in the Toledo 8217
   style, ENQ, "P"). Nothing here claims a model was tested: see docs/SCANNER_COMPATIBILITY.md.

Weights are integer grams. No float is ever used: "1.250" is split into its digits.
"""

import re
import threading
import time

from .barcode import valid_gs1

# ------------------------------------------------------------------ label-printing scales
# Common EAN-13 layouts: prefix (2 digits, set on the scale) + item code + value + check digit.
# item_len + value_len = 10 (positions 2..11); the check digit is position 12.
LAYOUTS = (
    {"id": "w5", "item_len": 5, "value_kind": "weight", "decimals": 3},   # 2P IIIII WWWWW C  (the most common)
    {"id": "p5", "item_len": 5, "value_kind": "price", "decimals": 2},    # 2P IIIII PPPPP C
    {"id": "w4", "item_len": 4, "value_kind": "weight", "decimals": 3},   # 2P IIII WWWWWW C
    {"id": "p4", "item_len": 4, "value_kind": "price", "decimals": 2},    # 2P IIII PPPPPP C
    {"id": "w6", "item_len": 6, "value_kind": "weight", "decimals": 3},   # 2P IIIIII WWWW C
    {"id": "p6", "item_len": 6, "value_kind": "price", "decimals": 2},    # 2P IIIIII PPPP C
)


def template_for(layout_id, prefix, name="scale"):
    """A label template (server.barcode.check_template shape) for one of LAYOUTS."""
    lay = next((x for x in LAYOUTS if x["id"] == layout_id), None)
    if lay is None or not re.fullmatch(r"2\d", str(prefix or "")):
        return None
    return {"name": name, "prefix": str(prefix), "length": 13, "item_start": 2, "item_len": lay["item_len"],
            "value_start": 2 + lay["item_len"], "value_len": 10 - lay["item_len"], "value_kind": lay["value_kind"],
            "decimals": lay["decimals"]}


def _scaled(text, decimals):
    """'1.25' with decimals=3 -> 1250 (integer, no float). None if it has more decimals."""
    text = text.strip().replace(",", ".").replace("٫", ".")
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    m = re.fullmatch(r"(\d{1,6})(?:\.(\d{0,3}))?", text)
    if not m:
        return None
    frac = (m.group(2) or "")
    if len(frac) > decimals:
        if frac[decimals:].strip("0"):
            return None
        frac = frac[:decimals]
    return int(m.group(1)) * 10 ** decimals + int(frac.ljust(decimals, "0") or 0)


def detect_label_layout(code, printed=None, item_code=None):
    """Find the layout of a real sticker. `printed` is the weight in kg (e.g. "1.250") or the
    price in pounds (e.g. "52.50") written on the sticker; `item_code` the item number on it,
    when there is one. Returns (candidates, error): every layout that explains the sticker,
    the most common first. With nothing printed given, every layout that fits is returned."""
    code = re.sub(r"\D", "", str(code or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    if len(code) != 13 or not code.startswith("2"):
        return [], "not_scale_label"
    if not valid_gs1(code):
        return [], "bad_check_digit"
    item_code = re.sub(r"\D", "", str(item_code or ""))
    out = []
    for lay in LAYOUTS:
        t = template_for(lay["id"], code[:2])
        item = code[2:2 + lay["item_len"]]
        raw = int(code[t["value_start"]:12])
        if item_code and int(item) != int(item_code):
            continue
        if printed not in (None, ""):
            want = _scaled(str(printed), lay["decimals"])
            if want is None or want != raw:
                continue
        out.append(dict(t, layout=lay["id"], item_code=item, value=raw))
    return out, (None if out else "label_not_understood")


# ------------------------------------------------------------------ scales on a COM port
POLL = {"none": b"", "W": b"W", "W_CR": b"W\r", "ENQ": b"\x05", "P": b"P", "S_CR": b"S\r\n"}
# a whole number only: never a piece cut out of a longer one ("1.23456" is not "1.2345" or "6")
_ERROR = re.compile(r"\bOL\b|-{3,}|\bERR", re.I)
_NUM = re.compile(r"([+-]?)\s*(?<![\d.,])(\d{1,6}(?:[.,]\d{1,3})?)(?![\d.,]\d)\s*(kg|KG|Kg|g|G|lb|LB|oz)?")


def parse_weight(frame):
    """One frame of text from a scale -> {"grams", "stable"} or None (nothing usable).
    Understood: 'ST,GS,+  1.250kg' / 'US,NT,  0.000 kg' (CAS-style and most Chinese scales),
    '  1.250 kg', '1250 g', '\\x02001.250\\r' (Toledo 8217 style, kg), status letters such as
    '?' / 'M' / 'US' for a moving weight, 'OL' / '----' for overload. Pounds are refused."""
    if isinstance(frame, bytes):
        frame = frame.decode("latin-1", "replace")
    text = frame.replace("\x02", " ").replace("\x03", " ").strip()
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    if not text or _ERROR.search(text):
        return None
    found = list(_NUM.finditer(text))
    # the weight is the number with a unit, else the last number (CAS-style lines put their flags first)
    m = next((f for f in reversed(found) if f.group(3)), found[-1] if found else None)
    if not m:
        return None
    sign, number, unit = m.group(1), m.group(2).replace(",", "."), (m.group(3) or "").lower()
    if unit in ("lb", "oz"):
        return None
    if unit == "g":
        grams = _scaled(number, 0)
        if grams is None:               # "1250.0 g"
            grams = _scaled(number, 1)
            grams = grams // 10 if grams is not None and grams % 10 == 0 else None
    else:                               # kg is the default unit of every scale sold for Egypt
        grams = _scaled(number, 3)
    if grams is None:
        return None
    if sign == "-":
        grams = -grams
    moving = bool(re.search(r"\bUS\b|\?|\bM\b|motion", text, re.I))
    return {"grams": grams, "stable": not moving}


class ScaleReader:
    """Keeps the latest weight of one scale on a COM/TCP port. Request-style scales are asked
    a few times a second; continuous ones are only listened to. A reading older than
    STALE seconds is not offered to the till (the cable may be out)."""

    STALE = 2.0

    def __init__(self, cfg, open_transport):
        self.cfg = cfg
        self.open_transport = open_transport
        self.status, self.error = "starting", None
        self.latest = None              # {"grams", "stable", "at"}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="scale-%s" % cfg["id"], daemon=True)

    def feed(self, frames):
        for f in frames:
            r = parse_weight(f)
            if r is not None:
                self.latest = dict(r, at=time.monotonic())
            elif _ERROR.search(f.decode("latin-1", "replace") if isinstance(f, bytes) else f):
                self.latest = None      # overload or error: the last good weight must not stay offered (review PR #18)

    def run(self):
        backoff = 0.5
        poll = POLL.get(self.cfg.get("poll", "none"), b"")
        while not self.stop.is_set():
            try:
                transport = self.open_transport(self.cfg)
            except (OSError, ValueError, KeyError) as exc:
                self.status, self.error = "disconnected", str(exc)[:200]
                self.stop.wait(backoff)
                backoff = min(backoff * 2, 5)
                continue
            self.status, self.error, backoff = "connected", None, 0.5
            buf, last_ask = b"", 0.0
            try:
                while not self.stop.is_set():
                    if poll and time.monotonic() - last_ask > 0.3:
                        transport.write(poll)
                        last_ask = time.monotonic()
                    data = transport.read(0.05)
                    if not data:
                        continue
                    buf = (buf + data)[-512:]
                    *frames, buf = re.split(rb"[\r\n\x03]+", buf)
                    self.feed(frames)
            except OSError as exc:
                self.status, self.error = "disconnected", str(exc)[:200]
            finally:
                try:
                    transport.close()
                except OSError:
                    pass

    def reading(self):
        r = self.latest
        if not r or time.monotonic() - r["at"] > self.STALE:
            return {"connected": self.status == "connected", "grams": None, "stable": False}
        return {"connected": True, "grams": r["grams"], "stable": r["stable"]}

    def describe(self):
        return {"id": self.cfg["id"], "type": self.cfg["type"], "role": "scale", "status": self.status,
                "error": self.error, "reading": self.reading()}
