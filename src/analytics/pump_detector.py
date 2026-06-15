"""Двухфазный детектор пампа: накопление → прорыв EMA9."""
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List

try:
    from ..utils.logger import setup_logger
except ImportError:
    import logging
    def setup_logger(name: str):  # type: ignore[misc]
        return logging.getLogger(name)

try:
    from .ict_detector import detect_fvg, detect_liquidity_sweep
    _ICT_AVAILABLE = True
except ImportError:
    _ICT_AVAILABLE = False

try:
    from ..utils.format_price import fmt_price as _fp
except ImportError:
    def _fp(x: float) -> str:  # type: ignore[misc]
        return f"{x:.4f}"

logger = setup_logger(__name__)


class AltPumpDetector:
    """
    Ловит памп в начале движения, а не после него.

    Фаза 1 — Накопление (до текущей свечи):
      - BB Squeeze: полосы сжались до минимума за N периодов (энергия копится)
      - Нарастающий объём: 2+ свечи подряд выше среднего (умные деньги заходят)

    Фаза 2 — Прорыв (текущая свеча):
      - Свежий пробой EMA9: большинство последних 5 свечей были ниже, сейчас выше
      - Объём прорыва >= 2× средний (реальный интерес)
      - Сильная бычья свеча: тело >= 50% диапазона

    Hard-фильтры (блокируют сигнал полностью):
      - RSI > 72: движение уже перегрето, входить поздно
      - RSI < 35: нисходящий тренд, не накопление

    Для сигнала обязательно: фаза 1 >= 1 критерий И фаза 2 >= 1 критерий.
    """

    def __init__(
        self,
        rsi_max: float = 72.0,
        rsi_min: float = 35.0,
        bb_period: int = 20,
        squeeze_lookback: int = 30,
        ema_cross_lookback: int = 5,
        volume_multiplier: float = 2.0,
        min_score: float = 0.50,
    ):
        self.rsi_max = rsi_max
        self.rsi_min = rsi_min
        self.bb_period = bb_period
        self.squeeze_lookback = squeeze_lookback
        self.ema_cross_lookback = ema_cross_lookback
        self.volume_multiplier = volume_multiplier
        self.min_score = min_score

    def detect(self, asset: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if len(df) < self.bb_period + self.squeeze_lookback + 5:
            return None

        closes = df["close"].values
        volumes = df["volume"].values
        cur_close = float(closes[-1])
        cur_open = float(df["open"].values[-1])
        cur_high = float(df["high"].values[-1])
        cur_low = float(df["low"].values[-1])

        # --- Hard фильтры по RSI ---
        rsi = self._calc_rsi(df, 14)
        if rsi is None or not (self.rsi_min <= rsi <= self.rsi_max):
            return None

        # --- EMA9 ---
        ema9 = df["close"].ewm(span=9, adjust=False).mean().values

        # --- Обязательное условие: свежий пробой EMA9 ---
        # Цена сейчас выше EMA9
        if cur_close <= ema9[-1]:
            return None
        # Большинство из последних N свечей были ниже (новое движение, не продолжение)
        prev_below = sum(
            1 for i in range(1, self.ema_cross_lookback + 1)
            if closes[-1 - i] < ema9[-1 - i]
        )
        if prev_below < 3:  # уже давно выше EMA9 — не начало движения
            return None

        # --- Фаза 1: Накопление ---
        p1_score = 0.0
        p1_signals: List[str] = []

        squeeze, bw_ratio = self._detect_bb_squeeze(df)
        if squeeze:
            p1_score += 0.30
            p1_signals.append(f"BB сжатие ({bw_ratio:.0%} от максимума)")

        if self._volume_building(volumes):
            p1_score += 0.20
            p1_signals.append("Объём нарастает 2+ свечи подряд")

        # --- Фаза 2: Прорыв ---
        p2_score = 0.0
        p2_signals: List[str] = []

        avg_vol = float(np.mean(volumes[-(self.bb_period + 1):-1]))
        cur_vol = float(volumes[-1])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0
        if vol_ratio >= self.volume_multiplier:
            p2_score += 0.25
            p2_signals.append(f"Объём прорыва {vol_ratio:.1f}x")

        candle_range = cur_high - cur_low
        candle_body = cur_close - cur_open
        if candle_range > 0 and candle_body > 0:
            body_ratio = candle_body / candle_range
            if body_ratio >= 0.50:
                p2_score += 0.15
                p2_signals.append(f"Сильная свеча (тело {body_ratio:.0%})")

        # Бонус: RSI в идеальной зоне 48–65
        if 48 <= rsi <= 65:
            p2_score += 0.10
            p2_signals.append(f"RSI в зоне импульса ({rsi:.1f})")

        # EMA9 выше EMA21 — краткосрочный бычий контекст
        ema21 = df["close"].ewm(span=21, adjust=False).mean().values
        if ema9[-1] > ema21[-1]:
            p2_score += 0.05
            p2_signals.append("EMA9 > EMA21")

        # --- ICT: Ликвидити свип и FVG ---
        if _ICT_AVAILABLE:
            # Бычий свип последних 5 свечей → стопы сметены, цена развернулась вверх
            sweeps = detect_liquidity_sweep(df, lookback=30, recent_candles=5)
            bull_sw = [s for s in sweeps if s["type"] == "BULLISH" and s["candles_ago"] <= 3]
            if bull_sw:
                sw = bull_sw[0]
                p1_score += 0.10
                p1_signals.append(f"Ликвидити свип (wick -{sw['wick_pct']:.1f}%)")

            fvgs = detect_fvg(df, max_lookback=30)
            # Незакрытый бычий FVG ниже цены → поддержка/дисбаланс
            support_fvg = sorted(
                [f for f in fvgs if f["type"] == "BULLISH" and not f["filled"]
                 and f["gap_high"] < cur_close],
                key=lambda x: -x["gap_high"],
            )
            if support_fvg:
                fv = support_fvg[0]
                p1_score += 0.05
                p1_signals.append(f"FVG поддержка {_fp(fv['gap_low'])}–{_fp(fv['gap_high'])}")

            # Незакрытый бычий FVG выше цены → ближайшая цель (магнит)
            target_fvg = sorted(
                [f for f in fvgs if f["type"] == "BULLISH" and not f["filled"]
                 and f["gap_low"] > cur_close],
                key=lambda x: x["gap_low"],
            )
            if target_fvg:
                fv = target_fvg[0]
                p2_score += 0.05
                p2_signals.append(f"FVG цель {_fp(fv['gap_low'])}–{_fp(fv['gap_high'])}")

        # --- Оба блока должны что-то дать ---
        if p1_score == 0 or p2_score == 0:
            return None

        total = p1_score + p2_score
        all_signals = p1_signals + p2_signals

        if total < self.min_score or len(all_signals) < 2:
            return None

        strength = "STRONG" if total >= 0.80 else "MEDIUM" if total >= 0.65 else "WEAK"

        logger.info(
            "🎯 PUMP [%s]: score=%.2f (%s) | RSI=%.1f | p1=%s | p2=%s",
            asset, total, strength, rsi,
            " + ".join(p1_signals) or "—",
            " + ".join(p2_signals) or "—",
        )

        return {
            "asset": asset,
            "signal_type": "PUMP",
            "strength": strength,
            "confidence": min(round(total, 3), 1.0),
            "current_price": cur_close,
            "volume_ratio": round(vol_ratio, 2),
            "rsi": round(rsi, 1),
            "phase1": p1_signals,
            "phase2": p2_signals,
            "signals": all_signals,
        }

    def detect_setup(self, asset: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Детектор PRE_PUMP: обнаруживает setup ДО пробоя.

        Ловит момент, когда энергия уже накопилась и прорыв вот-вот произойдёт:
        1. BB Squeeze у минимума — полосы сжаты максимально
        2. OBV растёт при плоской цене — покупатели копят позицию
        3. EMA9 подходит к EMA21 снизу — пересечение на подходе
        4. Цена прижата к верхней части диапазона — давит вверх
        5. Объём подсыхает → начинает оживать (тихо → smart money)
        """
        if len(df) < self.bb_period + self.squeeze_lookback + 10:
            return None

        closes = df["close"].values
        volumes = df["volume"].values
        cur_close = float(closes[-1])

        rsi = self._calc_rsi(df, 14)
        if rsi is None or rsi > 65 or rsi < 30:
            return None

        ema9 = df["close"].ewm(span=9, adjust=False).mean().values
        ema21 = df["close"].ewm(span=21, adjust=False).mean().values

        # Если уже пробил EMA9 и давно выше — это уже движение, не setup
        if cur_close > ema9[-1] * 1.02:
            return None

        score = 0.0
        signals: List[str] = []

        # 1. BB Squeeze — полосы у минимума
        squeeze, bw_ratio = self._detect_bb_squeeze(df)
        if squeeze:
            score += 0.30
            signals.append(f"BB сжатие ({bw_ratio:.0%} от макс.)")

        # 2. OBV растёт при плоской цене (покупатели копят)
        obv_div = self._obv_divergence(df, lookback=15)
        if obv_div:
            score += 0.25
            signals.append("OBV растёт, цена плоская (скрытые покупки)")

        # 3. EMA9 приближается к EMA21 снизу — пересечение близко
        ema_gap = (ema9[-1] - ema21[-1]) / ema21[-1] if ema21[-1] > 0 else 0
        ema_gap_prev = (ema9[-3] - ema21[-3]) / ema21[-3] if ema21[-3] > 0 else 0
        if -0.015 < ema_gap < 0.005 and ema_gap > ema_gap_prev:
            score += 0.15
            signals.append(f"EMA9→EMA21 сближение ({ema_gap:+.2%})")

        # 4. Цена у верхней границы диапазона — давит вверх
        recent_high = float(max(df["high"].values[-self.squeeze_lookback:]))
        recent_low = float(min(df["low"].values[-self.squeeze_lookback:]))
        if recent_high > recent_low:
            position = (cur_close - recent_low) / (recent_high - recent_low)
            if position > 0.70:
                score += 0.10
                signals.append(f"Цена у потолка диапазона ({position:.0%})")

        # 5. Объём: сначала подсох, теперь оживает
        vol_pattern = self._volume_drying_then_ticking(volumes)
        if vol_pattern:
            score += 0.15
            signals.append("Объём: просушка → оживление")

        # 6. RSI в зоне импульса 45-60
        if 45 <= rsi <= 60:
            score += 0.05
            signals.append(f"RSI готов к импульсу ({rsi:.1f})")

        if score < self.min_score or len(signals) < 2:
            return None

        strength = "STRONG" if score >= 0.75 else "MEDIUM" if score >= 0.60 else "WEAK"

        avg_vol = float(np.mean(volumes[-(self.bb_period + 1):-1]))
        cur_vol = float(volumes[-1])
        vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0

        logger.info(
            "⚡ PRE_PUMP [%s]: score=%.2f (%s) | RSI=%.1f | vol=%.1fx | %s",
            asset, score, strength, rsi, vol_ratio, " + ".join(signals),
        )

        return {
            "asset": asset,
            "signal_type": "PRE_PUMP",
            "strength": strength,
            "confidence": min(round(score, 3), 1.0),
            "current_price": cur_close,
            "volume_ratio": round(vol_ratio, 2),
            "rsi": round(rsi, 1),
            "signals": signals,
        }

    def _obv_divergence(self, df: pd.DataFrame, lookback: int = 15) -> bool:
        """OBV растёт, а цена плоская — скрытые покупки."""
        if len(df) < lookback + 1:
            return False
        window = df.iloc[-lookback:]
        closes = window["close"].values
        volumes = window["volume"].values

        price_change = abs(float(closes[-1]) - float(closes[0])) / float(closes[0])
        if price_change > 0.05:
            return False

        obv = np.zeros(len(closes))
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv[i] = obv[i - 1] + volumes[i]
            elif closes[i] < closes[i - 1]:
                obv[i] = obv[i - 1] - volumes[i]
            else:
                obv[i] = obv[i - 1]

        half = len(obv) // 2
        obv_first = float(np.mean(obv[:half]))
        obv_second = float(np.mean(obv[half:]))
        return obv_second > obv_first * 1.1

    def _volume_drying_then_ticking(self, volumes: np.ndarray) -> bool:
        """Объём сначала падал (5-10 свечей), потом начал расти (2-3 свечи)."""
        if len(volumes) < 12:
            return False
        dry_phase = volumes[-10:-3]
        tick_phase = volumes[-3:]
        avg_dry = float(np.mean(dry_phase))
        avg_tick = float(np.mean(tick_phase))
        avg_before = float(np.mean(volumes[-20:-10])) if len(volumes) >= 20 else avg_dry * 1.5
        return avg_dry < avg_before * 0.8 and avg_tick > avg_dry * 1.2

    def _detect_bb_squeeze(self, df: pd.DataFrame) -> tuple:
        """BB Squeeze: текущая ширина полос у минимума за squeeze_lookback свечей."""
        period = self.bb_period
        lookback = self.squeeze_lookback
        closes = df["close"]

        bw_series: List[float] = []
        for i in range(lookback, 0, -1):
            window = closes.iloc[-(period + i):-(i)]
            if len(window) < period:
                continue
            mean = float(window.mean())
            std = float(window.std())
            bw_series.append((2 * std) / mean if mean > 0 else 0)

        if not bw_series:
            return False, 0.0

        current_window = closes.iloc[-(period + 1):-1]
        cur_mean = float(current_window.mean())
        cur_std = float(current_window.std())
        cur_bw = (2 * cur_std) / cur_mean if cur_mean > 0 else 0

        max_bw = max(bw_series) or 1e-9
        bw_ratio = cur_bw / max_bw
        is_squeeze = cur_bw <= float(np.percentile(bw_series, 25))

        return is_squeeze, bw_ratio

    def _volume_building(self, volumes: np.ndarray) -> bool:
        """2+ свечи подряд с нарастающим объёмом выше среднего (до прорывной свечи)."""
        if len(volumes) < 5:
            return False
        avg = float(np.mean(volumes[-21:-1]))
        pre = volumes[-4:-1]  # 3 свечи перед текущей
        building = all(pre[i] < pre[i + 1] for i in range(len(pre) - 1))
        above_avg = sum(1 for v in pre if float(v) > avg)
        return building and above_avg >= 2

    @staticmethod
    def _calc_rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        if len(df) < period + 1:
            return None
        delta = df["close"].diff().dropna()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        last_loss = float(loss.iloc[-1])
        if last_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + float(gain.iloc[-1]) / last_loss)
