"""Number formatting that matches the workspace's JavaScript (`toFixed`, `Math.round`) so reports agree byte for byte."""
from __future__ import annotations

import math
from decimal import ROUND_HALF_DOWN, ROUND_HALF_UP, Decimal


def to_fixed(x: float, digits: int = 2) -> str:
    """JavaScript Number.prototype.toFixed: exact binary value, ties go to the larger n (away from zero for positives, toward zero for negatives)."""
    if not math.isfinite(x):
        return str(x)
    q = Decimal(1).scaleb(-digits)
    d = Decimal(x)
    rounded = d.quantize(q, rounding=ROUND_HALF_UP if d >= 0 else ROUND_HALF_DOWN)
    text = f"{rounded:f}"
    if text.startswith("-") and Decimal(text) == 0:
        text = text[1:]  # JS prints "0.00" for -0.001, not "-0.00"
    return text


def js_round(x: float) -> int:
    """JavaScript Math.round: halves round toward +infinity."""
    return int(math.floor(x + 0.5))


def pct(v: float) -> str:
    return f"{js_round(v * 100)}%"


def signed(x: float, digits: int = 2) -> str:
    s = to_fixed(x, digits)
    return s if s.startswith("-") else f"+{s}"


def plain(x: float) -> str:
    """Limits in prose: 0.3 not 0.30, 1 not 1.0 (matches the workspace's `${limits.threshold}`)."""
    if float(x).is_integer():
        return str(int(x))
    return repr(float(x))
