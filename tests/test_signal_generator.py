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
        """Максимальный R/R по целям должен быть ≥ 2.0 (проверка _is_risk_reward_acceptable).

        TP1 после _cap_primary_exit — консервативный «план закрытия» и может
        давать R/R < 2, поэтому проверяем лучшую цель, а не первую.
        """
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or not result.get("take_profit"):
            pytest.skip("Нет сигнала или TP")

        entry = result["entry_price"]
        sl = result["stop_loss"]
        sig_type = result["signal_type"]

        rr_values = []
        for tp in result["take_profit"]:
            level = tp["level"]
            if sig_type == "BUY":
                risk = entry - sl
                reward = level - entry
            else:
                risk = sl - entry
                reward = entry - level
            if risk > 0 and reward > 0:
                rr_values.append(reward / risk)

        if rr_values:
            assert max(rr_values) >= 2.0, f"max R/R = {max(rr_values):.2f} < 2.0"

    def test_tp1_within_exit_limit(self, df_up):
        """TP1 («план закрытия») не дальше _MAX_EXIT_PCT от входа (кейс AAVE 1d)."""
        gen = SignalGenerator(min_confidence=0.5)
        result = gen.generate_signal("ETH", "4h", df_up)
        if result is None or not result.get("take_profit"):
            pytest.skip("Нет сигнала или TP")

        entry = result["entry_price"]
        tp1 = result["take_profit"][0]["level"]
        limit_pct = SignalGenerator._MAX_EXIT_PCT["4h"]
        if result["signal_type"] == "BUY":
            assert tp1 <= entry * (1 + limit_pct) * 1.0001, (
                f"TP1 {tp1} дальше {limit_pct*100:.1f}% от входа {entry}"
            )
        else:
            assert tp1 >= entry * (1 - limit_pct) * 0.9999, (
                f"TP1 {tp1} дальше {limit_pct*100:.1f}% от входа {entry}"
            )


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


class TestStretchedTPConfidenceCap:
    """RR-фильтр оставил только маловероятные цели → сигнал не STRONG.

    Кейс LTC 3d 2026-09-18: ближние сопротивления срезаны RR-фильтром,
    выжила дальняя цель с p=0.1, но confidence остался 0.97 STRONG.
    """

    @pytest.fixture
    def df_flat(self):
        return pd.DataFrame({
            "open": [54.0] * 30, "high": [56.0] * 30,
            "low": [53.0] * 30, "close": [55.5] * 30,
            "volume": [100.0] * 30,
        })

    def test_caps_confidence_when_only_low_prob_tp_survive(self, df_flat):
        gen = SignalGenerator()
        # SL по ATR-floor = 50.527, риск 5.1 → цели ближе 60.73 режутся RR-фильтром
        levels = {
            "support_levels": [{"price": 52.64}],
            "resistance_levels": [
                {"price": 57.75}, {"price": 59.5},
                {"price": 61.0}, {"price": 65.84},
            ],
        }
        sig = gen._create_buy_signal(
            df_flat, current_price=55.63, confidence=0.97, factors=[],
            levels=levels, head_shoulders=None, volume_confirmation=False,
            rsi_analysis={"rsi": 64.1}, atr_value=5.103 / 1.5,
        )
        assert sig is not None
        assert sig["confidence"] <= 0.84
        assert sig["strength"] != "STRONG"

    def test_probability_by_rank_not_raw_index(self, df_flat):
        """Уровни ниже цены не должны занижать probability ближайшей цели.

        Кейс LTC 3d: мусорные сопротивления ниже цены занимали индексы,
        и ближайшая валидная цель получала p=0.1 вместо p=0.7.
        """
        gen = SignalGenerator()
        levels = {
            "support_levels": [{"price": 52.64}],
            "resistance_levels": [
                {"price": 42.0}, {"price": 45.0}, {"price": 50.0},  # ниже цены
                {"price": 60.9}, {"price": 65.84},
            ],
        }
        tps = gen._collect_tp_levels(levels, current_price=55.63, tp_limit=69.0, direction="BUY")
        assert tps[0]["level"] == 60.9
        assert tps[0]["probability"] == pytest.approx(0.7)

    def test_role_reversal_levels_become_targets(self, df_flat):
        """Старые лои выше цены (кейс NEAR 3d) должны становиться целями BUY."""
        gen = SignalGenerator()
        levels = {
            "support_levels": [{"price": 3.078}, {"price": 3.927}, {"price": 4.194}],
            "resistance_levels": [{"price": 2.973}, {"price": 3.182}, {"price": 4.444}],
        }
        tps = gen._collect_tp_levels(levels, current_price=3.532, tp_limit=4.415, direction="BUY")
        tp_levels = [tp["level"] for tp in tps]
        assert tp_levels == [3.927, 4.194], f"Цели из старых лоёв потеряны: {tp_levels}"

    def test_tp_sorted_after_head_shoulders_target(self, df_flat):
        """Цель «голова-плечи» ближе уровней — она должна стать TP1, а не хвостом."""
        gen = SignalGenerator()
        levels = {
            "support_levels": [{"price": 52.64}],
            "resistance_levels": [{"price": 64.0}, {"price": 66.0}],
        }
        sig = gen._create_buy_signal(
            df_flat, current_price=55.63, confidence=0.9, factors=[],
            levels=levels, head_shoulders={"target_price": 61.5},
            volume_confirmation=False, rsi_analysis={"rsi": 55.0},
            atr_value=5.103 / 1.5,
            max_exit_pct=0.20,  # широкий лимит: тест про сортировку, не про кэп TP1
        )
        assert sig is not None
        tp_levels = [tp["level"] for tp in sig["take_profit"]]
        assert tp_levels == sorted(tp_levels)
        assert tp_levels[0] == 61.5

    def test_no_cap_when_realistic_tp_survives(self, df_flat):
        gen = SignalGenerator()
        # Ближайшая цель 60.9 проходит RR-фильтр с probability 0.7 — штрафа нет
        levels = {
            "support_levels": [{"price": 52.64}],
            "resistance_levels": [{"price": 60.9}, {"price": 65.84}],
        }
        sig = gen._create_buy_signal(
            df_flat, current_price=55.63, confidence=0.97, factors=[],
            levels=levels, head_shoulders=None, volume_confirmation=False,
            rsi_analysis={"rsi": 55.0}, atr_value=5.103 / 1.5,
        )
        assert sig is not None
        assert sig["confidence"] == 0.97
        assert sig["strength"] == "STRONG"


