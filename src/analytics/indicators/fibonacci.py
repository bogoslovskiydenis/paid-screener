"""Fibonacci Retracement — уровни коррекции для определения зон входа."""
import numpy as np
import pandas as pd
from typing import Dict, Any, List


FIB_RATIOS = [0.236, 0.382, 0.500, 0.618, 0.786]
GOLDEN_ZONE = (0.618, 0.786)


def _find_swings(df: pd.DataFrame, left: int = 5, right: int = 3):
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)

    swing_highs: List[Dict[str, Any]] = []
    swing_lows: List[Dict[str, Any]] = []

    for i in range(left, len(df) - right):
        if highs[i] == max(highs[i - left : i + right + 1]):
            swing_highs.append({"price": float(highs[i]), "index": i})
        if lows[i] == min(lows[i - left : i + right + 1]):
            swing_lows.append({"price": float(lows[i]), "index": i})

    return swing_highs, swing_lows


def analyze(df: pd.DataFrame, lookback: int = 80) -> Dict[str, Any]:
    """
    Fibonacci retracement от последнего значимого swing.

    Бычий тренд (swing_low → swing_high):
      Уровни отката = swing_high - range * ratio
      Golden zone (0.618-0.786) — лучшая зона входа в лонг на откате.

    Медвежий тренд (swing_high → swing_low):
      Уровни отката = swing_low + range * ratio
    """
    empty = {"levels": [], "golden_zone": None, "in_golden_zone": False,
             "trend": "UNKNOWN", "swing_high": None, "swing_low": None,
             "nearest_level": None}

    if len(df) < 30:
        return empty

    window = df.iloc[-lookback:] if len(df) > lookback else df
    swing_highs, swing_lows = _find_swings(window)

    if not swing_highs or not swing_lows:
        return empty

    last_high = max(swing_highs, key=lambda x: x["index"])
    last_low = max(swing_lows, key=lambda x: x["index"])

    cur_price = float(df.iloc[-1]["close"])

    if last_low["index"] < last_high["index"]:
        trend = "BULLISH"
        sh, sl = last_high["price"], last_low["price"]
    else:
        trend = "BEARISH"
        sh, sl = last_high["price"], last_low["price"]

    price_range = sh - sl
    if price_range <= 0:
        return empty

    levels = []
    for ratio in FIB_RATIOS:
        if trend == "BULLISH":
            level = sh - price_range * ratio
        else:
            level = sl + price_range * ratio
        levels.append({
            "ratio": ratio,
            "label": f"{ratio * 100:.1f}%",
            "price": round(level, 8),
        })

    if trend == "BULLISH":
        gz_low = sh - price_range * GOLDEN_ZONE[1]
        gz_high = sh - price_range * GOLDEN_ZONE[0]
    else:
        gz_low = sl + price_range * GOLDEN_ZONE[0]
        gz_high = sl + price_range * GOLDEN_ZONE[1]

    golden_zone = {"low": round(gz_low, 8), "high": round(gz_high, 8)}
    in_golden_zone = gz_low <= cur_price <= gz_high

    nearest = min(levels, key=lambda x: abs(x["price"] - cur_price))
    dist_pct = abs(nearest["price"] - cur_price) / cur_price * 100 if cur_price > 0 else 0

    return {
        "levels": levels,
        "golden_zone": golden_zone,
        "in_golden_zone": in_golden_zone,
        "trend": trend,
        "swing_high": round(sh, 8),
        "swing_low": round(sl, 8),
        "nearest_level": {**nearest, "distance_pct": round(dist_pct, 2)},
    }
