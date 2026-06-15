"""ICT-концепции: Fair Value Gap (FVG) и Liquidity Sweep."""
import numpy as np
import pandas as pd
from typing import List, Dict, Any


def detect_fvg(
    df: pd.DataFrame,
    min_gap_pct: float = 0.002,
    max_lookback: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fair Value Gap — ценовой дисбаланс из 3 свечей.

    Бычий FVG:   low[i] > high[i-2]  →  гэп между high[i-2] и low[i]
    Медвежий FVG: high[i] < low[i-2]  →  гэп между high[i] и low[i-2]

    Незакрытый (filled=False) бычий FVG ниже цены — поддержка.
    Незакрытый бычий FVG выше цены — ближайшая цель (магнит).

    Returns: список гэпов, отсортированных от новых к старым.
    """
    if len(df) < 5:
        return []

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)

    n = len(df)
    start = max(2, n - max_lookback)
    fvgs: List[Dict[str, Any]] = []

    for i in range(start, n):
        # --- Бычий FVG ---
        gap_bot = highs[i - 2]   # нижняя граница гэпа = high свечи i-2
        gap_top = lows[i]         # верхняя граница гэпа = low свечи i
        if gap_top > gap_bot and gap_bot > 0:
            gap_pct = (gap_top - gap_bot) / gap_bot
            if gap_pct >= min_gap_pct:
                filled = any(
                    lows[j] < gap_top and highs[j] > gap_bot
                    for j in range(i + 1, n)
                )
                fvgs.append({
                    "type": "BULLISH",
                    "gap_low": float(gap_bot),
                    "gap_high": float(gap_top),
                    "gap_pct": round(gap_pct * 100, 2),
                    "filled": filled,
                    "candles_ago": n - 1 - i,
                })

        # --- Медвежий FVG ---
        gap_top_b = lows[i - 2]   # верхняя граница гэпа = low свечи i-2
        gap_bot_b = highs[i]       # нижняя граница гэпа = high свечи i
        if gap_top_b > gap_bot_b and gap_bot_b > 0:
            gap_pct = (gap_top_b - gap_bot_b) / gap_bot_b
            if gap_pct >= min_gap_pct:
                filled = any(
                    lows[j] < gap_top_b and highs[j] > gap_bot_b
                    for j in range(i + 1, n)
                )
                fvgs.append({
                    "type": "BEARISH",
                    "gap_low": float(gap_bot_b),
                    "gap_high": float(gap_top_b),
                    "gap_pct": round(gap_pct * 100, 2),
                    "filled": filled,
                    "candles_ago": n - 1 - i,
                })

    fvgs.sort(key=lambda x: x["candles_ago"])
    return fvgs


def detect_liquidity_sweep(
    df: pd.DataFrame,
    lookback: int = 30,
    recent_candles: int = 5,
    min_wick_pct: float = 0.05,
) -> List[Dict[str, Any]]:
    """
    Liquidity Sweep — цена уходит за swing high/low (собирает стопы),
    но закрывается обратно. Признак институционального разворота.

    Бычий свип:  wick < swing_low, close > swing_low  →  разворот вверх
    Медвежий свип: wick > swing_high, close < swing_high →  разворот вниз

    Parameters
    ----------
    lookback       : окно поиска swing-уровней (в свечах до текущей)
    recent_candles : сколько последних свечей проверяем на свип
    min_wick_pct   : минимальный выход за уровень в %, чтобы считать свипом
    """
    n = len(df)
    if n < lookback + recent_candles:
        return []

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)
    opens = df["open"].values.astype(float)

    check_start = n - recent_candles
    sweeps: List[Dict[str, Any]] = []

    for i in range(check_start, n):
        look_start = max(0, i - lookback)
        look_end = i  # не включаем текущую свечу в поиск уровней

        if look_end <= look_start:
            continue

        swing_low = float(np.min(lows[look_start:look_end]))
        swing_high = float(np.max(highs[look_start:look_end]))

        cur_low = lows[i]
        cur_high = highs[i]
        cur_close = closes[i]

        # Бычий свип: wick ниже swing_low, close выше → стопы сметены, разворот
        if cur_low < swing_low and cur_close > swing_low and swing_low > 0:
            wick_pct = (swing_low - cur_low) / swing_low * 100
            if wick_pct >= min_wick_pct:
                sweeps.append({
                    "type": "BULLISH",
                    "swept_level": round(float(swing_low), 8),
                    "wick_low": round(float(cur_low), 8),
                    "wick_pct": round(wick_pct, 2),
                    "close": round(float(cur_close), 8),
                    "candles_ago": n - 1 - i,
                })

        # Медвежий свип: wick выше swing_high, close ниже → стопы сметены, разворот вниз
        if cur_high > swing_high and cur_close < swing_high and swing_high > 0:
            wick_pct = (cur_high - swing_high) / swing_high * 100
            if wick_pct >= min_wick_pct:
                sweeps.append({
                    "type": "BEARISH",
                    "swept_level": round(float(swing_high), 8),
                    "wick_high": round(float(cur_high), 8),
                    "wick_pct": round(wick_pct, 2),
                    "close": round(float(cur_close), 8),
                    "candles_ago": n - 1 - i,
                })

    sweeps.sort(key=lambda x: x["candles_ago"])
    return sweeps