class TestContradictionPenalty:
    """Факторы против направления сигнала снижают потолок confidence.

    Кейс BTC 1d 2026-09-19: BUY 0.97 STRONG при медвежьей дивергенции MACD,
    медвежьей структуре и RSI 64 — аддитивный score не видел встречных очков.
    1 противоречие → cap 0.84 (не STRONG), 2+ → 0.75.
    """

    @pytest.fixture
    def df_flat(self):
        return pd.DataFrame({
            "open": [54.0] * 30, "high": [56.0] * 30,
            "low": [53.0] * 30, "close": [55.5] * 30,
            "volume": [100.0] * 30,
        })

    @pytest.fixture
    def buy_levels(self):
        # TP1 60.9 проходит RR-фильтр с p=0.7 — растянутых целей нет
        return {
            "support_levels": [{"price": 52.64, "strength": 1.0}],
            "resistance_levels": [{"price": 60.9, "strength": 1.0},
                                  {"price": 65.84, "strength": 1.0}],
        }

    def _evaluate_buy(self, gen, df, levels, *, rsi, structure, divergence):
        return gen._evaluate_signals(
            df, current_price=55.63,
            candlestick_pattern="Hammer",
            levels=levels,
            head_shoulders={"pattern_direction": "BULLISH", "target_price": None},
            rsi_analysis={"rsi": rsi, "rsi_signal": "NEUTRAL",
                          "rsi_zone": "NEUTRAL", "rsi_strength": 0.0},
            atr_value=5.103 / 1.5,
            ema_analysis={"trend": "BULLISH", "ema_cross": "BULLISH", "ema50": 54.0},
            macd_analysis={"macd_signal": "NEUTRAL", "divergence": divergence},
            vwap_data={},
            market_structure={"structure": structure, "trend_strength": 0.5,
                              "bos": None, "choch": None},
            order_blocks=[],
            volume_profile={}, fibonacci={}, liquidity_grab=None,
        )

    def test_three_contradictions_cap_075(self, df_flat, buy_levels):
        """Дивергенция + структура + не остывший RSI → потолок 0.75."""
        gen = SignalGenerator()
        sig = self._evaluate_buy(gen, df_flat, buy_levels,
                                 rsi=64.0, structure="BEARISH", divergence="BEARISH")
        assert sig is not None
        assert sig["signal_type"] == "BUY"
        assert sig["confidence"] <= 0.75
        assert sig["strength"] != "STRONG"

    def test_single_contradiction_caps_below_strong(self, df_flat, buy_levels):
        """Одна медвежья дивергенция → не STRONG (cap 0.84)."""
        gen = SignalGenerator()
        sig = self._evaluate_buy(gen, df_flat, buy_levels,
                                 rsi=55.0, structure="BULLISH", divergence="BEARISH")
        assert sig is not None
        assert sig["confidence"] <= 0.84
        assert sig["strength"] != "STRONG"

    def test_clean_context_not_penalized(self, df_flat, buy_levels):
        """Без противоречий confidence не трогаем — STRONG остаётся."""
        gen = SignalGenerator()
        sig = self._evaluate_buy(gen, df_flat, buy_levels,
                                 rsi=55.0, structure="BULLISH", divergence=None)
        assert sig is not None
        assert sig["confidence"] >= 0.85
        assert sig["strength"] == "STRONG"

    def test_sell_against_bullish_trend_capped(self, df_flat):
        """SELL при EMA BULLISH + структуре BULLISH (кейс ETH 1d) → потолок 0.75."""
        gen = SignalGenerator()
        levels = {
            "support_levels": [{"price": 45.0, "strength": 1.0}],
            "resistance_levels": [{"price": 57.75, "strength": 1.0}],
        }
        sig = gen._evaluate_signals(
            df_flat, current_price=55.63,
            candlestick_pattern="Shooting Star",
            levels=levels,
            head_shoulders={"pattern_direction": "BEARISH", "target_price": None},
            rsi_analysis={"rsi": 66.0, "rsi_signal": "SELL",
                          "rsi_zone": "NEAR_OVERBOUGHT", "rsi_strength": 0.5},
            atr_value=5.103 / 1.5,
            ema_analysis={"trend": "BULLISH", "ema_cross": "NEUTRAL", "ema50": 54.0},
            macd_analysis={"macd_signal": "NEUTRAL", "divergence": None},
            vwap_data={},
            market_structure={"structure": "BULLISH", "trend_strength": 0.5,
                              "bos": None, "choch": None},
            order_blocks=[],
            volume_profile={}, fibonacci={}, liquidity_grab=None,
        )
        assert sig is not None
        assert sig["signal_type"] == "SELL"
        assert sig["confidence"] <= 0.75
        assert sig["strength"] != "STRONG"


