"""Контейнер аналитических компонентов и фабрика."""
import argparse
from dataclasses import dataclass, field

from ..parsers.exchange_manager import ExchangeManager
from ..storage.database import Database
from ..analytics.signals.generator import SignalGenerator
from ..analytics.levels.support_resistance import SupportResistanceAnalyzer
from ..analytics.patterns.head_shoulders import HeadShouldersPattern
from ..analytics.patterns.chart_patterns import ChartPatternDetector
from ..analytics.candlestick.patterns import CandlestickPatternAnalyzer
from ..analytics.indicators.rsi import RSICalculator
from ..analytics.indicators.atr import ATRCalculator
from ..analytics.indicators.ema import EMACalculator
from ..analytics.indicators.macd import MACDCalculator
from ..analytics.indicators.vwap import VWAPCalculator
from ..analytics.indicators.volume_profile import VolumeProfileCalculator
from ..utils.config import Settings, get_analysis_config


@dataclass
class Components:
    exchange_manager: ExchangeManager
    database: Database
    signal_generator: SignalGenerator
    levels_analyzer: SupportResistanceAnalyzer
    pattern_analyzer: HeadShouldersPattern
    chart_pattern_detector: ChartPatternDetector
    candlestick_analyzer: CandlestickPatternAnalyzer
    rsi_calculator: RSICalculator
    atr_calculator: ATRCalculator
    ema_calculator: EMACalculator
    macd_calculator: MACDCalculator
    vwap_calculator: VWAPCalculator
    volume_profile_calculator: VolumeProfileCalculator
    oi_cache: dict = field(default_factory=dict)
    ob_cache: dict = field(default_factory=dict)


def build_components(args: argparse.Namespace, config: dict) -> Components:
    settings = Settings()
    acfg = get_analysis_config(config)

    enabled_exchanges = [
        name for name, ex_cfg in config.get("exchanges", {}).items()
        if ex_cfg.get("enabled", False)
    ]

    return Components(
        exchange_manager=ExchangeManager(enabled_exchanges),
        database=Database(settings.database_url),
        signal_generator=SignalGenerator(min_confidence=args.min_confidence),
        levels_analyzer=SupportResistanceAnalyzer(
            min_touches=acfg.min_touches,
            price_tolerance=acfg.price_tolerance,
        ),
        pattern_analyzer=HeadShouldersPattern(
            min_pattern_length=acfg.min_pattern_length,
            symmetry_tolerance=acfg.symmetry_tolerance,
        ),
        chart_pattern_detector=ChartPatternDetector(
            min_pattern_length=acfg.min_pattern_length,
            price_tolerance=acfg.price_tolerance,
        ),
        candlestick_analyzer=CandlestickPatternAnalyzer(),
        rsi_calculator=RSICalculator(period=14),
        atr_calculator=ATRCalculator(period=14),
        ema_calculator=EMACalculator(periods=[9, 21, 50]),
        macd_calculator=MACDCalculator(),
        vwap_calculator=VWAPCalculator(),
        volume_profile_calculator=VolumeProfileCalculator(),
    )
