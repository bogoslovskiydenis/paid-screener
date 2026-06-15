"""Тесты фильтров сигналов и spot-long фильтров."""
import pytest
import numpy as np
import pandas as pd
from src.analytics.entry_spot_filters import (
    _score_asset_long,
    _rsi,
    _ema,
    _macd_sig,
    _nearest_support_dist_pct,
    DEFAULT_ANCHOR_TF,
    DEFAULT_TIMING_TF,
)


def _make_results(
    asset: str,
    anchor_tf: str,
    timing_tf: str,
    anchor_ema_trend: str = "BULLISH",
    anchor_rsi: float = 50.0,
    timing_rsi: float = 50.0,
    timing_macd: str = "BUY",
    signal_type: str = "BUY",
    signal_conf: float = 0.8,
    support_price: float = 95.0,
    current_price: float = 100.0,
) -> dict:
    """Вспомогательная функция: строит минимальный results-словарь."""
    return {
        asset: {
            anchor_tf: {
                "ema": {"trend": anchor_ema_trend, "ema_cross": "NEUTRAL"},
                "rsi": {"rsi": anchor_rsi, "rsi_zone": "NEUTRAL"},
                "macd": {"macd_signal": "NEUTRAL"},
                "current_price": current_price,
                "levels": {"support_levels": [{"price": support_price, "strength": 1.0}]},
            },
            timing_tf: {
                "ema": {"trend": anchor_ema_trend, "ema_cross": "NEUTRAL"},
                "rsi": {"rsi": timing_rsi, "rsi_zone": "NEUTRAL"},
                "macd": {"macd_signal": timing_macd},
                "current_price": current_price,
                "levels": {"support_levels": [{"price": support_price, "strength": 1.0}]},
                "signal": {
                    "signal_type": signal_type,
                    "confidence": signal_conf,
                    "strength": "MEDIUM",
                },
            },
        }
    }


class TestSpotLongScore:
    def test_all_green_returns_active(self):
        """Все условия выполнены → active=True."""
        results = _make_results(
            "ETH", "1d", "4h",
            anchor_ema_trend="BULLISH",
            anchor_rsi=55.0,
            timing_rsi=55.0,
            timing_macd="BUY",
            signal_type="BUY",
            signal_conf=0.85,
            support_price=98.5,  # в пределах 2%
            current_price=100.0,
        )
        score = _score_asset_long(results, "ETH", "1d", "4h", min_confidence=0.7)
        assert score["active"] is True
        assert score["structure_ok"] is True

    def test_bearish_ema_blocks(self):
        """Медвежий EMA на старшем ТФ → active=False."""
        results = _make_results(
            "ETH", "1d", "4h",
            anchor_ema_trend="BEARISH",
            signal_conf=0.9,
            support_price=98.5,
        )
        score = _score_asset_long(results, "ETH", "1d", "4h", min_confidence=0.7)
        assert score["active"] is False
        assert score["structure_ok"] is False

    def test_high_rsi_blocks(self):
        """RSI ≥ 70 на старшем ТФ → не должен пускать."""
        results = _make_results(
            "ETH", "1d", "4h",
            anchor_ema_trend="BULLISH",
            anchor_rsi=72.0,
            signal_conf=0.9,
        )
        score = _score_asset_long(results, "ETH", "1d", "4h", min_confidence=0.7)
        assert score["structure_ok"] is False

    def test_macd_sell_blocks(self):
        """MACD=SELL на timing_tf → structure_ok=False."""
        results = _make_results(
            "ETH", "1d", "4h",
            timing_macd="SELL",
            anchor_ema_trend="BULLISH",
            anchor_rsi=55.0,
            timing_rsi=50.0,
            signal_conf=0.9,
        )
        score = _score_asset_long(results, "ETH", "1d", "4h", min_confidence=0.7)
        assert score["structure_ok"] is False

    def test_low_signal_confidence_blocks(self):
        """Уверенность сигнала ниже порога → active=False."""
        results = _make_results(
            "ETH", "1d", "4h",
            anchor_ema_trend="BULLISH",
            anchor_rsi=55.0,
            timing_rsi=55.0,
            timing_macd="BUY",
            signal_type="BUY",
            signal_conf=0.5,
            support_price=98.5,
        )
        score = _score_asset_long(results, "ETH", "1d", "4h", min_confidence=0.7)
        assert score["active"] is False

    def test_missing_asset_data(self):
        """Нет данных для актива → active=False."""
        score = _score_asset_long({}, "XLM", "1d", "4h", min_confidence=0.7)
        assert score["active"] is False
        assert score["confidence"] == 0.0


class TestHelpers:
    def test_nearest_support_dist_pct_close(self):
        """Поддержка на расстоянии 1% → dist ≈ 1."""
        levels = {"support_levels": [{"price": 99.0, "strength": 1.0}]}
        dist = _nearest_support_dist_pct(levels, 100.0)
        assert dist is not None
        assert abs(dist - 1.0) < 0.01

    def test_nearest_support_dist_pct_no_levels(self):
        """Нет уровней → None."""
        dist = _nearest_support_dist_pct({"support_levels": []}, 100.0)
        assert dist is None

    def test_nearest_support_dist_pct_only_above(self):
        """Все поддержки выше цены → None (ищем только снизу)."""
        levels = {"support_levels": [{"price": 110.0, "strength": 1.0}]}
        dist = _nearest_support_dist_pct(levels, 100.0)
        assert dist is None

    def test_rsi_helper_extracts_value(self):
        block = {"rsi": {"rsi": 55.5, "rsi_zone": "NEUTRAL"}}
        assert _rsi(block) == 55.5

    def test_rsi_helper_missing(self):
        assert _rsi({}) is None

    def test_ema_helper(self):
        block = {"ema": {"trend": "BULLISH"}}
        result = _ema(block)
        assert result.get("trend") == "BULLISH"

    def test_macd_sig_helper(self):
        block = {"macd": {"macd_signal": "buy"}}
        assert _macd_sig(block) == "BUY"  # должен вернуть uppercase
