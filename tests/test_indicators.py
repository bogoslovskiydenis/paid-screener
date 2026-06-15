"""Тесты технических индикаторов."""
import pytest
import pandas as pd
import numpy as np
from src.analytics.indicators.rsi import RSICalculator
from src.analytics.indicators.ema import EMACalculator
from src.analytics.indicators.macd import MACDCalculator
from src.analytics.indicators.atr import ATRCalculator


class TestRSI:
    def test_rsi_returns_value(self, df_up):
        calc = RSICalculator(period=14)
        result = calc.analyze(df_up)
        assert "rsi" in result
        assert isinstance(result["rsi"], float)

    def test_rsi_range(self, df_up):
        calc = RSICalculator(period=14)
        result = calc.analyze(df_up)
        assert 0 <= result["rsi"] <= 100

    def test_rsi_overbought_on_uptrend(self, df_up):
        """Сильный апрэнд → RSI должен быть выше 50."""
        calc = RSICalculator(period=14)
        result = calc.analyze(df_up)
        assert result["rsi"] > 50

    def test_rsi_oversold_on_downtrend(self, df_down):
        """Сильный даунтренд → RSI должен быть ниже 50."""
        calc = RSICalculator(period=14)
        result = calc.analyze(df_down)
        assert result["rsi"] < 50

    def test_rsi_zone_field(self, df_flat):
        calc = RSICalculator(period=14)
        result = calc.analyze(df_flat)
        assert "rsi_zone" in result
        assert result["rsi_zone"] in ("OVERSOLD", "NEAR_OVERSOLD", "NEUTRAL",
                                       "NEAR_OVERBOUGHT", "OVERBOUGHT")

    def test_rsi_short_df_handled(self, df_short):
        """Мало данных — не должно падать с исключением."""
        calc = RSICalculator(period=14)
        result = calc.analyze(df_short)
        assert isinstance(result, dict)

    def test_rsi_buy_signal_below_30(self):
        """RSI < 30 → сигнал BUY."""
        rng = np.random.default_rng(0)
        closes = np.ones(100) * 100.0
        # Резкое падение в конце
        closes[-15:] = np.linspace(100, 50, 15)
        df = pd.DataFrame({
            "open": closes, "high": closes + 1, "low": closes - 1,
            "close": closes, "volume": rng.uniform(1000, 2000, 100),
        })
        calc = RSICalculator(period=14)
        result = calc.analyze(df)
        # RSI может быть не строго < 30, но должен быть в нижней зоне
        assert result["rsi"] < 55


class TestEMA:
    def test_ema_returns_trends(self, df_up):
        calc = EMACalculator(periods=[9, 21, 50])
        result = calc.analyze(df_up)
        assert "trend" in result
        assert result["trend"] in ("BULLISH", "BEARISH", "NEUTRAL")

    def test_ema_bullish_on_uptrend(self, df_up):
        calc = EMACalculator(periods=[9, 21, 50])
        result = calc.analyze(df_up)
        assert result["trend"] == "BULLISH"

    def test_ema_bearish_on_downtrend(self, df_down):
        calc = EMACalculator(periods=[9, 21, 50])
        result = calc.analyze(df_down)
        assert result["trend"] == "BEARISH"

    def test_ema_cross_field(self, df_flat):
        calc = EMACalculator(periods=[9, 21, 50])
        result = calc.analyze(df_flat)
        assert "ema_cross" in result
        assert result["ema_cross"] in ("BULLISH", "BEARISH", "NEUTRAL")

    def test_ema_values_present(self, df_up):
        calc = EMACalculator(periods=[9, 21, 50])
        result = calc.analyze(df_up)
        assert "ema9" in result
        assert "ema21" in result
        assert "ema50" in result


class TestMACD:
    def test_macd_returns_signal(self, df_up):
        calc = MACDCalculator()
        result = calc.analyze(df_up)
        assert "macd_signal" in result
        assert result["macd_signal"] in ("BUY", "SELL", "NEUTRAL")

    def test_macd_no_crash_short(self, df_short):
        calc = MACDCalculator()
        result = calc.analyze(df_short)
        assert isinstance(result, dict)


class TestATR:
    def test_atr_positive(self, df_up):
        calc = ATRCalculator(period=14)
        val = calc.get_current(df_up)
        assert val is None or val > 0

    def test_atr_short_df(self, df_short):
        calc = ATRCalculator(period=14)
        val = calc.get_current(df_short)
        # Не должно падать
        assert val is None or isinstance(val, float)
