"""BTC-фильтр для BUY-сигналов альтов."""
import pandas as pd

from ..analytics.levels.support_resistance import SupportResistanceAnalyzer
from ..analytics.indicators.rsi import RSICalculator


def btc_allows_buy(df: pd.DataFrame, levels_analyzer: SupportResistanceAnalyzer) -> bool:
    """Возвращает False, если BTC в медвежьем тренде с пробоем поддержки."""
    if df is None or df.empty or len(df) < 100:
        return True

    rsi_calc = RSICalculator(period=14)
    rsi_analysis = rsi_calc.analyze(df)
    if (
        rsi_analysis.get("rsi_signal") == "SELL"
        and rsi_analysis.get("rsi_zone", "") in ("OVERBOUGHT", "NEAR_OVERBOUGHT")
    ):
        return False

    levels = levels_analyzer.find_levels(df)
    if levels:
        breakout = levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
        if (
            breakout.get("breakout")
            and breakout.get("level_type") == "support"
            and breakout.get("breakout_direction") == "DOWN"
        ):
            return False

    return True
