"""MACD индикатор."""
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional


class MACDCalculator:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal_period = signal

    def _compute(self, df: pd.DataFrame) -> Dict[str, pd.Series]:
        close = df["close"]
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()
        return {"macd": macd_line, "signal": signal_line, "histogram": macd_line - signal_line}

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        min_bars = self.slow + self.signal_period
        if len(df) < min_bars:
            return {
                "macd_signal": "NEUTRAL",
                "bullish_cross": False,
                "bearish_cross": False,
                "divergence": None,
            }

        data = self._compute(df)
        macd_line = data["macd"]
        signal_line = data["signal"]
        histogram = data["histogram"]

        curr_macd = float(macd_line.iloc[-1])
        curr_sig = float(signal_line.iloc[-1])
        curr_hist = float(histogram.iloc[-1])
        prev_macd = float(macd_line.iloc[-2])
        prev_sig = float(signal_line.iloc[-2])
        prev_hist = float(histogram.iloc[-2])

        bullish_cross = prev_macd < prev_sig and curr_macd > curr_sig
        bearish_cross = prev_macd > prev_sig and curr_macd < curr_sig

        if bullish_cross:
            macd_signal = "BUY"
        elif bearish_cross:
            macd_signal = "SELL"
        elif curr_macd > curr_sig and curr_hist > prev_hist:
            macd_signal = "BUY"
        elif curr_macd < curr_sig and curr_hist < prev_hist:
            macd_signal = "SELL"
        else:
            macd_signal = "NEUTRAL"

        divergence = self._detect_divergence(df, macd_line)

        return {
            "macd": curr_macd,
            "macd_signal_line": curr_sig,
            "histogram": curr_hist,
            "macd_signal": macd_signal,
            "bullish_cross": bullish_cross,
            "bearish_cross": bearish_cross,
            "divergence": divergence,
        }

    def _detect_divergence(self, df: pd.DataFrame, macd_line: pd.Series) -> Optional[str]:
        """Дивергенция: цена vs MACD на последних 20 свечах."""
        if len(df) < 20:
            return None
        prices = df["close"].tail(20).values
        macd_vals = macd_line.tail(20).values

        price_high_idx = int(np.argmax(prices))
        price_low_idx = int(np.argmin(prices))

        # Медвежья дивергенция: цена на максимуме, MACD ниже предыдущего пика
        if price_high_idx == len(prices) - 1 and price_high_idx > 0:
            prev_max_macd = float(np.max(macd_vals[:price_high_idx]))
            if macd_vals[-1] < prev_max_macd:
                return "BEARISH"

        # Бычья дивергенция: цена на минимуме, MACD выше предыдущего дна
        if price_low_idx == len(prices) - 1 and price_low_idx > 0:
            prev_min_macd = float(np.min(macd_vals[:price_low_idx]))
            if macd_vals[-1] > prev_min_macd:
                return "BULLISH"

        return None
