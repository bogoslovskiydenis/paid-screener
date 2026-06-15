"""Форматирование цен для USDT и мелких кросс-пар."""
import math
from typing import Any


def fmt_price(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    av = abs(v)
    if av == 0:
        return "0"
    if av >= 100:
        return f"{v:.2f}"
    if av >= 1:
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"
    if av >= 0.01:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"
    nd = min(12, max(6, int(math.ceil(-math.log10(av))) + 3))
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s if s else "0"
