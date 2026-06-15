"""Детектор накопления позиций на дневном таймфрейме (Wyckoff-подобный)."""
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


class AccumulationDetector:
    """
    Ищет признаки того, что кит тихо накапливает позицию.

    Базовое условие (обязательно): цена в боковике N свечей (±threshold%).
    Это отсекает монеты в активном тренде — накопление идёт в зоне.

    Оценочные сигналы:
      1. Абсорбция   — большой объём + маленькое движение цены (кит выкупает продавцов)
      2. Высокие низы — каждый дип откупается выше предыдущего (поддержка крепнет)
      3. Объём вверх > Объём вниз — покупатели давят сильнее продавцов
      4. BB сжимается — волатильность падает, энергия копится
      5. RSI в зоне 35–55 — не перекуплено, есть куда расти
    """

    def __init__(
        self,
        sideways_period: int = 20,
        sideways_threshold: float = 0.10,
        absorption_vol_mult: float = 1.5,
        min_score: float = 0.55,
        rsi_min: float = 30.0,
        rsi_max: float = 60.0,
    ):
        self.sideways_period = sideways_period
        self.sideways_threshold = sideways_threshold
        self.absorption_vol_mult = absorption_vol_mult
        self.min_score = min_score
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max

    def detect(self, asset: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        min_len = self.sideways_period + 20
        if len(df) < min_len:
            return None

        closes = df["close"].values
        opens = df["open"].values
        highs = df["high"].values
        lows = df["low"].values
        volumes = df["volume"].values

        # --- Обязательное условие: боковик ---
        window_closes = closes[-self.sideways_period:]
        price_max = float(np.max(window_closes))
        price_min = float(np.min(window_closes))
        price_range_pct = (price_max - price_min) / price_min if price_min > 0 else 1.0

        if price_range_pct > self.sideways_threshold:
            return None  # монета в тренде, не в накоплении

        # --- RSI: должен быть в зоне накопления ---
        rsi = self._calc_rsi(df, 14)
        if rsi is None or not (self.rsi_min <= rsi <= self.rsi_max):
            return None

        score = 0.0
        signals: List[str] = []
        cur_price = float(closes[-1])

        # --- Сигнал 1: Абсорбционные свечи ---
        avg_vol = float(np.mean(volumes[-self.sideways_period - 1:-1]))
        window_vols = volumes[-self.sideways_period:]
        window_opens = opens[-self.sideways_period:]
        window_closes_arr = closes[-self.sideways_period:]
        window_highs = highs[-self.sideways_period:]
        window_lows = lows[-self.sideways_period:]

        absorption_count = 0
        for i in range(len(window_vols)):
            vol = float(window_vols[i])
            candle_range = float(window_highs[i]) - float(window_lows[i])
            candle_body = abs(float(window_closes_arr[i]) - float(window_opens[i]))
            if vol > avg_vol * self.absorption_vol_mult and candle_range > 0:
                body_ratio = candle_body / candle_range
                if body_ratio < 0.35:  # большой объём + маленькое тело
                    absorption_count += 1

        if absorption_count >= 2:
            score += 0.25
            signals.append(f"Абсорбция: {absorption_count} свечей (объём ×{self.absorption_vol_mult:.1f}+, тело <35%)")
        elif absorption_count == 1:
            score += 0.10
            signals.append("Абсорбция: 1 свеча")

        # --- Сигнал 2: Высокие низы (поддержка держится) ---
        half = self.sideways_period // 2
        lows_first_half = float(np.mean(window_lows[:half]))
        lows_second_half = float(np.mean(window_lows[half:]))
        if lows_second_half > lows_first_half * 1.002:
            score += 0.20
            signals.append(f"Низы растут ({lows_first_half:.4f} → {lows_second_half:.4f})")

        # --- Сигнал 3: Покупатели доминируют по объёму ---
        bull_vols, bear_vols = [], []
        for i in range(len(window_vols)):
            v = float(window_vols[i])
            if window_closes_arr[i] > window_opens[i]:
                bull_vols.append(v)
            else:
                bear_vols.append(v)

        if bull_vols and bear_vols:
            avg_bull = float(np.mean(bull_vols))
            avg_bear = float(np.mean(bear_vols))
            if avg_bull > avg_bear * 1.3:
                score += 0.20
                signals.append(f"Объём покупок > продаж ({avg_bull / avg_bear:.1f}x)")

        # --- Сигнал 4: BB сужается ---
        bb_squeeze = self._bb_squeezing(df)
        if bb_squeeze:
            score += 0.15
            signals.append("BB сжимается (энергия копится)")

        # --- Сигнал 5: RSI в идеальной зоне накопления 38–55 ---
        if 38 <= rsi <= 55:
            score += 0.10
            signals.append(f"RSI в зоне накопления ({rsi:.1f})")

        # --- Бонус: объём последних свечей нарастает ---
        recent_vols = volumes[-5:]
        if all(recent_vols[i] <= recent_vols[i + 1] for i in range(len(recent_vols) - 1)):
            score += 0.10
            signals.append("Объём нарастает последние 5 свечей")

        # --- ICT: Ликвидити свип и FVG ---
        if _ICT_AVAILABLE:
            # Бычий свип низов диапазона → ликвидность собрана, откуп подтверждён
            sweeps = detect_liquidity_sweep(
                df, lookback=self.sideways_period, recent_candles=7
            )
            bull_sw = [s for s in sweeps if s["type"] == "BULLISH" and s["candles_ago"] <= 7]
            if bull_sw:
                sw = bull_sw[0]
                score += 0.15
                signals.append(f"Ликвидити свип низов (wick -{sw['wick_pct']:.1f}%, откуп)")

            # Незакрытый бычий FVG внутри или у нижней границы зоны накопления
            fvgs = detect_fvg(df, max_lookback=self.sideways_period)
            zone_fvgs = [
                f for f in fvgs
                if f["type"] == "BULLISH" and not f["filled"]
                and f["gap_low"] >= price_min * 0.95
                and f["gap_high"] <= price_max * 1.05
            ]
            if zone_fvgs:
                fv = zone_fvgs[0]
                score += 0.10
                signals.append(f"FVG дисбаланс {_fp(fv['gap_low'])}–{_fp(fv['gap_high'])}")

        if score < self.min_score or len(signals) < 2:
            return None

        strength = "STRONG" if score >= 0.75 else "MEDIUM" if score >= 0.60 else "WEAK"

        logger.info(
            "📦 ACCUMULATION [%s]: score=%.2f (%s) | диапазон=%.1f%% | RSI=%.1f | %s",
            asset, score, strength, price_range_pct * 100, rsi, " | ".join(signals),
        )

        return {
            "asset": asset,
            "signal_type": "ACCUMULATION",
            "strength": strength,
            "confidence": min(round(score, 3), 1.0),
            "current_price": cur_price,
            "sideways_range_pct": round(price_range_pct * 100, 2),
            "zone_low": round(price_min, 6),
            "zone_high": round(price_max, 6),
            "rsi": round(rsi, 1),
            "absorption_candles": absorption_count,
            "signals": signals,
        }

    def _bb_squeezing(self, df: pd.DataFrame, period: int = 20, lookback: int = 10) -> bool:
        """BB ширина уменьшается последние lookback свечей."""
        if len(df) < period + lookback:
            return False
        closes = df["close"]
        bw_values = []
        for i in range(lookback, 0, -1):
            w = closes.iloc[-(period + i):-(i)]
            if len(w) < period:
                continue
            m = float(w.mean())
            s = float(w.std())
            bw_values.append((2 * s) / m if m > 0 else 0)
        if len(bw_values) < 3:
            return False
        # ширина в целом снижается (линейный тренд убывает)
        trend = np.polyfit(range(len(bw_values)), bw_values, 1)[0]
        return trend < 0

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
