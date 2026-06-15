"""VWAP — Volume Weighted Average Price."""
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


class VWAPCalculator:
    """
    VWAP = cumsum(typical_price × volume) / cumsum(volume)

    Цена > VWAP → покупатели доминируют (бычий контекст).
    Цена < VWAP → продавцы доминируют (медвежий контекст).
    Касание VWAP → потенциальная точка разворота.
    """

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < 10:
            return {"vwap": None, "vwap_signal": "NEUTRAL", "vwap_distance_pct": 0.0}

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        volumes = df["volume"].values.astype(float)

        typical = (highs + lows + closes) / 3.0
        cum_vol = np.cumsum(volumes)
        cum_tp_vol = np.cumsum(typical * volumes)

        vwap_arr = np.where(cum_vol > 0, cum_tp_vol / cum_vol, typical)
        vwap = float(vwap_arr[-1])
        cur_price = float(closes[-1])
        distance_pct = (cur_price - vwap) / vwap * 100 if vwap > 0 else 0.0

        if distance_pct > 1.0:
            signal = "BULLISH"
        elif distance_pct < -1.0:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        return {
            "vwap": round(vwap, 8),
            "vwap_signal": signal,
            "vwap_distance_pct": round(distance_pct, 2),
        }
