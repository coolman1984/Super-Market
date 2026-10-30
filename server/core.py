"""Shared rules: errors, integer money/weight arithmetic, shop clock.

Money is integer piastres, weight integer grams, counted stock integer units.
No float ever enters a posted amount; JSON numbers from clients must be ints.
"""

import datetime as dt

MAX_INT = 2**63 - 1


class ShopError(ValueError):
    """Business error with a stable code; the UI translates the code."""

    status = 400

    def __init__(self, code, detail=None, status=None):
        super().__init__(code)
        self.code = code
        self.detail = detail or {}
        if status:
            self.status = status


class Forbidden(ShopError):
    status = 403

    def __init__(self, code="forbidden", detail=None):
        super().__init__(code, detail)


class Conflict(ShopError):
    status = 409


def integer(value, minimum=0, code="bad_number", maximum=MAX_INT):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ShopError(code, {"min": minimum})
    return value


def text(value, code="required", max_len=200, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise ShopError(code)
    return value.strip()


def round_half_up_div(numerator, denominator):
    """Integer division rounding half away from zero (positive amounts)."""
    if denominator <= 0:
        raise ShopError("bad_divisor")
    sign = -1 if numerator < 0 else 1
    q, r = divmod(abs(numerator), denominator)
    if 2 * r >= denominator:
        q += 1
    return sign * q


def weighed_amount(price_per_kg, grams):
    """Price of a weighed item: per-kilo piastres x grams / 1000, half-up."""
    return round_half_up_div(integer(price_per_kg) * integer(grams, 1), 1000)


def allocate(total, weights):
    """Split integer total across non-negative integer weights, largest
    remainder, ties by position. Sum of parts equals total exactly."""
    integer(abs(total))
    if not weights:
        return []
    base = sum(weights)
    if base <= 0:
        parts = [0] * len(weights)
        parts[0] = total
        return parts
    sign = -1 if total < 0 else 1
    absolute = abs(total)
    raw = [absolute * w // base for w in weights]
    rema = [(absolute * w % base, -i) for i, w in enumerate(weights)]
    left = absolute - sum(raw)
    for _, neg_index in sorted(rema, reverse=True)[:left]:
        raw[-neg_index] += 1
    return [sign * p for p in raw]


def cumulative_share(total, total_qty, previous_qty, previous_amount, qty):
    """Share of `total` for `qty` after `previous_qty` were already taken;
    exact at the last portion, never more than total overall."""
    if previous_qty + qty > total_qty:
        raise ShopError("exceeds_original")
    return total * (previous_qty + qty) // total_qty - previous_amount


# ---------- clock ----------

try:  # zoneinfo has no database on a bare Windows Python; fall back to PC time
    from zoneinfo import ZoneInfo
    SHOP_TZ = ZoneInfo("Africa/Cairo")
except Exception:  # pragma: no cover - depends on host tz data
    SHOP_TZ = None

_clock_override = None


def set_clock(value):
    """Tests only: freeze the shop clock to an aware datetime (or None)."""
    global _clock_override
    _clock_override = value


def now():
    if _clock_override is not None:
        return _clock_override
    if SHOP_TZ is not None:
        return dt.datetime.now(SHOP_TZ)
    return dt.datetime.now().astimezone()


def now_iso():
    return now().isoformat(timespec="seconds")


def today():
    return now().date().isoformat()


def iso_date(value, code="bad_date", optional=False):
    if value in (None, "") and optional:
        return None
    if not isinstance(value, str):
        raise ShopError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ShopError(code) from exc
    if parsed.isoformat() != value:
        raise ShopError(code)
    return value


def add_days(date_text, days):
    return (dt.date.fromisoformat(date_text) + dt.timedelta(days=days)).isoformat()
