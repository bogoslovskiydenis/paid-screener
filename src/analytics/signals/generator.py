"""Генерация торговых сигналов."""
import pandas as pd
from typing import Optional, Dict, Any, List
from datetime import datetime
try:
    from ..candlestick.patterns import CandlestickPatternAnalyzer
    from ..levels.support_resistance import SupportResistanceAnalyzer
    from ..patterns.head_shoulders import HeadShouldersPattern
    from ...utils.logger import setup_logger
except ImportError:
    from analytics.candlestick.patterns import CandlestickPatternAnalyzer
    from analytics.levels.support_resistance import SupportResistanceAnalyzer
    from analytics.patterns.head_shoulders import HeadShouldersPattern
    try:
        from utils.logger import setup_logger
    except ImportError:
        import logging
        def setup_logger(name):
            return logging.getLogger(name)

logger = setup_logger(__name__)


class SignalGenerator:
    """Генератор торговых сигналов."""
    
    def __init__(self, min_confidence: float = 0.6):
        self.min_confidence = min_confidence
        self.candlestick_analyzer = CandlestickPatternAnalyzer()
        self.levels_analyzer = SupportResistanceAnalyzer()
        self.pattern_analyzer = HeadShouldersPattern()
    
    def generate_signal(
        self,
        asset: str,
        timeframe: str,
        df: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """Генерирует торговый сигнал на основе анализа."""
        if len(df) < 100:
            logger.warning(f"Insufficient data for {asset}/{timeframe}")
            return None
        
        current_price = df.iloc[-1]["close"]
        
        candlestick_pattern = self.candlestick_analyzer.analyze(df)
        levels = self.levels_analyzer.find_levels(df)
        head_shoulders = self.pattern_analyzer.detect(df)
        
        signal_data = self._evaluate_signals(
            df,
            current_price,
            candlestick_pattern,
            levels,
            head_shoulders
        )
        
        if not signal_data or signal_data["confidence"] < self.min_confidence:
            return None
        
        signal_data.update({
            "asset": asset,
            "timeframe": timeframe,
            "timestamp": datetime.utcnow(),
            "current_price": current_price
        })
        
        return signal_data
    
    def _evaluate_signals(
        self,
        df: pd.DataFrame,
        current_price: float,
        candlestick_pattern: Optional[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Оценивает сигналы и определяет тип."""
        buy_score = 0.0
        sell_score = 0.0
        
        buy_factors = []
        sell_factors = []
        
        if candlestick_pattern:
            if any(x in candlestick_pattern for x in ["Hammer", "Bullish Engulfing", "Morning Star"]):
                buy_score += 0.3
                buy_factors.append(f"Candlestick: {candlestick_pattern}")
            elif any(x in candlestick_pattern for x in ["Shooting Star", "Bearish Engulfing", "Evening Star"]):
                sell_score += 0.3
                sell_factors.append(f"Candlestick: {candlestick_pattern}")
        
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        if support_levels:
            nearest_support = max(support_levels, key=lambda x: x["price"])
            if abs(current_price - nearest_support["price"]) / current_price < 0.02:
                buy_score += 0.2 * nearest_support["strength"]
                buy_factors.append(f"Near support: {nearest_support['price']}")
        
        if resistance_levels:
            nearest_resistance = min(resistance_levels, key=lambda x: x["price"])
            if abs(current_price - nearest_resistance["price"]) / current_price < 0.02:
                sell_score += 0.2 * nearest_resistance["strength"]
                sell_factors.append(f"Near resistance: {nearest_resistance['price']}")
        
        if head_shoulders:
            if head_shoulders["pattern_direction"] == "BULLISH":
                buy_score += 0.4
                buy_factors.append("Inverse Head and Shoulders")
            elif head_shoulders["pattern_direction"] == "BEARISH":
                sell_score += 0.4
                sell_factors.append("Head and Shoulders")
        
        volume_confirmation = self._check_volume(df)
        if volume_confirmation:
            if buy_score > sell_score:
                buy_score += 0.1
            else:
                sell_score += 0.1
        
        if buy_score > sell_score and buy_score > 0.5:
            return self._create_buy_signal(
                df,
                current_price,
                buy_score,
                buy_factors,
                levels,
                head_shoulders,
                volume_confirmation
            )
        elif sell_score > buy_score and sell_score > 0.5:
            return self._create_sell_signal(
                df,
                current_price,
                sell_score,
                sell_factors,
                levels,
                head_shoulders,
                volume_confirmation
            )
        
        return None
    
    def _create_buy_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        confidence: float,
        factors: List[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]],
        volume_confirmation: bool
    ) -> Dict[str, Any]:
        """Создает сигнал на покупку."""
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        entry_price = current_price
        stop_loss = current_price * 0.97
        
        if support_levels:
            nearest_support = max([s for s in support_levels if s["price"] < current_price], 
                                 key=lambda x: x["price"], default=None)
            if nearest_support:
                stop_loss = nearest_support["price"] * 0.995
        
        take_profit = []
        if resistance_levels:
            for i, res in enumerate(resistance_levels[:2]):
                if res["price"] > current_price:
                    take_profit.append({
                        "level": res["price"],
                        "probability": 0.7 - i * 0.2
                    })
        
        if head_shoulders and head_shoulders.get("target_price"):
            take_profit.append({
                "level": head_shoulders["target_price"],
                "probability": 0.6
            })
        
        if not take_profit:
            take_profit = [{"level": current_price * 1.05, "probability": 0.7}]
        
        strength = "STRONG" if confidence > 0.8 else "MEDIUM" if confidence > 0.65 else "WEAK"
        
        return {
            "signal_type": "BUY",
            "strength": strength,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "indicators": {
                "candlestick_pattern": factors[0] if factors else None,
                "support_level": support_levels[0]["price"] if support_levels else None,
                "resistance_level": resistance_levels[0]["price"] if resistance_levels else None,
                "volume_confirmation": volume_confirmation,
                "head_shoulders": head_shoulders is not None
            },
            "confidence": confidence
        }
    
    def _create_sell_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        confidence: float,
        factors: List[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]],
        volume_confirmation: bool
    ) -> Dict[str, Any]:
        """Создает сигнал на продажу."""
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        entry_price = current_price
        stop_loss = current_price * 1.03
        
        if resistance_levels:
            nearest_resistance = min([r for r in resistance_levels if r["price"] > current_price],
                                    key=lambda x: x["price"], default=None)
            if nearest_resistance:
                stop_loss = nearest_resistance["price"] * 1.005
        
        take_profit = []
        if support_levels:
            for i, sup in enumerate(support_levels[:2]):
                if sup["price"] < current_price:
                    take_profit.append({
                        "level": sup["price"],
                        "probability": 0.7 - i * 0.2
                    })
        
        if head_shoulders and head_shoulders.get("target_price"):
            take_profit.append({
                "level": head_shoulders["target_price"],
                "probability": 0.6
            })
        
        if not take_profit:
            take_profit = [{"level": current_price * 0.95, "probability": 0.7}]
        
        strength = "STRONG" if confidence > 0.8 else "MEDIUM" if confidence > 0.65 else "WEAK"
        
        return {
            "signal_type": "SELL",
            "strength": strength,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "indicators": {
                "candlestick_pattern": factors[0] if factors else None,
                "support_level": support_levels[0]["price"] if support_levels else None,
                "resistance_level": resistance_levels[0]["price"] if resistance_levels else None,
                "volume_confirmation": volume_confirmation,
                "head_shoulders": head_shoulders is not None
            },
            "confidence": confidence
        }
    
    def _check_volume(self, df: pd.DataFrame) -> bool:
        """Проверяет подтверждение объемом."""
        if len(df) < 20:
            return False
        
        current_volume = df.iloc[-1]["volume"]
        avg_volume = df["volume"].tail(20).mean()
        
        return current_volume > avg_volume * 1.1

