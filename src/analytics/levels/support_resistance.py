"""Определение уровней поддержки и сопротивления."""
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from collections import defaultdict

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


class SupportResistanceAnalyzer:
    """Анализатор уровней поддержки и сопротивления."""
    
    def __init__(self, min_touches: int = 2, price_tolerance: float = 0.005):
        self.min_touches = min_touches
        self.price_tolerance = price_tolerance
    
    def find_levels(self, df: pd.DataFrame) -> Dict[str, List[Dict[str, Any]]]:
        """Находит уровни поддержки и сопротивления."""
        if len(df) < 20:
            return {"support_levels": [], "resistance_levels": []}
        
        highs = df["high"].values
        lows = df["low"].values
        
        resistance_levels = self._find_resistance_levels(highs, df)
        support_levels = self._find_support_levels(lows, df)
        
        return {
            "support_levels": support_levels,
            "resistance_levels": resistance_levels
        }
    
    def _find_resistance_levels(
        self,
        highs: np.ndarray,
        df: pd.DataFrame
    ) -> List[Dict[str, Any]]:
        """Находит уровни сопротивления."""
        levels = []
        price_groups = defaultdict(list)
        
        for i, high in enumerate(highs):
            for price in price_groups.keys():
                if abs(high - price) / price <= self.price_tolerance:
                    price_groups[price].append(i)
                    break
            else:
                price_groups[high].append(i)
        
        for price, indices in price_groups.items():
            if len(indices) >= self.min_touches:
                touches = len(indices)
                strength = min(touches / 5.0, 1.0)
                
                levels.append({
                    "price": float(price),
                    "strength": strength,
                    "touches": touches
                })
        
        levels.sort(key=lambda x: x["strength"], reverse=True)
        return levels[:5]
    
    def _find_support_levels(
        self,
        lows: np.ndarray,
        df: pd.DataFrame
    ) -> List[Dict[str, Any]]:
        """Находит уровни поддержки."""
        levels = []
        price_groups = defaultdict(list)
        
        for i, low in enumerate(lows):
            for price in price_groups.keys():
                if abs(low - price) / price <= self.price_tolerance:
                    price_groups[price].append(i)
                    break
            else:
                price_groups[low].append(i)
        
        for price, indices in price_groups.items():
            if len(indices) >= self.min_touches:
                touches = len(indices)
                strength = min(touches / 5.0, 1.0)
                
                levels.append({
                    "price": float(price),
                    "strength": strength,
                    "touches": touches
                })
        
        levels.sort(key=lambda x: x["strength"], reverse=True)
        return levels[:5]
    
    def check_breakout(
        self,
        df: pd.DataFrame,
        levels: Dict[str, List[Dict[str, Any]]],
        volume_confirmation: bool = True
    ) -> Dict[str, Any]:
        """Проверяет прорыв уровней."""
        if len(df) < 2:
            return {"breakout": False}
        
        current = df.iloc[-1]
        prev = df.iloc[-2]
        current_volume = current["volume"]
        avg_volume = df["volume"].tail(20).mean()
        
        result = {"breakout": False, "level_type": None, "price": None}
        
        for level in levels.get("resistance_levels", []):
            if (prev["high"] < level["price"] and
                current["high"] > level["price"]):
                if not volume_confirmation or current_volume > avg_volume * 1.2:
                    result = {
                        "breakout": True,
                        "level_type": "resistance",
                        "price": level["price"],
                        "strength": level["strength"]
                    }
                    break
        
        if not result["breakout"]:
            for level in levels.get("support_levels", []):
                if (prev["low"] > level["price"] and
                    current["low"] < level["price"]):
                    if not volume_confirmation or current_volume > avg_volume * 1.2:
                        result = {
                            "breakout": True,
                            "level_type": "support",
                            "price": level["price"],
                            "strength": level["strength"]
                        }
                        break
        
        return result

