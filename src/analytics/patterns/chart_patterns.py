"""Распознавание графических паттернов."""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any, List

try:
    from scipy.signal import find_peaks
except ImportError:
    def find_peaks(*args, **kwargs):
        return ([], {})

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


class ChartPatternDetector:
    """Детектор графических паттернов."""
    
    def __init__(self, min_pattern_length: int = 20, price_tolerance: float = 0.01):
        self.min_pattern_length = min_pattern_length
        self.price_tolerance = price_tolerance
    
    def detect_all(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Обнаруживает все возможные паттерны."""
        patterns = []
        
        double_top = self.detect_double_top(df)
        if double_top:
            patterns.append(double_top)
        
        double_bottom = self.detect_double_bottom(df)
        if double_bottom:
            patterns.append(double_bottom)
        
        triangle = self.detect_triangle(df)
        if triangle:
            patterns.append(triangle)
        
        flag = self.detect_flag(df)
        if flag:
            patterns.append(flag)
        
        pennant = self.detect_pennant(df)
        if pennant:
            patterns.append(pennant)
        
        wedge = self.detect_wedge(df)
        if wedge:
            patterns.append(wedge)
        
        rectangle = self.detect_rectangle(df)
        if rectangle:
            patterns.append(rectangle)
        
        return patterns
    
    def detect_double_top(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает двойную вершину (медвежий паттерн)."""
        if len(df) < self.min_pattern_length:
            return None
        
        highs = df["high"].values
        peaks, properties = find_peaks(
            highs,
            distance=self.min_pattern_length // 3,
            prominence=np.std(highs) * 0.3
        )
        
        if len(peaks) < 2:
            return None
        
        for i in range(len(peaks) - 1):
            peak1_idx = peaks[i]
            peak2_idx = peaks[i + 1]
            
            peak1_price = highs[peak1_idx]
            peak2_price = highs[peak2_idx]
            
            if abs(peak1_price - peak2_price) / max(peak1_price, peak2_price) < self.price_tolerance:
                trough_idx = df.iloc[peak1_idx:peak2_idx]["low"].idxmin()
                trough_price = df.iloc[trough_idx]["low"]
                
                neckline = trough_price
                target = neckline - (peak1_price - neckline)
                
                completion = self._calculate_completion(df, peak1_price, neckline, is_resistance=True)
                volume_confirmation = self._check_volume(df, peak1_idx, peak2_idx)
                
                return {
                    "pattern_type": "DOUBLE_TOP",
                    "pattern_direction": "BEARISH",
                    "peak1_price": float(peak1_price),
                    "peak2_price": float(peak2_price),
                    "neckline": float(neckline),
                    "target_price": float(target),
                    "completion_percentage": completion,
                    "volume_confirmation": volume_confirmation
                }
        
        return None
    
    def detect_double_bottom(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает двойное дно (бычий паттерн)."""
        if len(df) < self.min_pattern_length:
            return None
        
        lows = df["low"].values
        peaks, properties = find_peaks(
            -lows,
            distance=self.min_pattern_length // 3,
            prominence=np.std(lows) * 0.3
        )
        
        if len(peaks) < 2:
            return None
        
        for i in range(len(peaks) - 1):
            bottom1_idx = peaks[i]
            bottom2_idx = peaks[i + 1]
            
            bottom1_price = lows[bottom1_idx]
            bottom2_price = lows[bottom2_idx]
            
            if abs(bottom1_price - bottom2_price) / max(bottom1_price, bottom2_price) < self.price_tolerance:
                peak_idx = df.iloc[bottom1_idx:bottom2_idx]["high"].idxmax()
                peak_price = df.iloc[peak_idx]["high"]
                
                neckline = peak_price
                target = neckline + (neckline - bottom1_price)
                
                completion = self._calculate_completion(df, bottom1_price, neckline, is_resistance=False)
                volume_confirmation = self._check_volume(df, bottom1_idx, bottom2_idx)
                
                return {
                    "pattern_type": "DOUBLE_BOTTOM",
                    "pattern_direction": "BULLISH",
                    "bottom1_price": float(bottom1_price),
                    "bottom2_price": float(bottom2_price),
                    "neckline": float(neckline),
                    "target_price": float(target),
                    "completion_percentage": completion,
                    "volume_confirmation": volume_confirmation
                }
        
        return None
    
    def detect_triangle(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает треугольник (восходящий, нисходящий, симметричный)."""
        if len(df) < self.min_pattern_length:
            return None
        
        recent_df = df.tail(self.min_pattern_length)
        highs = recent_df["high"].values
        lows = recent_df["low"].values
        
        high_trend = np.polyfit(range(len(highs)), highs, 1)[0]
        low_trend = np.polyfit(range(len(lows)), lows, 1)[0]
        
        high_volatility = np.std(highs)
        low_volatility = np.std(lows)
        
        if abs(high_trend) < high_volatility * 0.1 and abs(low_trend) < low_volatility * 0.1:
            return None
        
        if high_trend < 0 and low_trend > 0:
            pattern_type = "SYMMETRIC_TRIANGLE"
            direction = "NEUTRAL"
        elif high_trend < 0 and low_trend < 0:
            pattern_type = "DESCENDING_TRIANGLE"
            direction = "BEARISH"
        elif high_trend > 0 and low_trend > 0:
            pattern_type = "ASCENDING_TRIANGLE"
            direction = "BULLISH"
        else:
            return None
        
        apex_price = (highs[-1] + lows[-1]) / 2
        base_width = len(recent_df)
        
        return {
            "pattern_type": pattern_type,
            "pattern_direction": direction,
            "apex_price": float(apex_price),
            "base_width": base_width,
            "completion_percentage": 0.5,
            "volume_confirmation": self._check_volume_trend(recent_df)
        }
    
    def detect_flag(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает флаг (продолжение тренда)."""
        if len(df) < self.min_pattern_length:
            return None
        
        pole_length = self.min_pattern_length // 2
        flag_length = self.min_pattern_length // 2
        
        if len(df) < pole_length + flag_length:
            return None
        
        pole_df = df.iloc[-pole_length - flag_length:-flag_length]
        flag_df = df.tail(flag_length)
        
        pole_trend = np.polyfit(range(len(pole_df)), pole_df["close"].values, 1)[0]
        flag_high_trend = np.polyfit(range(len(flag_df)), flag_df["high"].values, 1)[0]
        flag_low_trend = np.polyfit(range(len(flag_df)), flag_df["low"].values, 1)[0]
        
        if abs(pole_trend) < np.std(pole_df["close"]) * 0.1:
            return None
        
        if pole_trend > 0 and flag_high_trend < 0 and flag_low_trend < 0:
            direction = "BULLISH"
        elif pole_trend < 0 and flag_high_trend > 0 and flag_low_trend > 0:
            direction = "BEARISH"
        else:
            return None
        
        pole_start = pole_df.iloc[0]["close"]
        pole_end = pole_df.iloc[-1]["close"]
        flag_breakout = flag_df.iloc[-1]["close"]
        
        return {
            "pattern_type": "FLAG",
            "pattern_direction": direction,
            "pole_start": float(pole_start),
            "pole_end": float(pole_end),
            "flag_breakout": float(flag_breakout),
            "completion_percentage": 0.7,
            "volume_confirmation": self._check_volume_trend(flag_df)
        }
    
    def detect_pennant(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает вымпел (продолжение тренда)."""
        if len(df) < self.min_pattern_length:
            return None
        
        pole_length = self.min_pattern_length // 2
        pennant_length = self.min_pattern_length // 2
        
        if len(df) < pole_length + pennant_length:
            return None
        
        pole_df = df.iloc[-pennant_length - pole_length:-pennant_length]
        pennant_df = df.tail(pennant_length)
        
        pole_trend = np.polyfit(range(len(pole_df)), pole_df["close"].values, 1)[0]
        pennant_high_trend = np.polyfit(range(len(pennant_df)), pennant_df["high"].values, 1)[0]
        pennant_low_trend = np.polyfit(range(len(pennant_df)), pennant_df["low"].values, 1)[0]
        
        if abs(pole_trend) < np.std(pole_df["close"]) * 0.1:
            return None
        
        if abs(pennant_high_trend) < 0.1 and abs(pennant_low_trend) < 0.1:
            return None
        
        if pole_trend > 0 and pennant_high_trend < 0 and pennant_low_trend > 0:
            direction = "BULLISH"
        elif pole_trend < 0 and pennant_high_trend < 0 and pennant_low_trend > 0:
            direction = "BEARISH"
        else:
            return None
        
        pole_start = pole_df.iloc[0]["close"]
        pole_end = pole_df.iloc[-1]["close"]
        pennant_breakout = pennant_df.iloc[-1]["close"]
        
        return {
            "pattern_type": "PENNANT",
            "pattern_direction": direction,
            "pole_start": float(pole_start),
            "pole_end": float(pole_end),
            "pennant_breakout": float(pennant_breakout),
            "completion_percentage": 0.7,
            "volume_confirmation": self._check_volume_trend(pennant_df)
        }
    
    def detect_wedge(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает клин (восходящий/нисходящий)."""
        if len(df) < self.min_pattern_length:
            return None
        
        recent_df = df.tail(self.min_pattern_length)
        highs = recent_df["high"].values
        lows = recent_df["low"].values
        
        high_trend = np.polyfit(range(len(highs)), highs, 1)[0]
        low_trend = np.polyfit(range(len(lows)), lows, 1)[0]
        
        if high_trend > 0 and low_trend > 0 and abs(high_trend - low_trend) / max(abs(high_trend), abs(low_trend)) > 0.3:
            direction = "BEARISH"
            pattern_type = "RISING_WEDGE"
        elif high_trend < 0 and low_trend < 0 and abs(high_trend - low_trend) / max(abs(high_trend), abs(low_trend)) > 0.3:
            direction = "BULLISH"
            pattern_type = "FALLING_WEDGE"
        else:
            return None
        
        apex_price = (highs[-1] + lows[-1]) / 2
        
        return {
            "pattern_type": pattern_type,
            "pattern_direction": direction,
            "apex_price": float(apex_price),
            "completion_percentage": 0.6,
            "volume_confirmation": self._check_volume_trend(recent_df)
        }
    
    def detect_rectangle(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает прямоугольник (флэт, консолидация)."""
        if len(df) < self.min_pattern_length:
            return None
        
        recent_df = df.tail(self.min_pattern_length)
        highs = recent_df["high"].values
        lows = recent_df["low"].values
        
        high_range = np.max(highs) - np.min(highs)
        low_range = np.max(lows) - np.min(lows)
        price_range = np.max(highs) - np.min(lows)
        
        if high_range / price_range > 0.3 or low_range / price_range > 0.3:
            return None
        
        high_trend = np.polyfit(range(len(highs)), highs, 1)[0]
        low_trend = np.polyfit(range(len(lows)), lows, 1)[0]
        
        if abs(high_trend) > np.std(highs) * 0.2 or abs(low_trend) > np.std(lows) * 0.2:
            return None
        
        resistance = np.max(highs)
        support = np.min(lows)
        current_price = recent_df.iloc[-1]["close"]
        
        return {
            "pattern_type": "RECTANGLE",
            "pattern_direction": "NEUTRAL",
            "resistance": float(resistance),
            "support": float(support),
            "current_price": float(current_price),
            "completion_percentage": 0.5,
            "volume_confirmation": self._check_volume_trend(recent_df)
        }
    
    def _calculate_completion(
        self,
        df: pd.DataFrame,
        peak_price: float,
        neckline: float,
        is_resistance: bool = True
    ) -> float:
        """Вычисляет процент завершения паттерна."""
        current = df.iloc[-1]
        
        if is_resistance:
            current_price = current["high"]
            if current_price <= neckline:
                return 1.0
            progress = (peak_price - current_price) / (peak_price - neckline)
        else:
            current_price = current["low"]
            if current_price >= neckline:
                return 1.0
            progress = (current_price - peak_price) / (neckline - peak_price)
        
        return max(0.0, min(1.0, 1.0 - progress))
    
    def _check_volume(self, df: pd.DataFrame, start_idx: int, end_idx: int) -> bool:
        """Проверяет подтверждение объемом."""
        pattern_volume = df.iloc[start_idx:end_idx + 1]["volume"].mean()
        avg_volume = df["volume"].mean()
        return pattern_volume > avg_volume * 0.8
    
    def _check_volume_trend(self, df: pd.DataFrame) -> bool:
        """Проверяет тренд объемов."""
        if len(df) < 10:
            return False
        recent_volume = df.tail(5)["volume"].mean()
        earlier_volume = df.iloc[-10:-5]["volume"].mean()
        return recent_volume > earlier_volume * 0.9

