"""Average True Range (ATR) индикатор."""
import pandas as pd
import numpy as np
from typing import Optional


class ATRCalculator:
    def __init__(self, period: int = 14):
        self.period = period

    def calculate(self, df: pd.DataFrame) -> pd.Series:
        if len(df) < self.period + 1:
            return pd.Series([np.nan] * len(df), index=df.index)
        high = df["high"]
        low = df["low"]
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=self.period, adjust=False).mean()

    def get_current(self, df: pd.DataFrame) -> Optional[float]:
        atr = self.calculate(df)
        val = atr.iloc[-1]
        return float(val) if not pd.isna(val) else None
