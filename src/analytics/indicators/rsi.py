"""Расчет индикатора RSI (Relative Strength Index)."""
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional

try:
    from ...utils.logger import setup_logger
except ImportError:
    try:
        from utils.logger import setup_logger
    except ImportError:
        import logging
        def setup_logger(name):
            return logging.getLogger(name)

logger = setup_logger(__name__)


class RSICalculator:
    """Калькулятор RSI индикатора."""
    
    def __init__(self, period: int = 14):
        self.period = period
    
    def calculate(self, df: pd.DataFrame) -> pd.Series:
        """Рассчитывает RSI для DataFrame."""
        if len(df) < self.period + 1:
            return pd.Series([np.nan] * len(df), index=df.index)
        
        close = df["close"]
        delta = close.diff()
        
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def get_signal(self, rsi_value: float) -> Dict[str, Any]:
        """Определяет торговый сигнал на основе RSI."""
        if pd.isna(rsi_value):
            return {
                "signal": "NEUTRAL",
                "strength": 0.0,
                "zone": "UNKNOWN"
            }
        
        if rsi_value < 30:
            return {
                "signal": "BUY",
                "strength": min(1.0, (30 - rsi_value) / 20),
                "zone": "OVERSOLD",
                "rsi_value": float(rsi_value)
            }
        elif rsi_value > 70:
            return {
                "signal": "SELL",
                "strength": min(1.0, (rsi_value - 70) / 20),
                "zone": "OVERBOUGHT",
                "rsi_value": float(rsi_value)
            }
        elif rsi_value < 40:
            return {
                "signal": "BUY",
                "strength": (40 - rsi_value) / 20 * 0.5,
                "zone": "NEAR_OVERSOLD",
                "rsi_value": float(rsi_value)
            }
        elif rsi_value > 60:
            return {
                "signal": "SELL",
                "strength": (rsi_value - 60) / 20 * 0.5,
                "zone": "NEAR_OVERBOUGHT",
                "rsi_value": float(rsi_value)
            }
        else:
            return {
                "signal": "NEUTRAL",
                "strength": 0.0,
                "zone": "NEUTRAL",
                "rsi_value": float(rsi_value)
            }
    
    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Полный анализ RSI для DataFrame."""
        rsi_series = self.calculate(df)
        current_rsi = rsi_series.iloc[-1] if not rsi_series.empty else np.nan
        
        signal_info = self.get_signal(current_rsi)
        
        return {
            "rsi": float(current_rsi) if not pd.isna(current_rsi) else None,
            "rsi_signal": signal_info["signal"],
            "rsi_zone": signal_info["zone"],
            "rsi_strength": signal_info["strength"],
            "rsi_period": self.period
        }

