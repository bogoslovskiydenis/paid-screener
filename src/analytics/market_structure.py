"""Market Structure: BOS (Break of Structure) и CHoCH (Change of Character)."""
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional


def find_swing_points(
    df: pd.DataFrame,
    left: int = 3,
    right: int = 3,
) -> List[Dict[str, Any]]:
    """
    Swing High: high[i] > всех high в окне [i-left, i+right].
    Swing Low:  low[i]  < всех low  в окне [i-left, i+right].
    """
    if len(df) < left + right + 1:
        return []

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    swings: List[Dict[str, Any]] = []

    for i in range(left, len(df) - right):
        window_highs = highs[i - left:i + right + 1]
        if highs[i] == np.max(window_highs):
            swings.append({
                "type": "HIGH",
                "price": float(highs[i]),
                "index": i,
                "candles_ago": len(df) - 1 - i,
            })

        window_lows = lows[i - left:i + right + 1]
        if lows[i] == np.min(window_lows):
            swings.append({
                "type": "LOW",
                "price": float(lows[i]),
                "index": i,
                "candles_ago": len(df) - 1 - i,
            })

    return swings


def detect_structure(
    df: pd.DataFrame,
    lookback: int = 50,
    swing_left: int = 3,
    swing_right: int = 3,
) -> Dict[str, Any]:
    """
    Определяет текущую рыночную структуру и находит BOS/CHoCH.

    Бычья структура: Higher Highs + Higher Lows.
    Медвежья: Lower Highs + Lower Lows.

    BOS (Break of Structure) — пробой в направлении тренда:
      - Бычий BOS: цена пробивает предыдущий swing high → тренд продолжается.
      - Медвежий BOS: цена пробивает предыдущий swing low → тренд продолжается.

    CHoCH (Change of Character) — пробой ПРОТИВ тренда:
      - Бычий CHoCH: в нисходящем тренде цена пробивает swing high → разворот вверх.
      - Медвежий CHoCH: в восходящем тренде цена пробивает swing low → разворот вниз.
    """
    if len(df) < lookback:
        return {
            "structure": "UNKNOWN",
            "bos": None,
            "choch": None,
            "swing_highs": [],
            "swing_lows": [],
            "trend_strength": 0.0,
        }

    window = df.iloc[-lookback:]
    swings = find_swing_points(window, left=swing_left, right=swing_right)

    swing_highs = [s for s in swings if s["type"] == "HIGH"]
    swing_lows = [s for s in swings if s["type"] == "LOW"]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {
            "structure": "UNKNOWN",
            "bos": None,
            "choch": None,
            "swing_highs": swing_highs[-3:],
            "swing_lows": swing_lows[-3:],
            "trend_strength": 0.0,
        }

    hh_count = sum(
        1 for i in range(1, len(swing_highs))
        if swing_highs[i]["price"] > swing_highs[i - 1]["price"]
    )
    hl_count = sum(
        1 for i in range(1, len(swing_lows))
        if swing_lows[i]["price"] > swing_lows[i - 1]["price"]
    )
    lh_count = sum(
        1 for i in range(1, len(swing_highs))
        if swing_highs[i]["price"] < swing_highs[i - 1]["price"]
    )
    ll_count = sum(
        1 for i in range(1, len(swing_lows))
        if swing_lows[i]["price"] < swing_lows[i - 1]["price"]
    )

    n_highs = max(len(swing_highs) - 1, 1)
    n_lows = max(len(swing_lows) - 1, 1)

    bull_score = (hh_count / n_highs + hl_count / n_lows) / 2
    bear_score = (lh_count / n_highs + ll_count / n_lows) / 2

    if bull_score > bear_score and bull_score > 0.5:
        structure = "BULLISH"
        trend_strength = bull_score
    elif bear_score > bull_score and bear_score > 0.5:
        structure = "BEARISH"
        trend_strength = bear_score
    else:
        structure = "RANGING"
        trend_strength = 0.0

    cur_price = float(df["close"].values[-1])
    last_sh = swing_highs[-1]
    last_sl = swing_lows[-1]
    prev_sh = swing_highs[-2] if len(swing_highs) >= 2 else None
    prev_sl = swing_lows[-2] if len(swing_lows) >= 2 else None

    bos: Optional[Dict[str, Any]] = None
    choch: Optional[Dict[str, Any]] = None

    if structure == "BULLISH" and prev_sh:
        if cur_price > last_sh["price"]:
            bos = {
                "type": "BULLISH",
                "broken_level": last_sh["price"],
                "description": f"BOS: пробой swing high {last_sh['price']:.4f}",
            }
    elif structure == "BEARISH" and prev_sl:
        if cur_price < last_sl["price"]:
            bos = {
                "type": "BEARISH",
                "broken_level": last_sl["price"],
                "description": f"BOS: пробой swing low {last_sl['price']:.4f}",
            }

    if structure == "BEARISH" and prev_sh:
        if cur_price > last_sh["price"]:
            choch = {
                "type": "BULLISH",
                "broken_level": last_sh["price"],
                "description": f"CHoCH: пробой swing high в даунтренде → разворот",
            }
    elif structure == "BULLISH" and prev_sl:
        if cur_price < last_sl["price"]:
            choch = {
                "type": "BEARISH",
                "broken_level": last_sl["price"],
                "description": f"CHoCH: пробой swing low в аптренде → разворот",
            }

    return {
        "structure": structure,
        "trend_strength": round(trend_strength, 2),
        "bos": bos,
        "choch": choch,
        "swing_highs": swing_highs[-3:],
        "swing_lows": swing_lows[-3:],
    }


