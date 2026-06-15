#!/usr/bin/env python3
"""Запуск проекта с реальными данными с Binance."""
import sys
import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Проверка зависимостей (до setup_logger — используем print)
try:
    import ccxt        # noqa: F401
    import yaml        # noqa: F401
    import pandas as pd
    import numpy as np  # noqa: F401
    from sqlalchemy import create_engine  # noqa: F401
    print("✓ Все зависимости установлены")
except ImportError as e:
    print(f"✗ Отсутствует зависимость: {e}")
    print("  Установите: pip install -r requirements.txt")
    sys.exit(1)

# Импорт модулей проекта
try:
    from src.utils.config import load_config, get_assets, get_timeframes, Settings, get_analysis_config
    from src.utils.format_price import fmt_price
    from src.utils.logger import setup_logger
    from src.parsers.exchange_manager import ExchangeManager
    from src.storage.database import Database
    from src.analytics.signals.generator import SignalGenerator
    from src.analytics.levels.support_resistance import SupportResistanceAnalyzer
    from src.analytics.patterns.head_shoulders import HeadShouldersPattern
    from src.analytics.patterns.chart_patterns import ChartPatternDetector
    from src.analytics.indicators.rsi import RSICalculator
    from src.analytics.indicators.atr import ATRCalculator
    from src.analytics.indicators.ema import EMACalculator
    from src.analytics.indicators.macd import MACDCalculator
    from src.analytics.candlestick.patterns import CandlestickPatternAnalyzer
    from src.analytics.rotation_filters import compute_rotation_bundle, format_rotation_console
    from src.analytics.entry_spot_filters import compute_spot_long_bundle, format_spot_long_console
    from src.analytics.context_bundles import merge_context_bundles_into_results, format_context_bundles_console
    from src.analytics.market_sentiment import analyze_market_sentiment, SentimentResult  # noqa: F401
    from src.analytics.pump_detector import AltPumpDetector
    from src.analytics.accumulation_detector import AccumulationDetector
    from src.analytics.indicators.vwap import VWAPCalculator
    from src.analytics.indicators.volume_profile import VolumeProfileCalculator
    from src.analytics.market_structure import detect_structure, detect_order_blocks
    from src.analytics.multi_tf_confluence import compute_confluence
    from src.utils.fear_greed import get_fear_greed
    from src.utils.btc_dominance import get_btc_dominance
    from src.analytics.orderbook_analyzer import analyze_orderbook
    print("✓ Модули проекта загружены")
