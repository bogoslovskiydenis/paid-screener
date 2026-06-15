"""Общие фикстуры для тестов."""
import sys
from pathlib import Path

# Добавляем src в sys.path чтобы работали импорты
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
import pandas as pd
import pytest


def _make_ohlcv(n: int = 200, trend: str = "up", base: float = 100.0) -> pd.DataFrame:
    """Генерирует синтетический OHLCV DataFrame для тестов.

    Args:
        n: количество свечей
        trend: 'up' / 'down' / 'flat'
        base: начальная цена
    """
    rng = np.random.default_rng(42)

    prices = [base]
    for _ in range(n - 1):
        if trend == "up":
            delta = rng.normal(0.3, 1.0)
        elif trend == "down":
            delta = rng.normal(-0.3, 1.0)
        else:
            delta = rng.normal(0.0, 1.0)
        prices.append(max(prices[-1] + delta, 1.0))

    timestamps = pd.date_range("2024-01-01", periods=n, freq="4h")
    closes = np.array(prices)
    highs = closes + rng.uniform(0.5, 2.0, n)
    lows = closes - rng.uniform(0.5, 2.0, n)
    lows = np.maximum(lows, 0.1)
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = rng.uniform(1000, 5000, n)

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def df_up():
    return _make_ohlcv(200, trend="up")


@pytest.fixture
def df_down():
    return _make_ohlcv(200, trend="down")


@pytest.fixture
def df_flat():
    return _make_ohlcv(200, trend="flat")


@pytest.fixture
def df_short():
    """Слишком короткий DataFrame (меньше минимума)."""
    return _make_ohlcv(50, trend="flat")
