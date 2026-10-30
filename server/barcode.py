"""Barcode text normalisation and classification. Independent of hardware:
the scanner layer delivers text, this module decides what the text means.

Rules: codes stay text (leading zeros kept); no silent 12<->13 unification;
the keyboard-layout repair is applied only when the device profile says so;
scanned data is untrusted (length cap, no control characters, URLs never
opened, QR content is not assumed to be a product).
"""

import calendar
import re

MAX_SCAN = 512
GS = "\x1d"

ARABIC_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
ARABIC_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})

# Windows "Arabic (101)" layout: character produced -> US key that produced it.
# Multi-character ligature keys come first so they are matched greedily.
_AR101 = [
    ("لإ", "T"), ("لأ", "G"), ("لآ", "B"), ("لا", "b"),
    ("ض", "q"), ("ص", "w"), ("ث", "e"), ("ق", "r"), ("ف", "t"), ("غ", "y"), ("ع", "u"),
    ("ه", "i"), ("خ", "o"), ("ح", "p"), ("ج", "["), ("د", "]"), ("ش", "a"), ("س", "s"),
    ("ي", "d"), ("ب", "f"), ("ل", "g"), ("ا", "h"), ("ت", "j"), ("ن", "k"), ("م", "l"),
    ("ك", ";"), ("ط", "'"), ("ئ", "z"), ("ء", "x"), ("ؤ", "c"), ("ر", "v"), ("ى", "n"),
    ("ة", "m"), ("و", ","), ("ز", "."), ("ظ", "/"), ("ذ", "`"),
    ("َ", "Q"), ("ً", "W"), ("ُ", "E"), ("ٌ", "R"), ("إ", "Y"), ("‘", "U"), ("÷", "I"),
    ("×", "O"), ("؛", "P"), ("ِ", "A"), ("ٍ", "S"), ("]", "D"), ("[", "F"), ("أ", "H"),
    ("ـ", "J"), ("،", "K"), ("/", "L"), ("~", "Z"), ("ْ", "X"), ("}", "C"), ("{", "V"),
    ("آ", "N"), ("’", "M"), (",", "<"), (".", ">"), ("؟", "?"), ("<", "{"), (">", "}"),
]
_ARABIC_RANGE = re.compile("[؀-ۿݐ-ݿ‘’×÷]")


class ScanError(ValueError):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}


def repair_arabic_layout(value):
    out, i = [], 0
    while i < len(value):
        for arabic, latin in _AR101:
            if value.startswith(arabic, i):
                out.append(latin)
                i += len(arabic)
                break
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def normalize(raw, profile=None):
    """Turn received scanner text into a clean code or raise ScanError."""
    profile = profile or {}
    if not isinstance(raw, str):
        raise ScanError("scan_not_text")
    if len(raw) > MAX_SCAN:
        raise ScanError("scan_too_long", {"max": MAX_SCAN})
    value = raw
    for sub in profile.get("gs_substitutes", ["<GS>", "^]"]):
        if sub:
            value = value.replace(sub, GS)
    value = value.strip("\r\n\t ")
    value = value.translate(ARABIC_DIGITS)
    if _ARABIC_RANGE.search(value):
        if profile.get("layout_fix") == "arabic101":
            value = repair_arabic_layout(value)
        else:
            raise ScanError("layout_mismatch", {"hint": "arabic101"})
    for ch in value:
        if ch != GS and (ord(ch) < 32 or ord(ch) == 127):
            raise ScanError("scan_control_char", {"char": "0x%02x" % ord(ch)})
    if not value:
        raise ScanError("scan_empty")
    return value