except ImportError as e:
    print(f"✗ Ошибка импорта модулей: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

logger = setup_logger(__name__)

FUNDING_USDT_ASSETS = frozenset({"ETH", "SOL", "BTC"})
ACTIVE_SIGNALS_PATH = Path("data/active_signals.json")


def _add_signal_to_tracker(signal: dict) -> None:
    """Добавляет сгенерированный сигнал в active_signals.json для трекинга TP/SL."""
    path = ACTIVE_SIGNALS_PATH
    existing: list = []
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as _f:
                data = json.load(_f)
            existing = data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            existing = []

    asset = signal.get("asset")
    tf = signal.get("timeframe")
    stype = signal.get("signal_type")

    # Не добавляем дубликат, если такой незакрытый сигнал уже есть
    for s in existing:
        if (
            s.get("asset") == asset
            and s.get("timeframe") == tf
            and s.get("signal_type") == stype
            and s.get("status") not in {"TP", "SL", "TSL"}
        ):
            logger.info("[%s/%s] Сигнал уже в трекере, пропускаем", asset, tf)
            return

    existing.append(signal)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as _f:
        json.dump(existing, _f, ensure_ascii=False, indent=2, default=str)
    logger.info("[%s/%s] ➕ Сигнал добавлен в трекер (%d активных)", asset, tf, len(existing))
FUNDING_RATE_THRESHOLD = 0.0005


# ---------------------------------------------------------------------------
# Контейнер компонентов
# ---------------------------------------------------------------------------

@dataclass
class _Components:
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
    # OI кэш между итерациями: {asset: float} — значение прошлого запуска
    oi_cache: dict = field(default_factory=dict)
    # Стакан прошлого прогона для OFI: {asset: {"bids": ..., "asks": ...}}
    ob_cache: dict = field(default_factory=dict)


def _build_components(args: argparse.Namespace, config: dict) -> _Components:
    """Создаёт все аналитические компоненты по конфигу."""
    settings = Settings()
    acfg = get_analysis_config(config)  # типизированный объект вместо цепочек .get()

    enabled_exchanges = [
        name for name, ex_cfg in config.get("exchanges", {}).items()
        if ex_cfg.get("enabled", False)
    ]

    return _Components(
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


# ---------------------------------------------------------------------------
# Фильтр BTC
# ---------------------------------------------------------------------------

def _btc_allows_buy(df: pd.DataFrame, levels_analyzer: SupportResistanceAnalyzer) -> bool:
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


# ---------------------------------------------------------------------------
# Анализ одного таймфрейма
# ---------------------------------------------------------------------------

def _analyze_timeframe(
    asset: str,
    timeframe: str,
    df: pd.DataFrame,
    c: _Components,
    btc_buy_allowed_cache: dict,
    funding_rate_cache: dict,
    ls_ratio_cache: dict,
    min_confidence: float,
    limit: int,
) -> dict:
    """Полный анализ одного актива/таймфрейма. Возвращает result_data."""

    logger.info(
        "[%s/%s] Загружено %d свечей | цена $%s | диапазон %s – %s",
        asset, timeframe, len(df),
        fmt_price(df.iloc[-1]["close"]),
        df["timestamp"].min(), df["timestamp"].max(),
    )

    # Уровни поддержки/сопротивления
    levels = c.levels_analyzer.find_levels(df)
    logger.info(
        "[%s/%s] Уровни: поддержка %d, сопротивление %d",
        asset, timeframe,
        len(levels.get("support_levels", [])),
        len(levels.get("resistance_levels", [])),
    )

    breakout: dict = {}
    if levels:
        breakout = c.levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
        if breakout.get("breakout"):
            logger.info(
                "[%s/%s] ⚠ ПРОБОЙ %s $%s | направление: %s | объём: %s",
                asset, timeframe,
                breakout["level_type"].upper(),
                fmt_price(breakout["price"]),
                breakout.get("breakout_direction", "N/A"),
                "✓" if breakout.get("volume_confirmation") else "✗",
            )

    # RSI
    rsi_analysis = c.rsi_calculator.analyze(df)
    rsi_value = rsi_analysis.get("rsi")
    rsi_zone = rsi_analysis.get("rsi_zone", "NEUTRAL")
    rsi_signal = rsi_analysis.get("rsi_signal", "NEUTRAL")
    if rsi_value:
        logger.info("[%s/%s] RSI: %.1f (%s) → %s", asset, timeframe, rsi_value, rsi_zone, rsi_signal)

    # Свечные паттерны
    pattern = c.candlestick_analyzer.analyze(df)
    if pattern:
        logger.info("[%s/%s] Свечной паттерн: %s", asset, timeframe, pattern)

    # Голова и плечи
    hs_pattern = c.pattern_analyzer.detect(df)
    if hs_pattern:
        logger.info(
            "[%s/%s] Г&П: %s (%s)",
            asset, timeframe, hs_pattern["pattern_type"], hs_pattern["pattern_direction"],
        )

    # Дополнительные графические паттерны
    chart_patterns = c.chart_pattern_detector.detect_all(df)
    for cp in (chart_patterns or []):
        logger.info("[%s/%s] Паттерн: %s (%s)", asset, timeframe, cp["pattern_type"], cp["pattern_direction"])

    # BTC-фильтр (только для USDT-альтов)
    btc_buy_allowed = True
    if "/" not in asset and asset in ("ETH", "SOL"):
        if timeframe in btc_buy_allowed_cache:
            btc_buy_allowed = btc_buy_allowed_cache[timeframe]
        else:
            try:
                btc_df = c.exchange_manager.get_ohlcv("BTC", timeframe, limit=limit)
            except Exception as exc:
                logger.warning("Не удалось загрузить BTC для фильтра: %s", exc)
                btc_df = None
            btc_buy_allowed = _btc_allows_buy(btc_df, c.levels_analyzer)
            btc_buy_allowed_cache[timeframe] = btc_buy_allowed

    # Funding rate
    funding_rate = None
    if "/" not in asset and asset in FUNDING_USDT_ASSETS:
        if asset not in funding_rate_cache:
            funding_rate_cache[asset] = c.exchange_manager.get_funding_rate(asset)
        funding_rate = funding_rate_cache[asset]
        if funding_rate is not None:
            logger.info("[%s/%s] Funding Rate: %+.4f%%", asset, timeframe, funding_rate * 100)

    # Генерация сигнала
    signal = c.signal_generator.generate_signal(asset, timeframe, df)

    if signal and signal.get("signal_type") == "BUY" and not btc_buy_allowed:
        old_conf = float(signal.get("confidence", 0))
        signal["confidence"] = max(0.0, old_conf - 0.15)
        signal["btc_warning"] = "BTC в медвежьем тренде — confidence снижен"
        logger.info(
            "[%s/%s] BTC медвежий: confidence %.1f%% → %.1f%%",
            asset, timeframe, old_conf * 100, signal["confidence"] * 100,
        )
        if signal["confidence"] < min_confidence:
            logger.info("[%s/%s] BUY отозван: confidence ниже порога после BTC-фильтра", asset, timeframe)
            signal = None

    if signal and funding_rate is not None:
        if signal.get("signal_type") == "BUY" and funding_rate > FUNDING_RATE_THRESHOLD:
            logger.info("[%s/%s] BUY заблокирован: funding rate перегрет (%+.4f%%)", asset, timeframe, funding_rate * 100)
            signal = None
        elif signal.get("signal_type") == "SELL" and funding_rate < -FUNDING_RATE_THRESHOLD:
            logger.info("[%s/%s] SELL заблокирован: шорты перегреты (%+.4f%%)", asset, timeframe, funding_rate * 100)
            signal = None

    # ─── Market Sentiment: L/S Ratio + Open Interest ──────────────────────────
    sentiment_result = None
    if "/" not in asset and asset in FUNDING_USDT_ASSETS:
        # L/S Ratio (кэш в пределах одной итерации — один запрос на актив)
        if asset not in ls_ratio_cache:
            ls_ratio_cache[asset] = c.exchange_manager.get_long_short_ratio(asset)
        long_ratio = ls_ratio_cache.get(asset)

        # Open Interest (кэш между итерациями — нужна динамика)
        oi_now = c.exchange_manager.get_open_interest(asset)
        oi_prev = c.oi_cache.get(asset)
        if oi_now is not None:
            c.oi_cache[asset] = oi_now

        sig_type = signal.get("signal_type", "BUY") if signal else "BUY"
        sentiment_result = analyze_market_sentiment(
            signal_type=sig_type,
            long_ratio=long_ratio,
            open_interest=oi_now,
            oi_prev=oi_prev,
            funding_rate=funding_rate,
        )

        logger.info(
            "[%s/%s] 📊 Sentiment: %s | L/S: %s | OI Δ: %s | Δconf: %+.3f",
            asset, timeframe,
            sentiment_result.sentiment,
            f"{sentiment_result.long_ratio:.0%}" if sentiment_result.long_ratio is not None else "N/A",
            f"{sentiment_result.oi_change_pct:+.1f}%" if sentiment_result.oi_change_pct is not None else "—",
            sentiment_result.confidence_delta,
        )
        for note in sentiment_result.notes:
            logger.info("[%s/%s]    ↳ %s", asset, timeframe, note)

        # Мягкая корректировка уверенности (не жёсткий блок, как funding rate)
        if signal and sentiment_result.confidence_delta != 0:
            old_conf = float(signal.get("confidence", 0))
            new_conf = max(0.0, min(1.0, old_conf + sentiment_result.confidence_delta))
            signal["confidence"] = new_conf
            if new_conf < min_confidence:
                logger.info(
                    "[%s/%s] Сигнал отозван (sentiment): conf %.1f%% → %.1f%% < %.0f%%",
                    asset, timeframe, old_conf * 100, new_conf * 100, min_confidence * 100,
                )
                signal = None

        # Встраиваем sentiment в сигнал для Telegram
        if signal is not None:
            signal["sentiment"] = sentiment_result.to_dict()

    if signal:
        logger.info(
            "[%s/%s] ✓ СИГНАЛ %s (%s) | conf=%.1f%% | вход=$%s | SL=$%s | TP=%d",
            asset, timeframe,
            signal["signal_type"], signal["strength"],
            signal["confidence"] * 100,
            fmt_price(signal["entry_price"]),
            fmt_price(signal["stop_loss"]),
            len(signal.get("take_profit", [])),
        )
        c.database.save_signal(signal)
        _add_signal_to_tracker(signal)
    else:
        logger.info("[%s/%s] Сигнал не сгенерирован (низкая уверенность)", asset, timeframe)

    # Дополнительные индикаторы
    _atr = c.atr_calculator.get_current(df)
    _ema = c.ema_calculator.analyze(df)
    _macd = c.macd_calculator.analyze(df)
    atr_str = f"{_atr:.4f}" if _atr else "N/A"
    logger.info(
        "[%s/%s] ATR=%s | EMA=%s/%s | MACD=%s%s",
        asset, timeframe,
        atr_str,
        _ema.get("trend", "?"), _ema.get("ema_cross", "?"),
        _macd.get("macd_signal", "?"),
        f" дивергенция={_macd['divergence']}" if _macd.get("divergence") else "",
    )

    # Order Book (только на 4h, чтобы не нагружать API)
    _orderbook = None
    if "/" not in asset and timeframe == "4h":
        ob_data = c.exchange_manager.get_order_book(asset)
        if ob_data:
            cur_p = float(df.iloc[-1]["close"])
            prev_ob = c.ob_cache.get(asset, {})
            recent_trades = c.exchange_manager.get_recent_trades(asset, limit=200)
            _orderbook = analyze_orderbook(
                bids=ob_data["bids"],
                asks=ob_data["asks"],
                current_price=cur_p,
                prev_bids=prev_ob.get("bids"),
                prev_asks=prev_ob.get("asks"),
                recent_trades=recent_trades,
            )
            c.ob_cache[asset] = {"bids": ob_data["bids"], "asks": ob_data["asks"]}

            trade_imb = _orderbook.get("trade_imbalance") or {}
            spoofing = _orderbook.get("spoofing") or {}
            logger.info(
                "[%s/%s] OrderBook: bid=$%.0f ask=$%.0f imb=%.1f%% → %s | "
                "mid_pressure=%+.3f%% | OFI=%s%s%s",
                asset, timeframe,
                _orderbook["bid_volume"], _orderbook["ask_volume"],
                _orderbook["imbalance"] * 100, _orderbook["signal"],
                _orderbook["mid_pressure"],
                f"{_orderbook['ofi']:+.2f}" if _orderbook.get("ofi") is not None else "N/A",
                f" | trade_imb={trade_imb.get('imbalance', 0):+.2f}({trade_imb.get('signal','')})"
                if trade_imb else "",
                f" | spoof_bid={len(spoofing.get('bid_spoof', []))} ask={len(spoofing.get('ask_spoof', []))}"
                if (spoofing.get("bid_spoof") or spoofing.get("ask_spoof")) else "",
            )

    # VWAP
    _vwap = c.vwap_calculator.analyze(df)
    if _vwap.get("vwap"):
        logger.info(
            "[%s/%s] VWAP=%s (%s, %.1f%%)",
            asset, timeframe, fmt_price(_vwap["vwap"]),
            _vwap["vwap_signal"], _vwap["vwap_distance_pct"],
        )

    # Volume Profile
    _vol_profile = c.volume_profile_calculator.analyze(df)
    if _vol_profile.get("poc"):
        logger.info(
            "[%s/%s] VP: POC=%s | VAH=%s | VAL=%s | %s",
            asset, timeframe,
            fmt_price(_vol_profile["poc"]),
            fmt_price(_vol_profile["vah"]),
            fmt_price(_vol_profile["val"]),
            _vol_profile["price_vs_poc"],
        )

    # Market Structure (BOS/CHoCH)
    _structure = detect_structure(df)
    if _structure["structure"] != "UNKNOWN":
        parts = [f"Структура: {_structure['structure']} ({_structure['trend_strength']:.0%})"]
        if _structure.get("bos"):
            parts.append(_structure["bos"]["description"])
        if _structure.get("choch"):
            parts.append(_structure["choch"]["description"])
        logger.info("[%s/%s] %s", asset, timeframe, " | ".join(parts))

    # Order Blocks
    _order_blocks = detect_order_blocks(df)
    active_obs = [ob for ob in _order_blocks if not ob["mitigated"]]
    if active_obs:
        for ob in active_obs[:2]:
            logger.info(
                "[%s/%s] OB %s: %s–%s (%d св. назад)",
                asset, timeframe, ob["type"],
                fmt_price(ob["ob_low"]), fmt_price(ob["ob_high"]),
                ob["candles_ago"],
            )

    result_data: dict = {
        "current_price": float(df.iloc[-1]["close"]),
        "candles_count": len(df),
        "rsi": rsi_analysis,
        "candlestick_pattern": pattern,
        "levels": levels,
        "head_shoulders_pattern": hs_pattern,
        "chart_patterns": chart_patterns or None,
        "atr": _atr,
        "ema": _ema,
        "macd": _macd,
        "vwap": _vwap,
        "volume_profile": _vol_profile,
        "market_structure": _structure,
        "order_blocks": active_obs[:5] if active_obs else None,
        "orderbook": _orderbook,
        "funding_rate": funding_rate,
        "sentiment": sentiment_result.to_dict() if sentiment_result is not None else None,
        "signal": signal,
    }
    if breakout.get("breakout"):
        result_data["breakout"] = breakout

    return result_data


# ---------------------------------------------------------------------------
# Pump scan альтов
# ---------------------------------------------------------------------------

def _run_pump_scan(
    args: argparse.Namespace,
    config: dict,
    c: _Components,
) -> list:
    """Сканирует топ-N альтов на памп-сигналы. Возвращает список найденных сигналов."""
    pump_cfg = config.get("pump_scan", {})
    if not pump_cfg.get("enabled", False) and not getattr(args, "pump_scan", False):
        return []

    top_n = int(pump_cfg.get("top_n", 30))
    min_vol = float(pump_cfg.get("min_volume_usd", 5_000_000))
    min_mcap = float(pump_cfg.get("min_market_cap_usd", 50_000_000))
    timeframe = pump_cfg.get("timeframe", "4h")
    limit = int(pump_cfg.get("limit", 100))

    detector = AltPumpDetector(
        volume_multiplier=float(pump_cfg.get("volume_multiplier", 2.0)),
        rsi_min=float(pump_cfg.get("rsi_min", 35.0)),
        rsi_max=float(pump_cfg.get("rsi_max", 72.0)),
        squeeze_lookback=int(pump_cfg.get("squeeze_lookback", 30)),
    )

    # В экстремальном страхе BB squeeze массовый — повышаем порог
    fg = get_fear_greed()
    fg_value = fg.get("value", 50)
    if fg_value < 15:
        detector.min_score = max(detector.min_score, 0.65)
        logger.info("Pump scan: F&G=%d (EXTREME_FEAR) → min_score повышен до %.2f", fg_value, detector.min_score)
    elif fg_value < 25:
        detector.min_score = max(detector.min_score, 0.55)
        logger.info("Pump scan: F&G=%d (FEAR) → min_score повышен до %.2f", fg_value, detector.min_score)

    # Основные монеты уже анализируются — исключаем из pump scan
    main_assets = set(
        a.upper() for a in (
            [a.strip() for a in args.asset.split(",") if a.strip()]
            if args.asset else config.get("assets", [])
        )
        if "/" not in a
    )

    logger.info("=" * 60)
    logger.info(
        "Pump scan: топ-%d альтов | мин объём $%s | мин mcap $%s",
        top_n, f"{min_vol:,.0f}", f"{min_mcap:,.0f}",
    )
    alts = c.exchange_manager.get_top_alts(
        top_n=top_n,
        min_volume_usd=min_vol,
        min_market_cap_usd=min_mcap,
        exclude=main_assets,
    )
    logger.info("Pump scan: найдено %d альтов для анализа", len(alts))

    pump_signals = []
    for alt in alts:
        try:
            df = c.exchange_manager.get_ohlcv(alt, timeframe, limit=limit)
            if df.empty or len(df) < 30:
                continue
            result = detector.detect(alt, df)
            if result:
                pump_signals.append(result)
            else:
                setup = detector.detect_setup(alt, df)
                if setup:
                    pump_signals.append(setup)
        except Exception as exc:
            logger.debug("Pump scan [%s]: ошибка — %s", alt, exc)

    # PUMP (breakout) в экстремальном страхе — пробои откатываются, дисконт confidence
    if fg_value < 20:
        penalty = 0.15 if fg_value < 15 else 0.10
        for sig in pump_signals:
            if sig["signal_type"] == "PUMP":
                old_conf = sig["confidence"]
                sig["confidence"] = round(max(0, old_conf - penalty), 3)
                sig["strength"] = (
                    "STRONG" if sig["confidence"] >= 0.80
                    else "MEDIUM" if sig["confidence"] >= 0.65
                    else "WEAK"
                )
                logger.info(
                    "  Pump [%s]: F&G=%d → conf %.0f%% → %.0f%% (breakout discount)",
                    sig["asset"], fg_value, old_conf * 100, sig["confidence"] * 100,
                )

    pump_signals.sort(key=lambda x: x["confidence"], reverse=True)
    logger.info("Pump scan завершён: %d сигналов из %d альтов", len(pump_signals), len(alts))
    for sig in pump_signals:
        icon = "⚡" if sig.get("signal_type") == "PRE_PUMP" else "🚀"
        logger.info(
            "  %s %s [%s] | %s | conf=%.0f%% | vol=%.1fx | RSI=%.1f | %s",
            icon, sig["asset"], sig.get("signal_type", "PUMP"),
            sig["strength"], sig["confidence"] * 100,
            sig.get("volume_ratio", 0), sig.get("rsi", 0),
            " | ".join(sig.get("signals", [])),
        )

    return pump_signals


# ---------------------------------------------------------------------------
# Accumulation scan
# ---------------------------------------------------------------------------

def _run_accumulation_scan(
    args: argparse.Namespace,
    config: dict,
    c: _Components,
) -> list:
    """Сканирует альты на признаки накопления на 1d. Возвращает список сигналов."""
    acc_cfg = config.get("accumulation_scan", {})
    if not acc_cfg.get("enabled", False) and not getattr(args, "pump_scan", False):
        return []

    top_n = int(acc_cfg.get("top_n", 50))
    min_vol = float(acc_cfg.get("min_volume_usd", 5_000_000))
    min_mcap = float(acc_cfg.get("min_market_cap_usd", 50_000_000))
    timeframe = acc_cfg.get("timeframe", "1d")
    limit = int(acc_cfg.get("limit", 100))

    detector = AccumulationDetector(
        sideways_period=int(acc_cfg.get("sideways_period", 20)),
        sideways_threshold=float(acc_cfg.get("sideways_threshold", 0.10)),
        min_score=float(acc_cfg.get("min_score", 0.55)),
        rsi_min=float(acc_cfg.get("rsi_min", 30.0)),
        rsi_max=float(acc_cfg.get("rsi_max", 60.0)),
    )

    main_assets = set(
        a.upper() for a in (
            [a.strip() for a in args.asset.split(",") if a.strip()]
            if args.asset else config.get("assets", [])
        )
        if "/" not in a
    )

    logger.info("=" * 60)
    logger.info("Accumulation scan: топ-%d альтов на %s", top_n, timeframe)
    alts = c.exchange_manager.get_top_alts(
        top_n=top_n,
        min_volume_usd=min_vol,
        min_market_cap_usd=min_mcap,
        exclude=main_assets,
    )
    logger.info("Accumulation scan: %d альтов для анализа", len(alts))

    acc_signals = []
    for alt in alts:
        try:
            df = c.exchange_manager.get_ohlcv(alt, timeframe, limit=limit)
            if df.empty or len(df) < 50:
                continue
            result = detector.detect(alt, df)
            if result:
                acc_signals.append(result)
        except Exception as exc:
            logger.debug("Accumulation scan [%s]: ошибка — %s", alt, exc)

    acc_signals.sort(key=lambda x: x["confidence"], reverse=True)
    logger.info(
        "Accumulation scan завершён: %d сигналов из %d альтов",
        len(acc_signals), len(alts),
    )
    for sig in acc_signals:
        logger.info(
            "  📦 %s | %s | conf=%.0f%% | зона=%.1f%% | RSI=%.1f | %s",
            sig["asset"], sig["strength"], sig["confidence"] * 100,
            sig["sideways_range_pct"], sig["rsi"],
            " | ".join(sig["signals"]),
        )

    return acc_signals


# ---------------------------------------------------------------------------
# Одна итерация скринера
# ---------------------------------------------------------------------------

def _run_iteration(
    args: argparse.Namespace,
    config: dict,
    c: _Components,
) -> dict:
    """Обходит все активы и таймфреймы, возвращает словарь results."""
    assets = (
        [a.strip() for a in args.asset.split(",") if a.strip()]
        if args.asset
        else get_assets(config)
    )
    timeframes = (
        [t.strip() for t in args.timeframes.split(",") if t.strip()]
        if args.timeframes
        else get_timeframes(config)
    )

    logger.info("Активы: %s | ТФ: %s | Свечей: %d", ", ".join(assets), ", ".join(timeframes), args.limit)

    results: dict = {}
    btc_buy_allowed_cache: dict = {}
    funding_rate_cache: dict = {}
    ls_ratio_cache: dict = {}   # сбрасывается каждую итерацию, один запрос на актив

    for asset in assets:
        results[asset] = {}
        for timeframe in timeframes:
            logger.info("-" * 60)
            logger.info("Обработка %s/%s", asset, timeframe)
            try:
                df = c.exchange_manager.get_ohlcv(asset, timeframe, limit=args.limit)
                if df.empty:
                    logger.warning("Нет данных для %s/%s", asset, timeframe)
                    results[asset][timeframe] = {"error": "No data"}
                    continue
                results[asset][timeframe] = _analyze_timeframe(
                    asset, timeframe, df, c,
                    btc_buy_allowed_cache, funding_rate_cache, ls_ratio_cache,
                    args.min_confidence, args.limit,
                )
            except Exception as exc:
                logger.error("Ошибка %s/%s: %s", asset, timeframe, exc, exc_info=True)
                results[asset][timeframe] = {"error": str(exc)}

    # Итоговые бандлы
    rotation_bundle = compute_rotation_bundle(results, anchor_tf="1d", min_confidence=args.min_confidence)
    results["_rotation"] = rotation_bundle
    logger.info(format_rotation_console(rotation_bundle))

    # Multi-TF Confluence — считаем ДО spot_bundle, чтобы confluence влиял на решение
    usdt_assets = [a for a in assets if "/" not in a]
    if usdt_assets:
        logger.info("\n—— Multi-TF Confluence ——")
        for asset in usdt_assets:
            conf = compute_confluence(results, asset)
            results.setdefault(asset, {})["_confluence"] = conf
            logger.info(
                "[%s] %s (%.0f%%) | bull: %s | bear: %s",
                asset, conf["direction"], conf["score"] * 100,
                ", ".join(conf["bullish_tfs"]) or "—",
                ", ".join(conf["bearish_tfs"]) or "—",
            )

    spot_bundle = compute_spot_long_bundle(
        results, anchor_tf="1d", timing_tf="4h", min_confidence=args.min_confidence
    )
    results["_spot_long_entry"] = spot_bundle
    logger.info(format_spot_long_console(spot_bundle))

    merge_context_bundles_into_results(results)
    logger.info(format_context_bundles_console(results))

    # Fear & Greed + BTC Dominance (один раз за итерацию)
    try:
        fg = get_fear_greed()
        btc_dom = get_btc_dominance()
        results["_market_context"] = {"fear_greed": fg, "btc_dominance": btc_dom}
        logger.info(
            "\n—— Рыночный контекст ——\n"
            "Fear & Greed: %d (%s)\n"
            "BTC Dominance: %.1f%% | Сезон: %s | MCap 24h: %+.1f%%",
            fg["value"], fg["zone"],
            btc_dom["btc_dominance"], btc_dom["season"],
            btc_dom["market_cap_change_24h_pct"],
        )
    except Exception as exc:
        logger.warning("Не удалось загрузить рыночный контекст: %s", exc)

    pump_signals = _run_pump_scan(args, config, c)
    results["_pump_signals"] = pump_signals

    acc_signals = _run_accumulation_scan(args, config, c)
    results["_accumulation_signals"] = acc_signals

    return results


# ---------------------------------------------------------------------------
# Экспорт и уведомления
# ---------------------------------------------------------------------------

def _export_results(results: dict, args: argparse.Namespace) -> None:
    """Сохраняет results в JSON или выводит в stdout."""
    if args.export_json:
        output_file = Path(args.output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str, ensure_ascii=False)
        logger.info("Результаты сохранены в %s", args.output)
    else:
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))


