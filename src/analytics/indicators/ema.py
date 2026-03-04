"""EMA (Exponential Moving Average) индикатор."""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional


class EMACalculator:
    def __init__(self, periods: Optional[List[int]] = None):
        self.periods = periods or [9, 21, 50]

    def calculate(self, df: pd.DataFrame, period: int) -> pd.Series:
        return df["close"].ewm(span=period, adjust=False).mean()

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        current_price = float(df.iloc[-1]["close"])
        result: Dict[str, Any] = {"price": current_price}

        for period in self.periods:
            ema = self.calculate(df, period)
            val = ema.iloc[-1]
            result[f"ema{period}"] = float(val) if not pd.isna(val) else None

        ema50 = result.get("ema50")
        result["trend"] = "BULLISH" if ema50 and current_price > ema50 else (
            "BEARISH" if ema50 and current_price < ema50 else "NEUTRAL"
        )

        ema9 = result.get("ema9")
        ema21 = result.get("ema21")
        if ema9 is not None and ema21 is not None:
            result["ema_cross"] = "BULLISH" if ema9 > ema21 else "BEARISH"
        else:
            result["ema_cross"] = "NEUTRAL"

        return result
