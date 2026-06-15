"""Тесты генератора сигналов."""
import pytest
import numpy as np
import pandas as pd
from src.analytics.signals.generator import SignalGenerator


class TestSignalGenerator:
    def test_returns_none_on_short_df(self, df_short):
        """Меньше 100 свечей → None."""
        gen = SignalGenerator(min_confidence=0.7)
        result = gen.generate_signal("ETH", "4h", df_short)
        assert result is None

    def test_signal_structure(self, df_up):
        """Если сигнал есть — у него правильная структура."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None:
            pytest.skip("Сигнал не сгенерирован на данном датасете")
        assert "signal_type" in result
        assert result["signal_type"] in ("BUY", "SELL")
        assert "strength" in result
        assert result["strength"] in ("WEAK", "MEDIUM", "STRONG")
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0
        assert "entry_price" in result
        assert "stop_loss" in result
        assert "take_profit" in result

    def test_tp_probability_non_negative(self, df_up):
        """Вероятности TP не должны быть отрицательными."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None:
            pytest.skip("Нет сигнала")
        for tp in result.get("take_profit", []):
            assert tp["probability"] >= 0, f"Отрицательная вероятность TP: {tp}"

    def test_buy_signal_stop_loss_below_entry(self, df_up):
        """BUY: стоп-лосс должен быть ниже цены входа."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or result["signal_type"] != "BUY":
            pytest.skip("Нет BUY сигнала")
        assert result["stop_loss"] < result["entry_price"]

    def test_sell_signal_stop_loss_above_entry(self, df_down):
        """SELL: стоп-лосс должен быть выше цены входа."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_down)
        if result is None or result["signal_type"] != "SELL":
            pytest.skip("Нет SELL сигнала")
        assert result["stop_loss"] > result["entry_price"]

    def test_buy_tp_above_entry(self, df_up):
        """BUY: все TP выше цены входа."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or result["signal_type"] != "BUY":
            pytest.skip("Нет BUY сигнала")
        for tp in result["take_profit"]:
            assert tp["level"] > result["entry_price"], \
                f"TP {tp['level']} ниже entry {result['entry_price']}"

    def test_sell_tp_below_entry(self, df_down):
        """SELL: все TP ниже цены входа."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_down)
        if result is None or result["signal_type"] != "SELL":
            pytest.skip("Нет SELL сигнала")
        for tp in result["take_profit"]:
            assert tp["level"] < result["entry_price"], \
                f"TP {tp['level']} выше entry {result['entry_price']}"

    def test_min_confidence_filter(self, df_up):
        """high min_confidence → меньше сигналов."""
        gen_low = SignalGenerator(min_confidence=0.3)
        gen_high = SignalGenerator(min_confidence=0.99)
        sig_low = gen_low.generate_signal("ETH", "4h", df_up)
        sig_high = gen_high.generate_signal("ETH", "4h", df_up)
        # При высоком пороге — либо нет сигнала, либо confidence ≥ 0.99
        if sig_high is not None:
            assert sig_high["confidence"] >= 0.99

    def test_indicators_in_signal(self, df_up):
        """Поле indicators должно содержать ключевые данные."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "1d", df_up)
        if result is None:
            pytest.skip("Нет сигнала")
        ind = result.get("indicators", {})
        assert "rsi" in ind
        assert "ema_trend" in ind
        assert "macd_signal" in ind

    def test_risk_reward_at_least_2(self, df_up):
        """R/R к первому TP должен быть ≥ 2.0 (проверка _is_risk_reward_acceptable)."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or not result.get("take_profit"):
            pytest.skip("Нет сигнала или TP")

        entry = result["entry_price"]
        sl = result["stop_loss"]
        tp1 = result["take_profit"][0]["level"]
        sig_type = result["signal_type"]

        if sig_type == "BUY":
            risk = entry - sl
            reward = tp1 - entry
        else:
            risk = sl - entry
            reward = entry - tp1

        if risk > 0 and reward > 0:
            assert reward / risk >= 2.0, f"R/R = {reward/risk:.2f} < 2.0"


class TestTPSorting:
    """Проверяем что TP уровни отсортированы по близости к цене."""

    def test_buy_tp_ascending(self, df_up):
        """BUY: TP в порядке возрастания (ближайший первый)."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or result["signal_type"] != "BUY":
            pytest.skip("Нет BUY сигнала")
        tp_levels = [tp["level"] for tp in result["take_profit"]]
        assert tp_levels == sorted(tp_levels), f"TP не отсортированы: {tp_levels}"

    def test_sell_tp_descending(self, df_down):
        """SELL: TP в порядке убывания (ближайший первый)."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_down)
        if result is None or result["signal_type"] != "SELL":
            pytest.skip("Нет SELL сигнала")
        tp_levels = [tp["level"] for tp in result["take_profit"]]
        assert tp_levels == sorted(tp_levels, reverse=True), f"TP не отсортированы: {tp_levels}"