def _notify_telegram(
    output_file: str,
    min_confidence: float,
    pump_signals: Optional[list] = None,
    acc_signals: Optional[list] = None,
    results: Optional[dict] = None,
) -> None:
    """Отправляет BUY/SELL и PUMP сигналы в Telegram."""
    try:
        from telegram_bot import (  # noqa: PLC0415
            broadcast_signals,
            broadcast_pump_signals,
            broadcast_accumulation_signals,
            broadcast_spot_overview,
            load_subscribers,
            TELEGRAM_BOT_TOKEN,
        )

        subscribers_path = Path("data/telegram_subscribers.json")

        tg_args = SimpleNamespace(
            asset="",
            timeframes="",
            include_sell=True,
            min_confidence=min_confidence,
        )
        broadcast_signals(
            token=TELEGRAM_BOT_TOKEN,
            subscribers_path=subscribers_path,
            signals_file=Path(output_file),
            args=tg_args,
        )

        if pump_signals:
            broadcast_pump_signals(
                token=TELEGRAM_BOT_TOKEN,
                subscribers_path=subscribers_path,
                pump_signals=pump_signals,
                min_confidence=0.55,
            )

        if acc_signals:
            broadcast_accumulation_signals(
                token=TELEGRAM_BOT_TOKEN,
                subscribers_path=subscribers_path,
                signals=acc_signals,
                min_confidence=0.55,
            )

        if results:
            broadcast_spot_overview(
                token=TELEGRAM_BOT_TOKEN,
                subscribers_path=subscribers_path,
                results=results,
            )
    except Exception as exc:
        logger.warning("Ошибка уведомления Telegram: %s", exc)


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Paid Screener — реальные данные Binance")
    parser.add_argument("--asset", type=str, default="",
                        help="Актив или несколько через запятую (ETH,SOL). Пусто = все из конфига")
    parser.add_argument("--timeframes", type=str, default="",
                        help="Переопределить таймфреймы (необязательно)")
    parser.add_argument("--min-confidence", type=float, default=0.7,
                        help="Минимальная уверенность сигнала")
    parser.add_argument("--export-json", action="store_true", help="Экспорт в JSON")
    parser.add_argument("--output", type=str, default="data/signals.json", help="Файл вывода")
    parser.add_argument("--limit", type=int, default=500, help="Количество свечей")
    parser.add_argument("--config", type=str, default="config/config.yaml",
                        help="Путь к конфигурации")
    parser.add_argument("--loop", action="store_true", help="Бесконечный цикл")
    parser.add_argument("--loop-interval", type=int, default=300,
                        help="Интервал между итерациями, сек (default: 300)")
    parser.add_argument("--pump-scan", action="store_true",
                        help="Сканировать топ-альты на памп (дополнительно к основному анализу)")

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Paid Screener — реальные данные Binance")
    logger.info("=" * 60)

    # Инициализация компонентов один раз до цикла
    init_config = load_config(args.config)
    components = _build_components(args, init_config)
    logger.info("Компоненты инициализированы")

    while True:
        try:
            config = load_config(args.config)  # горячая перезагрузка конфига
            results = _run_iteration(args, config, components)
            _export_results(results, args)

            logger.info("=" * 60)
            logger.info("Анализ завершён")
            logger.info("=" * 60)

            if args.export_json:
                _notify_telegram(
                    args.output,
                    args.min_confidence,
                    pump_signals=results.get("_pump_signals") or [],
                    acc_signals=results.get("_accumulation_signals") or [],
                    results=results,
                )

        except KeyboardInterrupt:
            logger.info("Остановлено вручную.")
            break
        except Exception as exc:
            logger.error("Ошибка итерации: %s", exc, exc_info=True)

        if not args.loop:
            break

        logger.info("Следующий запуск через %d сек... (Ctrl+C для остановки)", args.loop_interval)
        time.sleep(args.loop_interval)


if __name__ == "__main__":
    main()