def detect_order_blocks(
    df: pd.DataFrame,
    lookback: int = 30,
    min_move_pct: float = 0.02,
) -> List[Dict[str, Any]]:
    """
    Order Block — последняя противоположная свеча перед импульсным движением.

    Бычий OB: последняя медвежья свеча перед сильным бычьим импульсом.
    Медвежий OB: последняя бычья свеча перед сильным медвежьим импульсом.

    Цена возвращается к OB → институциональная зона входа.
    """
    if len(df) < lookback + 5:
        return []

    opens = df["open"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)
    n = len(df)
    start = max(1, n - lookback)

    blocks: List[Dict[str, Any]] = []

    for i in range(start, n - 2):
        move = (closes[i + 1] - closes[i]) / closes[i] if closes[i] > 0 else 0
        move2 = (closes[i + 2] - closes[i]) / closes[i] if closes[i] > 0 else 0

        # Бычий OB: медвежья свеча → сильный бычий импульс (2+ свечи подряд)
        if closes[i] < opens[i] and move > min_move_pct and move2 > min_move_pct:
            mitigated = any(lows[j] <= opens[i] for j in range(i + 2, n))
            blocks.append({
                "type": "BULLISH",
                "ob_high": float(opens[i]),
                "ob_low": float(closes[i]),
                "candles_ago": n - 1 - i,
                "mitigated": mitigated,
            })

        # Медвежий OB: бычья свеча → сильный медвежий импульс
        if closes[i] > opens[i] and move < -min_move_pct and move2 < -min_move_pct:
            mitigated = any(highs[j] >= opens[i] for j in range(i + 2, n))
            blocks.append({
                "type": "BEARISH",
                "ob_high": float(closes[i]),
                "ob_low": float(opens[i]),
                "candles_ago": n - 1 - i,
                "mitigated": mitigated,
            })

    blocks.sort(key=lambda x: x["candles_ago"])
    return blocks


def detect_liquidity_grab(
    df: pd.DataFrame,
    lookback: int = 30,
    swing_left: int = 3,
    swing_right: int = 3,
) -> Optional[Dict[str, Any]]:
    """
    Liquidity Grab / Stop Hunt — свип ликвидности за swing low/high.

    Бычий: тень прокалывает swing low, но свеча ЗАКРЫВАЕТСЯ выше →
    институциональный сбор стопов, сильный сигнал на лонг.

    Медвежий: тень прокалывает swing high, но свеча ЗАКРЫВАЕТСЯ ниже →
    сбор стопов лонгов, сигнал на шорт.
    """
    if len(df) < lookback:
        return None

    swings = find_swing_points(df.iloc[:-3], left=swing_left, right=swing_right)
    if not swings:
        return None

    swing_lows = [s for s in swings if s["type"] == "LOW"]
    swing_highs = [s for s in swings if s["type"] == "HIGH"]

    for i in range(-3, 0):
        candle = df.iloc[i]
        low = float(candle["low"])
        high = float(candle["high"])
        close = float(candle["close"])
        open_ = float(candle["open"])

        for sl in swing_lows[-5:]:
            if low < sl["price"] and close > sl["price"] and close > open_:
                return {
                    "type": "BULLISH",
                    "grabbed_level": sl["price"],
                    "low_wick": low,
                    "close": close,
                    "candles_ago": abs(i),
                    "description": f"Liquidity grab: свип swing low {sl['price']:.4f}",
                }

        for sh in swing_highs[-5:]:
            if high > sh["price"] and close < sh["price"] and close < open_:
                return {
                    "type": "BEARISH",
                    "grabbed_level": sh["price"],
                    "high_wick": high,
                    "close": close,
                    "candles_ago": abs(i),
                    "description": f"Liquidity grab: свип swing high {sh['price']:.4f}",
                }

    return None