def visible(raw):
    """Safe display of received text: control characters as <CR>, <GS> ..."""
    names = {"\r": "<CR>", "\n": "<LF>", "\t": "<TAB>", GS: "<GS>", "\x1e": "<RS>",
             "\x04": "<EOT>", "\x02": "<STX>", "\x03": "<ETX>", "\x1b": "<ESC>"}
    out = []
    for ch in raw[:MAX_SCAN]:
        if ch in names:
            out.append(names[ch])
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("<0x%02X>" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


# ---------- GS1 ----------

def gs1_check_digit(body):
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return str((10 - total % 10) % 10)


def valid_gs1(code):
    return code.isdigit() and len(code) >= 2 and gs1_check_digit(code[:-1]) == code[-1]


def upce_to_upca(code):
    """8-digit UPC-E (number system 0/1) to 12-digit UPC-A, or None."""
    if len(code) != 8 or not code.isdigit() or code[0] not in "01":
        return None
    ns, d, check = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    upca = ns + body + check
    return upca if valid_gs1(upca) else None


# AI -> (fixed length or None for variable, max length, name)
AIS = {
    "00": (18, 18, "sscc"), "01": (14, 14, "gtin"), "02": (14, 14, "content_gtin"),
    "10": (None, 20, "batch"), "11": (6, 6, "production_date"), "13": (6, 6, "pack_date"),
    "15": (6, 6, "best_before"), "17": (6, 6, "expiry"), "21": (None, 20, "serial"),
    "30": (None, 8, "count"), "37": (None, 8, "count"),
}
for _d in range(10):
    AIS["310%d" % _d] = (6, 6, "net_kg")
    AIS["392%d" % _d] = (None, 15, "price")


def _ai_at(data, i):
    for size in (4, 3, 2):
        if data[i:i + size] in AIS:
            return data[i:i + size]
    return None


def parse_gs1(data):
    """Parse a GS1 element string (FNC1 = GS) or bracketed '(01)...' text."""
    fields = {}
    if data.startswith("("):
        for ai, value in re.findall(r"\((\d{2,4})\)([^(]*)", data):
            if ai not in AIS:
                raise ScanError("gs1_unknown_ai", {"ai": ai})
            fields[ai] = value
    else:
        i = 0
        while i < len(data):
            if data[i] == GS:
                i += 1
                continue
            ai = _ai_at(data, i)
            if not ai:
                raise ScanError("gs1_unknown_ai", {"at": i})
            fixed, maximum, _ = AIS[ai]
            i += len(ai)
            if fixed:
                value = data[i:i + fixed]
                i += fixed
            else:
                end = data.find(GS, i)
                end = len(data) if end < 0 else end
                value = data[i:end]
                i = end
            fields[ai] = value
    result = {}
    for ai, value in fields.items():
        fixed, maximum, name = AIS[ai]
        if (fixed and len(value) != fixed) or len(value) > maximum or not value:
            raise ScanError("gs1_bad_length", {"ai": ai})
        if ai in ("00", "01", "02"):
            if not valid_gs1(value):
                raise ScanError("bad_check_digit", {"ai": ai})
        if name in ("production_date", "pack_date", "best_before", "expiry"):
            if not value.isdigit():
                raise ScanError("gs1_bad_date", {"ai": ai})
            yy, mm, dd = int(value[:2]), int(value[2:4]), int(value[4:])
            if not 1 <= mm <= 12 or dd > 31:
                raise ScanError("gs1_bad_date", {"ai": ai})
            # GS1 rule simplified: years 00-49 -> 20xx, 50-99 -> 19xx; DD 00 = month end
            year = 2000 + yy if yy < 50 else 1900 + yy
            last = calendar.monthrange(year, mm)[1]
            if dd > last:
                raise ScanError("gs1_bad_date", {"ai": ai})
            value = "%04d-%02d-%02d" % (year, mm, dd or last)
        if name == "net_kg":
            decimals = int(ai[3])
            if not fields[ai].isdigit():
                raise ScanError("gs1_bad_number", {"ai": ai})
            value = int(fields[ai]) * 10 ** 3 // 10 ** decimals if decimals <= 3 else int(fields[ai]) // 10 ** (decimals - 3)
            name = "net_g"
        if name == "price":
            decimals = int(ai[3])
            if not fields[ai].isdigit():
                raise ScanError("gs1_bad_number", {"ai": ai})
            value = int(fields[ai]) * 100 // 10 ** decimals if decimals <= 2 else int(fields[ai]) // 10 ** (decimals - 2)
            name = "price_piastres"
        result[name] = value
    return result


# ---------- scale / price labels ----------

def check_template(t):
    """Validate a shop-configured weight/price label template."""
    try:
        prefix = str(t["prefix"])
        length = int(t["length"])
        item = (int(t["item_start"]), int(t["item_len"]))
        val = (int(t["value_start"]), int(t["value_len"]))
        kind = t["value_kind"]
        decimals = int(t["decimals"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScanError("bad_template") from exc
    if not prefix.isdigit() or length not in (12, 13) or kind not in ("weight", "price"):
        raise ScanError("bad_template")
    if not 0 <= decimals <= 3:
        raise ScanError("bad_template")
    for start, size in (item, val):
        if start < len(prefix) or size < 1 or start + size > length - 1:
            raise ScanError("bad_template")
    if item[0] < val[0] + val[1] and val[0] < item[0] + item[1]:
        raise ScanError("bad_template")
    return dict(prefix=prefix, length=length, item_start=item[0], item_len=item[1],
                value_start=val[0], value_len=val[1], value_kind=kind, decimals=decimals,
                name=str(t.get("name", "label"))[:60])


def decode_label(code, template):
    t = template
    item = code[t["item_start"]:t["item_start"] + t["item_len"]]
    raw = int(code[t["value_start"]:t["value_start"] + t["value_len"]])
    if t["value_kind"] == "weight":
        value = raw * 10 ** (3 - t["decimals"])  # grams
        return {"item_code": item, "net_g": value}
    value = raw * 10 ** (2 - t["decimals"]) if t["decimals"] <= 2 else raw // 10
    return {"item_code": item, "price_piastres": value}


def classify(raw, profile=None, templates=(), pack_prefix="28"):
    """Return a dict describing the scan. Never raises for unknown content;
    raises ScanError only for damaged or unsafe input."""
    profile = profile or {}
    code = normalize(raw, profile)
    symbology = None
    if profile.get("aim_ids") and code.startswith("]") and len(code) > 3:
        symbology, code = code[:3], code[3:]
    lowered = code.lower()
    if "://" in lowered or lowered.startswith(("www.", "mailto:", "tel:", "wifi:")):
        return {"kind": "url", "code": code, "symbology": symbology}
    gs1_flag = symbology in ("]C1", "]d2", "]Q3", "]e0") or GS in code or code.startswith("(01)")
    if gs1_flag:
        fields = parse_gs1(code)
        out = {"kind": "gs1", "code": code, "symbology": symbology, "fields": fields}
        if "gtin" in fields:
            out["gtin"] = fields["gtin"]
        return out
    if code.isdigit():
        if code.startswith(pack_prefix) and len(code) == 13:
            if not valid_gs1(code):
                raise ScanError("bad_check_digit")
            return {"kind": "pack", "code": code, "pack_code": code}
        for t in templates:
            if len(code) == t["length"] and code.startswith(t["prefix"]):
                if not valid_gs1(code):
                    raise ScanError("bad_check_digit")
                out = {"kind": "label", "code": code, "template": t.get("name")}
                out.update(decode_label(code, t))
                return out
        if len(code) in (12, 13, 14):
            if not valid_gs1(code):
                raise ScanError("bad_check_digit")
            return {"kind": "gtin", "code": code, "length": len(code)}
        if len(code) == 8:
            if valid_gs1(code):
                return {"kind": "gtin", "code": code, "length": 8}
            upca = upce_to_upca(code)
            if upca:
                return {"kind": "gtin", "code": code, "length": 8, "upca": upca}
            raise ScanError("bad_check_digit")
    return {"kind": "code", "code": code, "symbology": symbology}


def gtin_variants(code, upca=None):
    """Representations of the same GTIN (used only when the shop enables the
    rule and confirms the link in a preview). An 8-digit code is expanded only
    when the caller classified it as UPC-E (pass `upca`); EAN-8 stays itself."""
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return []
    if len(code) == 8 and not upca:
        return []
    base = (upca or code).zfill(14)
    out = {base, base[1:], base[2:]} if base.startswith("00") else {base, base[1:]} if base.startswith("0") else {base}
    out.discard(code)
    return sorted(v for v in out if len(v) in (12, 13, 14))


def pack_code(prefix, number):
    body = prefix + str(number).zfill(12 - len(prefix))
    if len(body) != 12:
        raise ValueError("pack number too large for prefix")
    return body + gs1_check_digit(body)