class TestCapPrimaryExit:
    """TP1 прижимается к входу: приоритет закрытой сделке, а не R/R.

    Кейс AAVE 1d 2026-09-23: вход 147.72, TP1 167.75 (+13.5%) — не достигнут,
    хотя +5% (155) цена дала. План закрытия должен быть в пределах _MAX_EXIT_PCT.
    """

    def test_far_tp1_replaced_with_level_within_limit(self):
        gen = SignalGenerator(min_confidence=0.5)
        entry = 147.72
        # RR-фильтр срезал ближний уровень 152.0, остался дальний 167.75
        pre_rr = [
            {"level": 152.0, "probability": 0.7},
            {"level": 167.75, "probability": 0.5},
        ]
        filtered = [{"level": 167.75, "probability": 0.5}]
        out = gen._cap_primary_exit(entry, filtered, pre_rr, 0.04, "BUY")
        assert out[0]["level"] == 152.0  # реальный уровень в пределах +4%
        assert out[1]["level"] == 167.75  # дальняя цель осталась как TP2

    def test_synthetic_exit_when_no_level_within_limit(self):
        gen = SignalGenerator(min_confidence=0.5)
        entry = 147.72
        filtered = [{"level": 167.75, "probability": 0.5}]
        out = gen._cap_primary_exit(entry, filtered, filtered, 0.04, "BUY")
        assert out[0]["level"] == pytest.approx(entry * 1.04)
        assert out[1]["level"] == 167.75

    def test_near_tp1_untouched(self):
        gen = SignalGenerator(min_confidence=0.5)
        entry = 100.0
        filtered = [{"level": 103.0, "probability": 0.7}, {"level": 110.0, "probability": 0.3}]
        out = gen._cap_primary_exit(entry, filtered, filtered, 0.04, "BUY")
        assert out == filtered

    def test_sell_capped_symmetrically(self):
        gen = SignalGenerator(min_confidence=0.5)
        entry = 100.0
        pre_rr = [{"level": 97.0, "probability": 0.7}, {"level": 85.0, "probability": 0.5}]
        filtered = [{"level": 85.0, "probability": 0.5}]
        out = gen._cap_primary_exit(entry, filtered, pre_rr, 0.04, "SELL")
        assert out[0]["level"] == 97.0
        assert out[1]["level"] == 85.0
