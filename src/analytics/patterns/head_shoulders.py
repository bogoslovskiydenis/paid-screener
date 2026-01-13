"""Распознавание паттерна Голова и плечи."""
import pandas as pd
import numpy as np
from typing import Optional, Dict, Any

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


class HeadShouldersPattern:
    """Анализатор паттерна Голова и плечи."""
    
    def __init__(self, min_pattern_length: int = 20, symmetry_tolerance: float = 0.1):
        self.min_pattern_length = min_pattern_length
        self.symmetry_tolerance = symmetry_tolerance
    
    def detect(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Обнаруживает паттерн Голова и плечи."""
        if len(df) < self.min_pattern_length:
            return None
        
        highs = df["high"].values
        lows = df["low"].values
        
        bearish = self._detect_bearish_pattern(highs, df)
        if bearish:
            return bearish
        
        bullish = self._detect_bullish_pattern(lows, df)
        if bullish:
            return bullish
        
        return None
    
    def _detect_bearish_pattern(
        self,
        highs: np.ndarray,
        df: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """Обнаруживает медвежий паттерн (Голова и плечи)."""
        peaks, properties = find_peaks(
            highs,
            distance=self.min_pattern_length // 4,
            prominence=np.std(highs) * 0.5
        )
        
        if len(peaks) < 3:
            return None
        
        for i in range(len(peaks) - 2):
            left_shoulder_idx = peaks[i]
            head_idx = peaks[i + 1]
            right_shoulder_idx = peaks[i + 2]
            
            left_shoulder = highs[left_shoulder_idx]
            head = highs[head_idx]
            right_shoulder = highs[right_shoulder_idx]
            
            if (head > left_shoulder and
                head > right_shoulder and
                abs(left_shoulder - right_shoulder) / max(left_shoulder, right_shoulder) < self.symmetry_tolerance):
                
                neckline = self._calculate_neckline(
                    df,
                    left_shoulder_idx,
                    head_idx,
                    right_shoulder_idx
                )
                
                target = neckline - (head - neckline)
                completion = self._calculate_completion(df, neckline, head)
                
                volume_confirmation = self._check_volume(df, left_shoulder_idx, right_shoulder_idx)
                
                return {
                    "pattern_type": "HEAD_AND_SHOULDERS",
                    "pattern_direction": "BEARISH",
                    "neckline": float(neckline),
                    "head_price": float(head),
                    "target_price": float(target),
                    "completion_percentage": completion,
                    "volume_confirmation": volume_confirmation
                }
        
        return None
    
    def _detect_bullish_pattern(
        self,
        lows: np.ndarray,
        df: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """Обнаруживает бычий паттерн (Обратная Голова и плечи)."""
        peaks, properties = find_peaks(
            -lows,
            distance=self.min_pattern_length // 4,
            prominence=np.std(lows) * 0.5
        )
        
        if len(peaks) < 3:
            return None
        
        for i in range(len(peaks) - 2):
            left_shoulder_idx = peaks[i]
            head_idx = peaks[i + 1]
            right_shoulder_idx = peaks[i + 2]
            
            left_shoulder = lows[left_shoulder_idx]
            head = lows[head_idx]
            right_shoulder = lows[right_shoulder_idx]
            
            if (head < left_shoulder and
                head < right_shoulder and
                abs(left_shoulder - right_shoulder) / max(left_shoulder, right_shoulder) < self.symmetry_tolerance):
                
                neckline = self._calculate_neckline(
                    df,
                    left_shoulder_idx,
                    head_idx,
                    right_shoulder_idx,
                    is_support=True
                )
                
                target = neckline + (neckline - head)
                completion = self._calculate_completion(df, neckline, head, is_support=True)
                
                volume_confirmation = self._check_volume(df, left_shoulder_idx, right_shoulder_idx)
                
                return {
                    "pattern_type": "INVERSE_HEAD_AND_SHOULDERS",
                    "pattern_direction": "BULLISH",
                    "neckline": float(neckline),
                    "head_price": float(head),
                    "target_price": float(target),
                    "completion_percentage": completion,
                    "volume_confirmation": volume_confirmation
                }
        
        return None
    
    def _calculate_neckline(
        self,
        df: pd.DataFrame,
        left_idx: int,
        head_idx: int,
        right_idx: int,
        is_support: bool = False
    ) -> float:
        """Вычисляет линию шеи."""
        if is_support:
            left_val = df.iloc[left_idx]["low"]
            right_val = df.iloc[right_idx]["low"]
        else:
            left_val = df.iloc[left_idx]["high"]
            right_val = df.iloc[right_idx]["high"]
        
        return (left_val + right_val) / 2.0
    
    def _calculate_completion(
        self,
        df: pd.DataFrame,
        neckline: float,
        head: float,
        is_support: bool = False
    ) -> float:
        """Вычисляет процент завершения паттерна."""
        current = df.iloc[-1]
        
        if is_support:
            current_price = current["low"]
            if current_price <= neckline:
                return 1.0
            progress = (neckline - current_price) / (neckline - head)
        else:
            current_price = current["high"]
            if current_price >= neckline:
                return 1.0
            progress = (current_price - neckline) / (head - neckline)
        
        return max(0.0, min(1.0, progress))
    
    def _check_volume(
        self,
        df: pd.DataFrame,
        left_idx: int,
        right_idx: int
    ) -> bool:
        """Проверяет подтверждение объемом."""
        pattern_volume = df.iloc[left_idx:right_idx + 1]["volume"].mean()
        avg_volume = df["volume"].mean()
        
        return pattern_volume > avg_volume * 0.8

