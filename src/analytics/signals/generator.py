"""Генерация торговых сигналов."""
import logging
import pandas as pd
from typing import Optional, Dict, Any, List
from datetime import datetime

from ..candlestick.patterns import CandlestickPatternAnalyzer
from ..levels.support_resistance import SupportResistanceAnalyzer
from ..patterns.head_shoulders import HeadShouldersPattern
from ..indicators.rsi import RSICalculator
from ..indicators.atr import ATRCalculator
from ..indicators.ema import EMACalculator
from ..indicators.macd import MACDCalculator
from ..indicators.vwap import VWAPCalculator
from ..indicators.volume_profile import VolumeProfileCalculator
from ..indicators import fibonacci as fib_module
from ..market_structure import detect_structure, detect_order_blocks, detect_liquidity_grab

try:
    from ...utils.logger import setup_logger
except ImportError:
    def setup_logger(name: str) -> logging.Logger:  # type: ignore[misc]
        return logging.getLogger(name)

logger = setup_logger(__name__)


class SignalGenerator:
    """Генератор торговых сигналов."""
    
    def __init__(self, min_confidence: float = 0.6, test_risk_usd: float = 10.0):
        self.min_confidence = min_confidence
        self.test_risk_usd = float(test_risk_usd)
        self.candlestick_analyzer = CandlestickPatternAnalyzer()
        self.levels_analyzer = SupportResistanceAnalyzer()
        self.pattern_analyzer = HeadShouldersPattern()
        self.rsi_calculator = RSICalculator(period=14)
        self.atr_calculator = ATRCalculator(period=14)
        self.ema_calculator = EMACalculator(periods=[9, 21, 50])
        self.macd_calculator = MACDCalculator()
        self.vwap_calculator = VWAPCalculator()
        self.volume_profile_calculator = VolumeProfileCalculator()

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
        rsi_analysis = self.rsi_calculator.analyze(df)
        breakout = self.levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
        atr_value = self.atr_calculator.get_current(df)
        ema_analysis = self.ema_calculator.analyze(df)
        macd_analysis = self.macd_calculator.analyze(df)
        volume_profile = self.volume_profile_calculator.analyze(df)
        fibonacci = fib_module.analyze(df)
        liquidity_grab = detect_liquidity_grab(df)

        signal_data = self._evaluate_signals(
            df,
            current_price,
            candlestick_pattern,
            levels,
            head_shoulders,
            rsi_analysis,
            breakout,
            atr_value=atr_value,
            ema_analysis=ema_analysis,
            macd_analysis=macd_analysis,
            timeframe=timeframe,
            volume_profile=volume_profile,
            fibonacci=fibonacci,
            liquidity_grab=liquidity_grab,
        )

        # Если основной скорер ничего не дал — пробуем контрарианский разворот
        if signal_data is None:
            signal_data = self._check_oversold_bounce(
                df=df,
                current_price=current_price,
                levels=levels,
                rsi_analysis=rsi_analysis,
                candlestick_pattern=candlestick_pattern,
                atr_value=atr_value,
                ema_analysis=ema_analysis,
                macd_analysis=macd_analysis,
                breakout=breakout,
                timeframe=timeframe,
                fibonacci=fibonacci,
                liquidity_grab=liquidity_grab,
            )

        # Растущий рынок: основной скорер рубит BUY по RSI>70 (вершина).
        # Ловим вход на откате к динамической поддержке в подтверждённом аптренде.
        if signal_data is None:
            signal_data = self._check_pullback_entry(
                df=df,
                current_price=current_price,
                levels=levels,
                rsi_analysis=rsi_analysis,
                candlestick_pattern=candlestick_pattern,
                atr_value=atr_value,
                ema_analysis=ema_analysis,
                macd_analysis=macd_analysis,
                timeframe=timeframe,
                fibonacci=fibonacci,
                volume_profile=volume_profile,
            )

        if not signal_data or signal_data["confidence"] < self.min_confidence:
            return None

        signal_data.update(
            {
                "asset": asset,
                "timeframe": timeframe,
                "timestamp": datetime.utcnow(),
                "current_price": current_price,
            }
        )

        return signal_data
    
    # Максимальное расстояние до TP в зависимости от таймфрейма
    _MAX_TP_PCT: Dict[str, float] = {
        "5m":  0.015,
        "15m": 0.025,
        "1h":  0.05,
        "4h":  0.10,
        "1d":  0.20,
        "3d":  0.25,
        "1w":  0.30,
        "1M":  0.40,
    }

    def _evaluate_signals(
        self,
        df: pd.DataFrame,
        current_price: float,
        candlestick_pattern: Optional[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]],
        rsi_analysis: Dict[str, Any],
        breakout: Optional[Dict[str, Any]] = None,
        atr_value: Optional[float] = None,
        ema_analysis: Optional[Dict[str, Any]] = None,
        macd_analysis: Optional[Dict[str, Any]] = None,
        timeframe: str = "",
        volume_profile: Optional[Dict[str, Any]] = None,
        fibonacci: Optional[Dict[str, Any]] = None,
        liquidity_grab: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Оценивает сигналы и определяет тип."""
        buy_score = 0.0
        sell_score = 0.0
        
        buy_factors = []
        sell_factors = []
        
        # RSI анализ
        rsi_signal = rsi_analysis.get("rsi_signal", "NEUTRAL")
        rsi_strength = rsi_analysis.get("rsi_strength", 0.0)
        rsi_zone = rsi_analysis.get("rsi_zone", "NEUTRAL")
        
        if rsi_signal == "BUY":
            buy_score += 0.3 * rsi_strength
            buy_factors.append(f"RSI: {rsi_zone} ({rsi_analysis.get('rsi', 0):.1f})")
        elif rsi_signal == "SELL":
            sell_score += 0.3 * rsi_strength
            sell_factors.append(f"RSI: {rsi_zone} ({rsi_analysis.get('rsi', 0):.1f})")
        
        if candlestick_pattern:
            if any(x in candlestick_pattern for x in ["Hammer", "Bullish Engulfing", "Morning Star"]):
                buy_score += 0.3
                buy_factors.append(f"Candlestick: {candlestick_pattern}")
            elif any(x in candlestick_pattern for x in ["Shooting Star", "Bearish Engulfing", "Evening Star"]):
                sell_score += 0.3
                sell_factors.append(f"Candlestick: {candlestick_pattern}")
        
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        below_supports = [s for s in support_levels if s["price"] < current_price]
        if below_supports:
            nearest_support = max(below_supports, key=lambda x: x["price"])
            if (current_price - nearest_support["price"]) / current_price < 0.02:
                buy_score += 0.2 * nearest_support["strength"]
                buy_factors.append(f"Near support: {nearest_support['price']}")

        above_resistances = [r for r in resistance_levels if r["price"] > current_price]
        if above_resistances:
            nearest_resistance = min(above_resistances, key=lambda x: x["price"])
            if (nearest_resistance["price"] - current_price) / current_price < 0.02:
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

        breakout_info = breakout or {}
        if breakout_info.get("breakout"):
            level_type = breakout_info.get("level_type")
            direction = breakout_info.get("breakout_direction")
            strength = breakout_info.get("strength", 1.0)
            impact = 0.6 * float(strength)
            if level_type == "resistance" and direction == "UP":
                buy_score += impact
                buy_factors.append(f"Breakout RESISTANCE at {breakout_info.get('price')}")
            elif level_type == "support" and direction == "DOWN":
                sell_score += impact
                sell_factors.append(f"Breakout SUPPORT at {breakout_info.get('price')}")
        
        # EMA trend
        ema = ema_analysis or {}
        ema_trend = ema.get("trend", "NEUTRAL")
        ema_cross = ema.get("ema_cross", "NEUTRAL")
        if ema_trend == "BULLISH":
            buy_score += 0.15
            buy_factors.append("EMA50: цена выше тренда")
        elif ema_trend == "BEARISH":
            sell_score += 0.15
            sell_factors.append("EMA50: цена ниже тренда")
        if ema_cross == "BULLISH":
            buy_score += 0.1
            buy_factors.append("EMA9/21: бычье пересечение")
        elif ema_cross == "BEARISH":
            sell_score += 0.1
            sell_factors.append("EMA9/21: медвежье пересечение")

        # MACD
        macd = macd_analysis or {}
        macd_sig = macd.get("macd_signal", "NEUTRAL")
        macd_div = macd.get("divergence")
        if macd_sig == "BUY":
            weight = 0.25 if macd.get("bullish_cross") else 0.15
            buy_score += weight
            buy_factors.append("MACD: бычье пересечение" if macd.get("bullish_cross") else "MACD: бычий импульс")
        elif macd_sig == "SELL":
            weight = 0.25 if macd.get("bearish_cross") else 0.15
            sell_score += weight
            sell_factors.append("MACD: медвежье пересечение" if macd.get("bearish_cross") else "MACD: медвежий импульс")
        if macd_div == "BULLISH":
            buy_score += 0.2
            buy_factors.append("MACD дивергенция: бычья")
        elif macd_div == "BEARISH":
            sell_score += 0.2
            sell_factors.append("MACD дивергенция: медвежья")

        # VWAP
        vwap_data = self.vwap_calculator.analyze(df)
        vwap_sig = vwap_data.get("vwap_signal", "NEUTRAL")
        if vwap_sig == "BULLISH":
            buy_score += 0.10
            buy_factors.append(f"Цена выше VWAP ({vwap_data['vwap_distance_pct']:+.1f}%)")
        elif vwap_sig == "BEARISH":
            sell_score += 0.10
            sell_factors.append(f"Цена ниже VWAP ({vwap_data['vwap_distance_pct']:+.1f}%)")

        # Market Structure (BOS / CHoCH)
        ms = detect_structure(df)
        if ms.get("bos"):
            if ms["bos"]["type"] == "BULLISH":
                buy_score += 0.15
                buy_factors.append("BOS: пробой swing high (тренд)")
            else:
                sell_score += 0.15
                sell_factors.append("BOS: пробой swing low (тренд)")
        if ms.get("choch"):
            if ms["choch"]["type"] == "BULLISH":
                buy_score += 0.20
                buy_factors.append("CHoCH: разворот вверх")
            elif ms["choch"]["type"] == "BEARISH":
                sell_score += 0.20
                sell_factors.append("CHoCH: разворот вниз")

        # Order Blocks
        obs = detect_order_blocks(df)
        active_bull_ob = [ob for ob in obs if ob["type"] == "BULLISH" and not ob["mitigated"]]
        active_bear_ob = [ob for ob in obs if ob["type"] == "BEARISH" and not ob["mitigated"]]
        if active_bull_ob:
            nearest = min(active_bull_ob, key=lambda x: x["candles_ago"])
            if abs(current_price - nearest["ob_high"]) / current_price < 0.03:
                buy_score += 0.10
                buy_factors.append("Order Block бычий (зона входа)")
        if active_bear_ob:
            nearest = min(active_bear_ob, key=lambda x: x["candles_ago"])
            if abs(current_price - nearest["ob_low"]) / current_price < 0.03:
                sell_score += 0.10
                sell_factors.append("Order Block медвежий (зона входа)")

        # Volume Profile — POC как магнит, VAL как поддержка
        vp = volume_profile or {}
        vp_val = vp.get("val")
        vp_vah = vp.get("vah")
        vp_poc = vp.get("poc")
        if vp_val and current_price > 0 and abs(current_price - vp_val) / current_price < 0.015:
            buy_score += 0.10
            buy_factors.append(f"Цена у VAL (нижняя граница Value Area)")
        if vp_vah and current_price > 0 and abs(current_price - vp_vah) / current_price < 0.015:
            sell_score += 0.10
            sell_factors.append(f"Цена у VAH (верхняя граница Value Area)")
        if vp_poc and current_price > 0 and abs(current_price - vp_poc) / current_price < 0.01:
            buy_score += 0.05
            buy_factors.append(f"Цена у POC — магнит объёма")

        # Fibonacci — golden zone как лучшая зона входа
        fib = fibonacci or {}
        if fib.get("in_golden_zone") and fib.get("trend") == "BULLISH":
            buy_score += 0.15
            buy_factors.append("Fibonacci: цена в Golden Zone (0.618–0.786)")
        elif fib.get("nearest_level"):
            nl = fib["nearest_level"]
            if nl.get("distance_pct", 100) < 1.0 and fib.get("trend") == "BULLISH" and nl.get("ratio", 0) >= 0.382:
                buy_score += 0.10
                buy_factors.append(f"Fibonacci: у уровня {nl['label']}")
            elif nl.get("distance_pct", 100) < 1.0 and fib.get("trend") == "BEARISH" and nl.get("ratio", 0) >= 0.382:
                sell_score += 0.10
                sell_factors.append(f"Fibonacci: у уровня {nl['label']}")

        # Liquidity Grab — институциональный сбор ликвидности
        lg = liquidity_grab
        if lg and lg["type"] == "BULLISH":
            buy_score += 0.25
            buy_factors.append(f"Liquidity Grab: свип swing low {lg['grabbed_level']:.4f}")
        elif lg and lg["type"] == "BEARISH":
            sell_score += 0.25
            sell_factors.append(f"Liquidity Grab: свип swing high {lg['grabbed_level']:.4f}")

        buy_score = min(buy_score, 0.97)
        sell_score = min(sell_score, 0.97)

        max_tp_pct = self._MAX_TP_PCT.get(timeframe, 0.20)
        rsi_val = float(rsi_analysis.get("rsi") or 50)

        if buy_score > sell_score and buy_score > 0.6:
            if rsi_val > 70:
                return None
            # Гард: BUY против сильной медвежьей структуры на перекупленности.
            # Без подтверждённого разворота (CHoCH вверх) — отбрасываем сигнал;
            # с CHoCH — пропускаем, но не даём STRONG (контекст спорный).
            bearish_ctx = (
                ms.get("structure") == "BEARISH"
                and float(ms.get("trend_strength") or 0.0) >= 0.70
            )
            choch_up = bool(ms.get("choch") and ms["choch"]["type"] == "BULLISH")
            if bearish_ctx and rsi_val >= 65:
                if not choch_up:
                    return None
                buy_score = min(buy_score, 0.79)
                buy_factors.append(
                    "⚠️ Структура BEARISH + RSI перекуплен — уверенность снижена"
                )
            # Гард STRONG: контр-трендовый BUY не может быть STRONG.
            # Если дневной тренд медвежий (EMA BEARISH) и нет подтверждённой
            # бычьей структуры (BULLISH или CHoCH вверх) — это в лучшем случае
            # отскок от перепроданности, поэтому ограничиваем до MEDIUM (<0.85).
            structure_bullish = ms.get("structure") == "BULLISH" or choch_up
            if ema_trend == "BEARISH" and not structure_bullish and buy_score >= 0.85:
                buy_score = 0.84
                buy_factors.append(
                    "⚠️ EMA медвежья + нет бычьей структуры — не STRONG (отскок)"
                )
            return self._create_buy_signal(
                df, current_price, buy_score, buy_factors,
                levels, head_shoulders, volume_confirmation, rsi_analysis,
                atr_value=atr_value, ema_analysis=ema_analysis, macd_analysis=macd_analysis,
                max_tp_pct=max_tp_pct,
                vwap_data=vwap_data, volume_profile=volume_profile,
                fibonacci=fibonacci, order_blocks=obs,
            )
        elif sell_score > buy_score and sell_score > 0.6:
            if rsi_val < 55:
                return None
            return self._create_sell_signal(
                df, current_price, sell_score, sell_factors,
                levels, head_shoulders, volume_confirmation, rsi_analysis,
                atr_value=atr_value, ema_analysis=ema_analysis, macd_analysis=macd_analysis,
                max_tp_pct=max_tp_pct,
            )
        
        return None
    
    def _filter_tp_by_rr(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: List[Dict[str, Any]],
        signal_type: str,
        min_rr: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """Убирает слишком близкие цели, у которых R/R < min_rr.

        Иначе первой целью (TP1) оказывается уровень вплотную к входу/сопротивлению
        с убыточным соотношением (напр. R/R 0.59), и именно по нему считаются
        отображаемый R/R и test_trade.expected_rr.
        """
        risk = (entry_price - stop_loss) if signal_type == "BUY" else (stop_loss - entry_price)
        if risk <= 0:
            return take_profit

        kept: List[Dict[str, Any]] = []
        for tp in take_profit:
            level = tp.get("level")
            if not isinstance(level, (int, float)):
                continue
            reward = (level - entry_price) if signal_type == "BUY" else (entry_price - level)
            if reward > 0 and reward / risk >= min_rr:
                kept.append(tp)
        return kept

    def _is_risk_reward_acceptable(
        self,
        entry_price: float,
        stop_loss: float,
        take_profit: List[Dict[str, Any]],
        signal_type: str,
        min_rr: float = 2.0,
    ) -> bool:
        if not take_profit:
            return False

        rr_values: List[float] = []
        for tp in take_profit:
            if not isinstance(tp, dict):
                continue
            level = tp.get("level")
            if not isinstance(level, (int, float)):
                continue
            if signal_type == "BUY":
                reward = level - entry_price
                risk = entry_price - stop_loss
            else:
                reward = entry_price - level
                risk = stop_loss - entry_price
            if risk <= 0 or reward <= 0:
                continue
            rr_values.append(reward / risk)

        if not rr_values:
            return False

        return max(rr_values) >= min_rr

    def _create_buy_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        confidence: float,
        factors: List[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]],
        volume_confirmation: bool,
        rsi_analysis: Dict[str, Any],
        atr_value: Optional[float] = None,
        ema_analysis: Optional[Dict[str, Any]] = None,
        macd_analysis: Optional[Dict[str, Any]] = None,
        max_tp_pct: float = 0.20,
        vwap_data: Optional[Dict[str, Any]] = None,
        volume_profile: Optional[Dict[str, Any]] = None,
        fibonacci: Optional[Dict[str, Any]] = None,
        order_blocks: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Создает сигнал на покупку с оптимальными зонами входа."""
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        entry_price = current_price
        stop_loss = (current_price - 1.5 * atr_value) if atr_value else current_price * 0.97

        if support_levels:
            nearest_support = max([s for s in support_levels if s["price"] < current_price],
                                  key=lambda x: x["price"], default=None)
            if nearest_support:
                stop_loss = nearest_support["price"] * 0.995

        tp_max = current_price * (1 + max_tp_pct)
        take_profit = []
        if resistance_levels:
            for i, res in enumerate(sorted(resistance_levels, key=lambda x: x["price"])[:8]):
                if current_price < res["price"] <= tp_max:
                    take_profit.append({
                        "level": res["price"],
                        "probability": max(0.1, 0.7 - i * 0.2)
                    })
        
        if head_shoulders and head_shoulders.get("target_price"):
            target = head_shoulders["target_price"]
            if current_price < target <= tp_max:
                take_profit.append({"level": target, "probability": 0.6})

        filtered_tp = [tp for tp in take_profit if isinstance(tp.get("level"), (int, float)) and tp["level"] > current_price]

        # Отбрасываем слишком близкие цели (R/R < 1) — иначе TP1 даёт убыточное соотношение
        filtered_tp = self._filter_tp_by_rr(entry_price, stop_loss, filtered_tp, "BUY", min_rr=1.0)

        if not filtered_tp:
            filtered_tp = [{"level": min(current_price * 1.05, tp_max), "probability": 0.7}]

        if not self._is_risk_reward_acceptable(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=filtered_tp,
            signal_type="BUY",
        ):
            return None

        strength = "STRONG" if confidence >= 0.85 else "MEDIUM" if confidence >= 0.7 else "WEAK"

        primary_tp_level = None
        for tp in filtered_tp:
            level = tp.get("level")
            if isinstance(level, (int, float)):
                primary_tp_level = float(level)
                break

        test_trade = None
        risk = entry_price - stop_loss
        if primary_tp_level is not None and risk > 0:
            qty = self.test_risk_usd / risk
            expected_reward = primary_tp_level - entry_price
            expected_rr = expected_reward / risk if expected_reward > 0 else 0.0
            test_trade = {
                "risk_usd": self.test_risk_usd,
                "qty": qty,
                "entry_value_usd": qty * entry_price,
                "expected_rr": expected_rr,
            }
        
        entry_zones = self._compute_entry_zones(
            current_price, levels, ema_analysis or {},
            vwap_data or {}, volume_profile or {},
            fibonacci or {}, order_blocks or [],
        )

        cs_factor = next((f for f in factors if f.startswith("Candlestick:")), None)
        ema = ema_analysis or {}
        macd = macd_analysis or {}

        ns_below = max(
            (s for s in support_levels if s["price"] < current_price),
            key=lambda x: x["price"],
            default=None,
        )
        nr_above = min(
            (r for r in resistance_levels if r["price"] > current_price),
            key=lambda x: x["price"],
            default=None,
        )
        sup_display = float(ns_below["price"]) if ns_below else None
        res_display = float(nr_above["price"]) if nr_above else None

        return {
            "signal_type": "BUY",
            "strength": strength,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": filtered_tp,
            "entry_zones": entry_zones,
            "fibonacci": fibonacci,
            "indicators": {
                "candlestick_pattern": cs_factor,
                "support_level": sup_display,
                "resistance_level": res_display,
                "volume_confirmation": volume_confirmation,
                "head_shoulders": head_shoulders is not None,
                "rsi": rsi_analysis.get("rsi"),
                "rsi_zone": rsi_analysis.get("rsi_zone"),
                "rsi_signal": rsi_analysis.get("rsi_signal"),
                "atr": atr_value,
                "ema_trend": ema.get("trend"),
                "ema_cross": ema.get("ema_cross"),
                "ema9": ema.get("ema9"),
                "ema21": ema.get("ema21"),
                "ema50": ema.get("ema50"),
                "macd_signal": macd.get("macd_signal"),
                "macd_divergence": macd.get("divergence"),
                "macd_cross": macd.get("bullish_cross"),
            },
            "confidence": confidence,
            "test_trade": test_trade,
        }
    
    def _create_sell_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        confidence: float,
        factors: List[str],
        levels: Dict[str, List[Dict[str, Any]]],
        head_shoulders: Optional[Dict[str, Any]],
        volume_confirmation: bool,
        rsi_analysis: Dict[str, Any],
        atr_value: Optional[float] = None,
        ema_analysis: Optional[Dict[str, Any]] = None,
        macd_analysis: Optional[Dict[str, Any]] = None,
        max_tp_pct: float = 0.20,
    ) -> Optional[Dict[str, Any]]:
        """Создает сигнал на продажу."""
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])
        
        entry_price = current_price
        stop_loss = (current_price + 1.5 * atr_value) if atr_value else current_price * 1.03

        if resistance_levels:
            nearest_resistance = min([r for r in resistance_levels if r["price"] > current_price],
                                     key=lambda x: x["price"], default=None)
            if nearest_resistance:
                stop_loss = nearest_resistance["price"] * 1.005

        tp_min = current_price * (1 - max_tp_pct)
        take_profit = []
        if support_levels:
            for i, sup in enumerate(sorted(support_levels, key=lambda x: x["price"], reverse=True)[:8]):
                if tp_min <= sup["price"] < current_price:
                    take_profit.append({
                        "level": sup["price"],
                        "probability": max(0.1, 0.7 - i * 0.2)
                    })
        
        if head_shoulders and head_shoulders.get("target_price"):
            target = head_shoulders["target_price"]
            if tp_min <= target < current_price:
                take_profit.append({"level": target, "probability": 0.6})

        filtered_tp = [tp for tp in take_profit if isinstance(tp.get("level"), (int, float)) and tp["level"] < current_price]

        # Отбрасываем слишком близкие цели (R/R < 1) — иначе TP1 даёт убыточное соотношение
        filtered_tp = self._filter_tp_by_rr(entry_price, stop_loss, filtered_tp, "SELL", min_rr=1.0)

        if not filtered_tp:
            filtered_tp = [{"level": max(current_price * 0.95, tp_min), "probability": 0.7}]

        if not self._is_risk_reward_acceptable(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=filtered_tp,
            signal_type="SELL",
        ):
            return None

        strength = "STRONG" if confidence >= 0.85 else "MEDIUM" if confidence >= 0.7 else "WEAK"

        primary_tp_level = None
        for tp in filtered_tp:
            level = tp.get("level")
            if isinstance(level, (int, float)):
                primary_tp_level = float(level)
                break

        test_trade = None
        risk = stop_loss - entry_price
        if primary_tp_level is not None and risk > 0:
            qty = self.test_risk_usd / risk
            expected_reward = entry_price - primary_tp_level
            expected_rr = expected_reward / risk if expected_reward > 0 else 0.0
            test_trade = {
                "risk_usd": self.test_risk_usd,
                "qty": qty,
                "entry_value_usd": qty * entry_price,
                "expected_rr": expected_rr,
            }
        
        cs_factor = next((f for f in factors if f.startswith("Candlestick:")), None)
        ema = ema_analysis or {}
        macd = macd_analysis or {}

        ns_below = max(
            (s for s in support_levels if s["price"] < current_price),
            key=lambda x: x["price"],
            default=None,
        )
        nr_above = min(
            (r for r in resistance_levels if r["price"] > current_price),
            key=lambda x: x["price"],
            default=None,
        )
        sup_display = float(ns_below["price"]) if ns_below else None
        res_display = float(nr_above["price"]) if nr_above else None

        return {
            "signal_type": "SELL",
            "strength": strength,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": filtered_tp,
            "indicators": {
                "candlestick_pattern": cs_factor,
                "support_level": sup_display,
                "resistance_level": res_display,
                "volume_confirmation": volume_confirmation,
                "head_shoulders": head_shoulders is not None,
                "rsi": rsi_analysis.get("rsi"),
                "rsi_zone": rsi_analysis.get("rsi_zone"),
                "rsi_signal": rsi_analysis.get("rsi_signal"),
                "atr": atr_value,
                "ema_trend": ema.get("trend"),
                "ema_cross": ema.get("ema_cross"),
                "ema9": ema.get("ema9"),
                "ema21": ema.get("ema21"),
                "ema50": ema.get("ema50"),
                "macd_signal": macd.get("macd_signal"),
                "macd_divergence": macd.get("divergence"),
                "macd_cross": macd.get("bearish_cross"),
            },
            "confidence": confidence,
            "test_trade": test_trade,
        }
    
    # ──────────────────────────────────────────────────────────────────────────
    # Scale-In: оптимальные зоны входа
    # ──────────────────────────────────────────────────────────────────────────

    def _compute_entry_zones(
        self,
        current_price: float,
        levels: Dict[str, List[Dict[str, Any]]],
        ema_analysis: Dict[str, Any],
        vwap_data: Dict[str, Any],
        volume_profile: Dict[str, Any],
        fibonacci: Dict[str, Any],
        order_blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Вычисляет зоны входа для scale-in (DCA) на основе конфлюенции уровней."""
        candidates: List[Dict[str, Any]] = []

        for sup in levels.get("support_levels", []):
            if sup["price"] < current_price:
                candidates.append({
                    "price": sup["price"],
                    "source": "support",
                    "weight": float(sup.get("strength", 0.5)),
                })

        for key in ("ema21", "ema50"):
            val = ema_analysis.get(key)
            if val and val < current_price:
                w = 0.6 if key == "ema21" else 0.7
                candidates.append({"price": val, "source": key, "weight": w})

        vwap = vwap_data.get("vwap")
        if vwap and vwap < current_price:
            candidates.append({"price": vwap, "source": "VWAP", "weight": 0.65})

        poc = volume_profile.get("poc")
        if poc and poc < current_price:
            candidates.append({"price": poc, "source": "VP_POC", "weight": 0.75})
        val_p = volume_profile.get("val")
        if val_p and val_p < current_price:
            candidates.append({"price": val_p, "source": "VP_VAL", "weight": 0.6})

        golden = fibonacci.get("golden_zone")
        if golden and fibonacci.get("trend") == "BULLISH":
            mid = (golden["low"] + golden["high"]) / 2
            if mid < current_price:
                candidates.append({"price": mid, "source": "fib_golden", "weight": 0.8})
        for fl in fibonacci.get("levels", []):
            if fl["ratio"] in (0.382, 0.5) and fl["price"] < current_price:
                candidates.append({"price": fl["price"], "source": f"fib_{fl['label']}", "weight": 0.55})

        for ob in order_blocks:
            if ob.get("type") == "BULLISH" and not ob.get("mitigated"):
                ob_mid = (ob["ob_high"] + ob["ob_low"]) / 2
                if ob_mid < current_price:
                    candidates.append({"price": ob_mid, "source": "order_block", "weight": 0.7})

        if not candidates:
            return [{"price": current_price, "allocation": 1.0, "type": "market", "sources": ["market"], "confluence": 0}]

        candidates.sort(key=lambda x: current_price - x["price"])

        clusters: List[Dict[str, Any]] = []
        used: set = set()
        for i, c in enumerate(candidates):
            if i in used:
                continue
            cluster = [c]
            used.add(i)
            for j, c2 in enumerate(candidates):
                if j in used:
                    continue
                if current_price > 0 and abs(c["price"] - c2["price"]) / current_price < 0.01:
                    cluster.append(c2)
                    used.add(j)
            avg_price = sum(x["price"] for x in cluster) / len(cluster)
            clusters.append({
                "price": avg_price,
                "confluence": len(cluster),
                "weight": sum(x["weight"] for x in cluster),
                "sources": [x["source"] for x in cluster],
            })

        clusters.sort(key=lambda x: x["weight"], reverse=True)

        zones: List[Dict[str, Any]] = []

        nearest = min(clusters, key=lambda x: abs(x["price"] - current_price))
        if current_price > 0 and abs(nearest["price"] - current_price) / current_price < 0.01:
            zones.append({
                "price": round(nearest["price"], 8),
                "allocation": 0.50,
                "type": "aggressive",
                "sources": nearest["sources"],
                "confluence": nearest["confluence"],
            })
        else:
            zones.append({
                "price": round(current_price, 8),
                "allocation": 0.40,
                "type": "market",
                "sources": ["market"],
                "confluence": 0,
            })

        deeper = [c for c in clusters if c["price"] < zones[0]["price"] * 0.99]
        if deeper:
            zones.append({
                "price": round(deeper[0]["price"], 8),
                "allocation": 0.30,
                "type": "moderate",
                "sources": deeper[0]["sources"],
                "confluence": deeper[0]["confluence"],
            })
            if len(deeper) > 1:
                zones.append({
                    "price": round(deeper[1]["price"], 8),
                    "allocation": 0.20,
                    "type": "conservative",
                    "sources": deeper[1]["sources"],
                    "confluence": deeper[1]["confluence"],
                })

        total = sum(z["allocation"] for z in zones)
        if total > 0:
            for z in zones:
                z["allocation"] = round(z["allocation"] / total, 2)

        return zones

    # ──────────────────────────────────────────────────────────────────────────
    # OVERSOLD BOUNCE — контрарианский разворотный сигнал
    # ──────────────────────────────────────────────────────────────────────────

    def _check_volume_uptick(self, df: pd.DataFrame) -> bool:
        """Мягкая проверка объёма для разворотов: достаточно одной растущей свечи.

        В отличие от _check_volume (требует 1.3× avg), здесь смотрим лишь на тренд
        — хватит одного роста объёма из последних трёх свечей.
        """
        if len(df) < 5:
            return False
        vols = df["volume"].tail(5).values
        return any(float(vols[-i]) > float(vols[-i - 1]) for i in range(1, 4))

    def _check_oversold_bounce(
        self,
        df: pd.DataFrame,
        current_price: float,
        levels: Dict[str, List[Dict[str, Any]]],
        rsi_analysis: Dict[str, Any],
        candlestick_pattern: Optional[str],
        atr_value: Optional[float],
        ema_analysis: Optional[Dict[str, Any]],
        macd_analysis: Optional[Dict[str, Any]],
        breakout: Optional[Dict[str, Any]],
        timeframe: str,
        fibonacci: Optional[Dict[str, Any]] = None,
        liquidity_grab: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Контрарианский сигнал OVERSOLD_BOUNCE.

        Условия (все обязательны):
          1. RSI ≤ 22 (экстремальная перепроданность)
          2. Цена в пределах 2% от ближайшей поддержки снизу
          3. Поддержка НЕ была только что пробита вниз
          4. Хотя бы один confirmator: MACD-дивергенция, свечной паттерн или
             объём нарастает

        Базовая уверенность 0.68, каждый confirmator добавляет очки.
        """
        rsi_val = float(rsi_analysis.get("rsi") or 50)
        if rsi_val > 30:
            return None

        # Недавний пробой поддержки вниз → уровень сломан, не входим
        br = breakout or {}
        if (
            br.get("breakout")
            and br.get("level_type") == "support"
            and br.get("breakout_direction") == "DOWN"
        ):
            return None

        # Ищем поддержку в пределах 2% снизу
        support_levels = levels.get("support_levels", [])
        near_support: Optional[Dict[str, Any]] = None
        for sup in sorted(support_levels, key=lambda x: x["price"], reverse=True):
            if sup["price"] >= current_price:
                continue
            if (current_price - sup["price"]) / current_price <= 0.02:
                near_support = sup
                break

        if near_support is None:
            return None

        # Собираем очки подтверждения
        if rsi_val <= 15:
            conf = 0.72
        elif rsi_val <= 22:
            conf = 0.68
        else:
            conf = 0.62
        confirmators: List[str] = []

        macd = macd_analysis or {}
        if macd.get("divergence") == "BULLISH":
            conf += 0.10
            confirmators.append("MACD бычья дивергенция")

        if candlestick_pattern and any(
            x in candlestick_pattern
            for x in ["Hammer", "Doji", "Bullish Engulfing", "Morning Star"]
        ):
            conf += 0.08
            confirmators.append(f"Свеча: {candlestick_pattern}")

        if rsi_val < 15:
            conf += 0.05
            confirmators.append(f"RSI экстремально низкий ({rsi_val:.1f})")

        sup_strength = float(near_support.get("strength", 0))
        if sup_strength >= 0.7:
            conf += 0.05
            confirmators.append(f"Сильная поддержка (strength={sup_strength:.2f})")

        volume_uptick = self._check_volume_uptick(df)
        if volume_uptick:
            conf += 0.05
            confirmators.append("Объём нарастает")

        fib = fibonacci or {}
        if fib.get("in_golden_zone") and fib.get("trend") == "BULLISH":
            conf += 0.08
            confirmators.append("Fibonacci Golden Zone")

        if liquidity_grab and liquidity_grab.get("type") == "BULLISH":
            conf += 0.12
            confirmators.append("Liquidity Grab (свип ликвидности)")

        # Нужно минимум 2 подтвердителя для надёжного разворота
        if len(confirmators) < 2:
            return None

        conf = min(conf, 1.0)
        if conf < self.min_confidence:
            return None

        logger.info(
            "OVERSOLD_BOUNCE %s/%s: RSI=%.1f у поддержки %.4f | conf=%.2f | %s",
            "?", timeframe, rsi_val, near_support["price"], conf,
            ", ".join(confirmators),
        )

        return self._create_bounce_signal(
            df=df,
            current_price=current_price,
            confidence=conf,
            confirmators=confirmators,
            near_support=near_support,
            levels=levels,
            rsi_analysis=rsi_analysis,
            volume_uptick=volume_uptick,
            atr_value=atr_value,
            ema_analysis=ema_analysis,
            macd_analysis=macd_analysis,
            timeframe=timeframe,
        )

    def _create_bounce_signal(
        self,
        df: pd.DataFrame,
        current_price: float,
        confidence: float,
        confirmators: List[str],
        near_support: Dict[str, Any],
        levels: Dict[str, List[Dict[str, Any]]],
        rsi_analysis: Dict[str, Any],
        volume_uptick: bool,
        atr_value: Optional[float],
        ema_analysis: Optional[Dict[str, Any]],
        macd_analysis: Optional[Dict[str, Any]],
        timeframe: str,
    ) -> Optional[Dict[str, Any]]:
        """Создаёт контрарианский BUY-сигнал с пометкой OVERSOLD_BOUNCE."""
        support_levels = levels.get("support_levels", [])
        resistance_levels = levels.get("resistance_levels", [])

        entry_price = current_price
        atr = atr_value or current_price * 0.02

        # Жёсткий стоп — чуть ниже поддержки (ATR × 0.5, а не 1.5 как у BUY)
        stop_loss = near_support["price"] - atr * 0.5

        max_tp_pct = self._MAX_TP_PCT.get(timeframe, 0.20)
        tp_max = current_price * (1 + max_tp_pct)

        take_profit: List[Dict[str, Any]] = []
        for i, res in enumerate(sorted(resistance_levels, key=lambda x: x["price"])[:5]):
            if current_price < res["price"] <= tp_max:
                take_profit.append({
                    "level": res["price"],
                    "probability": max(0.1, 0.6 - i * 0.15),
                })

        # ATR-based bounce targets — resistance может быть слишком близко
        if atr > 0:
            for mult, prob in [(2.0, 0.45), (3.0, 0.30), (4.0, 0.20)]:
                atr_target = entry_price + atr * mult
                if current_price < atr_target <= tp_max:
                    take_profit.append({"level": round(atr_target, 8), "probability": prob})

        filtered_tp = [tp for tp in take_profit if tp.get("level", 0) > current_price]
        # Отбрасываем слишком близкие цели (R/R < 1) — TP1 не должен быть убыточным
        filtered_tp = self._filter_tp_by_rr(entry_price, stop_loss, filtered_tp, "BUY", min_rr=1.0)
        if not filtered_tp:
            filtered_tp = [{"level": min(current_price * 1.05, tp_max), "probability": 0.5}]

        # Для разворотных сигналов требуем R/R ≥ 1.5 (менее жёстко, чем 2.0 для тренда)
        if not self._is_risk_reward_acceptable(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=filtered_tp,
            signal_type="BUY",
            min_rr=1.5,
        ):
            best_tp = filtered_tp[0]["level"] if filtered_tp else None
            risk = entry_price - stop_loss
            rr = (best_tp - entry_price) / risk if best_tp and risk > 0 else 0
            logger.info(
                "OVERSOLD_BOUNCE отброшен (R/R=%.2f < 1.5): entry=%.4f SL=%.4f TP=%.4f",
                rr, entry_price, stop_loss, best_tp or 0,
            )
            return None

        strength = "STRONG" if confidence >= 0.85 else "MEDIUM" if confidence >= 0.70 else "WEAK"

        primary_tp = next(
            (tp["level"] for tp in filtered_tp if isinstance(tp.get("level"), (int, float))),
            None,
        )
        test_trade = None
        risk = entry_price - stop_loss
        if primary_tp is not None and risk > 0:
            qty = self.test_risk_usd / risk
            expected_rr = (primary_tp - entry_price) / risk
            test_trade = {
                "risk_usd": self.test_risk_usd,
                "qty": qty,
                "entry_value_usd": qty * entry_price,
                "expected_rr": expected_rr,
            }

        ema = ema_analysis or {}
        macd = macd_analysis or {}

        ns_below = max(
            (s for s in support_levels if s["price"] < current_price),
            key=lambda x: x["price"],
            default=None,
        )
        nr_above = min(
            (r for r in resistance_levels if r["price"] > current_price),
            key=lambda x: x["price"],
            default=None,
        )

        return {
            "signal_type": "BUY",
            "signal_label": "OVERSOLD_BOUNCE",
            "bounce_mode": True,
            "warning": "⚠️ Контрарианский сигнал: разворот в медвежьем тренде. Строгий стоп.",
            "bounce_confirmators": confirmators,
            "strength": strength,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": filtered_tp,
            "indicators": {
                "candlestick_pattern": None,
                "support_level": float(ns_below["price"]) if ns_below else None,
                "resistance_level": float(nr_above["price"]) if nr_above else None,
                "volume_confirmation": volume_uptick,
                "head_shoulders": False,
                "rsi": rsi_analysis.get("rsi"),
                "rsi_zone": rsi_analysis.get("rsi_zone"),
                "rsi_signal": rsi_analysis.get("rsi_signal"),
                "atr": atr_value,
                "ema_trend": ema.get("trend"),
                "ema_cross": ema.get("ema_cross"),
                "ema9": ema.get("ema9"),
                "ema21": ema.get("ema21"),
                "ema50": ema.get("ema50"),
                "macd_signal": macd.get("macd_signal"),
                "macd_divergence": macd.get("divergence"),
                "macd_cross": macd.get("bullish_cross"),
            },
            "confidence": confidence,
            "test_trade": test_trade,
        }

    def _check_pullback_entry(
        self,
        df: pd.DataFrame,
        current_price: float,
        levels: Dict[str, List[Dict[str, Any]]],
        rsi_analysis: Dict[str, Any],
        candlestick_pattern: Optional[str],
        atr_value: Optional[float] = None,
        ema_analysis: Optional[Dict[str, Any]] = None,
        macd_analysis: Optional[Dict[str, Any]] = None,
        timeframe: str = "",
        fibonacci: Optional[Dict[str, Any]] = None,
        volume_profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Трендовый вход на откате (TREND_PULLBACK).

        В растущем рынке основной скорер рубит BUY по RSI>70 (вершина),
        поэтому ловим НЕ вершину, а откат к динамической поддержке в
        подтверждённом аптренде.

        Условия (все обязательны):
          1. Аптренд: EMA trend BULLISH (цена выше EMA50) и ema_cross BULLISH.
          2. Подтверждение структурой: BOS вверх или структура BULLISH.
          3. Откат, не вершина: RSI остыл в зону 40–62
             (не перекуплен = не вершина, не <40 = не слом тренда).
          4. Цена у динамической поддержки: ≤1.5% от EMA21/EMA50,
             либо Fib Golden Zone, либо ≤2% от уровня поддержки.
        """
        ema = ema_analysis or {}
        if ema.get("trend") != "BULLISH" or ema.get("ema_cross") != "BULLISH":
            return None

        rsi_val = float(rsi_analysis.get("rsi") or 50)
        if not (40.0 <= rsi_val <= 62.0):
            return None

        ms = detect_structure(df)
        bos_up = bool(ms.get("bos") and ms["bos"]["type"] == "BULLISH")
        struct_bull = ms.get("structure") == "BULLISH"
        if not (bos_up or struct_bull):
            return None

        # Цена должна быть у динамической поддержки (откат завершается)
        ema21 = ema.get("ema21")
        ema50 = ema.get("ema50")
        near_dynamic = False
        zone_label = ""
        for label, lvl in (("EMA21", ema21), ("EMA50", ema50)):
            if lvl and abs(current_price - lvl) / current_price <= 0.015:
                near_dynamic = True
                zone_label = label
                break

        fib = fibonacci or {}
        in_golden = bool(fib.get("in_golden_zone") and fib.get("trend") == "BULLISH")

        supports = [s for s in levels.get("support_levels", []) if s["price"] < current_price]
        near_support = max(supports, key=lambda x: x["price"], default=None)
        near_level = (
            near_support is not None
            and (current_price - near_support["price"]) / current_price <= 0.02
        )

        if not (near_dynamic or in_golden or near_level):
            return None

        conf = 0.68
        factors: List[str] = ["Аптренд: EMA50 бычий + EMA9/21 бычье пересечение"]
        if bos_up:
            conf += 0.08
            factors.append("BOS вверх — продолжение тренда")
        if struct_bull:
            conf += 0.05
            factors.append(
                f"Структура BULLISH ({float(ms.get('trend_strength') or 0) * 100:.0f}%)"
            )
        if near_dynamic:
            conf += 0.08
            factors.append(f"Откат к {zone_label} (динамическая поддержка)")
        if in_golden:
            conf += 0.08
            factors.append("Fibonacci Golden Zone (0.618–0.786)")
        if near_level:
            conf += 0.05
            factors.append(f"У поддержки {near_support['price']:.4f}")
        macd = macd_analysis or {}
        if macd.get("macd_signal") == "BUY":
            conf += 0.05
            factors.append("MACD бычий импульс")
        if candlestick_pattern and any(
            x in candlestick_pattern
            for x in ["Hammer", "Bullish Engulfing", "Morning Star", "Doji"]
        ):
            conf += 0.05
            factors.append(f"Свеча: {candlestick_pattern}")
        if self._check_volume_uptick(df):
            conf += 0.04
            factors.append("Объём нарастает")

        # Базовый тренд + минимум 2 подтверждения (всего ≥3 фактора)
        if len(factors) < 3:
            return None

        conf = min(conf, 0.95)
        if conf < self.min_confidence:
            return None

        logger.info(
            "TREND_PULLBACK %s: RSI=%.1f откат в аптренде | conf=%.2f | %s",
            timeframe, rsi_val, conf, ", ".join(factors[1:]),
        )

        obs = detect_order_blocks(df)
        signal = self._create_buy_signal(
            df, current_price, conf, factors, levels,
            head_shoulders=None, volume_confirmation=self._check_volume(df),
            rsi_analysis=rsi_analysis, atr_value=atr_value,
            ema_analysis=ema_analysis, macd_analysis=macd_analysis,
            max_tp_pct=self._MAX_TP_PCT.get(timeframe, 0.20),
            vwap_data=self.vwap_calculator.analyze(df),
            volume_profile=volume_profile, fibonacci=fibonacci, order_blocks=obs,
        )
        if signal:
            signal["signal_label"] = "TREND_PULLBACK"
        return signal

    def _check_volume(self, df: pd.DataFrame) -> bool:
        """Проверяет подтверждение объёмом по трём условиям.

        1. Текущий объём ≥ 1.3× от 20-периодного среднего.
        2. Средний объём последних 3 свечей выше 20-периодного
           (устойчивость, а не случайный всплеск).
        3. Большой объём без движения цены — поглощение, не подтверждение:
           если объём > 3× avg, а свеча почти дoji (< 20% avg body) — игнорируем.
        """
        if len(df) < 20:
            return False

        tail20 = df.tail(20)
        current = df.iloc[-1]
        avg_vol_20 = float(tail20["volume"].mean())
        cur_vol = float(current["volume"])

        # Условие 1: объём должен быть значимо выше нормы
        if cur_vol < avg_vol_20 * 1.3:
            return False

        # Условие 2: последние 3 свечи тоже выше нормы
        avg_vol_3 = float(df["volume"].tail(3).mean())
        if avg_vol_3 < avg_vol_20:
            return False

        # Условие 3: огромный объём без движения — поглощение
        price_body = abs(float(current["close"]) - float(current["open"]))
        avg_body = float((tail20["close"] - tail20["open"]).abs().mean())
        if avg_body > 0 and price_body < avg_body * 0.2 and cur_vol > avg_vol_20 * 3:
            return False

        return True

