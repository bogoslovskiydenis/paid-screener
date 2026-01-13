"""Анализ свечных паттернов."""
import pandas as pd
from typing import Optional, List, Dict, Any

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


class CandlestickPatternAnalyzer:
    """Анализатор свечных паттернов."""
    
    def __init__(self):
        self.patterns = []
    
    def analyze(self, df: pd.DataFrame) -> Optional[str]:
        """Анализирует последние свечи и определяет паттерн."""
        if len(df) < 3:
            return None
        
        patterns = [
            self._detect_hammer,
            self._detect_engulfing,
            self._detect_doji,
            self._detect_shooting_star,
            self._detect_evening_star,
            self._detect_morning_star,
        ]
        
        for pattern_func in patterns:
            pattern = pattern_func(df)
            if pattern:
                return pattern
        
        return None
    
    def _detect_hammer(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Молот."""
        if len(df) < 1:
            return None
        
        candle = df.iloc[-1]
        body = abs(candle["close"] - candle["open"])
        lower_shadow = min(candle["open"], candle["close"]) - candle["low"]
        upper_shadow = candle["high"] - max(candle["open"], candle["close"])
        
        if (lower_shadow > 2 * body and 
            upper_shadow < 0.1 * body and
            body > 0):
            return "Hammer"
        
        return None
    
    def _detect_engulfing(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Поглощение."""
        if len(df) < 2:
            return None
        
        prev = df.iloc[-2]
        curr = df.iloc[-1]
        
        prev_body = abs(prev["close"] - prev["open"])
        curr_body = abs(curr["close"] - curr["open"])
        
        prev_bullish = prev["close"] > prev["open"]
        curr_bullish = curr["close"] > curr["open"]
        
        if (curr_body > 1.1 * prev_body and
            ((prev_bullish and not curr_bullish and
              curr["open"] > prev["close"] and curr["close"] < prev["open"]) or
             (not prev_bullish and curr_bullish and
              curr["open"] < prev["close"] and curr["close"] > prev["open"]))):
            return "Bullish Engulfing" if curr_bullish else "Bearish Engulfing"
        
        return None
    
    def _detect_doji(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Доджи."""
        if len(df) < 1:
            return None
        
        candle = df.iloc[-1]
        body = abs(candle["close"] - candle["open"])
        total_range = candle["high"] - candle["low"]
        
        if total_range > 0 and body / total_range < 0.1:
            return "Doji"
        
        return None
    
    def _detect_shooting_star(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Падающая звезда."""
        if len(df) < 1:
            return None
        
        candle = df.iloc[-1]
        body = abs(candle["close"] - candle["open"])
        upper_shadow = candle["high"] - max(candle["open"], candle["close"])
        lower_shadow = min(candle["open"], candle["close"]) - candle["low"]
        
        if (upper_shadow > 2 * body and
            lower_shadow < 0.1 * body and
            candle["close"] < candle["open"]):
            return "Shooting Star"
        
        return None
    
    def _detect_evening_star(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Вечерняя звезда."""
        if len(df) < 3:
            return None
        
        first = df.iloc[-3]
        second = df.iloc[-2]
        third = df.iloc[-1]
        
        if (first["close"] > first["open"] and
            abs(second["close"] - second["open"]) < abs(first["close"] - first["open"]) * 0.3 and
            third["close"] < third["open"] and
            third["close"] < (first["open"] + first["close"]) / 2):
            return "Evening Star"
        
        return None
    
    def _detect_morning_star(self, df: pd.DataFrame) -> Optional[str]:
        """Определяет паттерн Утренняя звезда."""
        if len(df) < 3:
            return None
        
        first = df.iloc[-3]
        second = df.iloc[-2]
        third = df.iloc[-1]
        
        if (first["close"] < first["open"] and
            abs(second["close"] - second["open"]) < abs(first["close"] - first["open"]) * 0.3 and
            third["close"] > third["open"] and
            third["close"] > (first["open"] + first["close"]) / 2):
            return "Morning Star"
        
        return None

