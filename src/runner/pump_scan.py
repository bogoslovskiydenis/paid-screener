"""Сканер памп-сигналов среди топ-альтов."""
from ..utils.logger import setup_logger
from ..utils.fear_greed import get_fear_greed
from ..analytics.pump_detector import AltPumpDetector
from .components import Components

logger = setup_logger(__name__)


def run_pump_scan(args, config: dict, c: Components) -> list:
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

    fg = get_fear_greed()
    fg_value = fg.get("value", 50)
    if fg_value < 15:
        detector.min_score = max(detector.min_score, 0.65)
        logger.info("Pump scan: F&G=%d (EXTREME_FEAR) → min_score повышен до %.2f", fg_value, detector.min_score)
    elif fg_value < 25:
        detector.min_score = max(detector.min_score, 0.55)
        logger.info("Pump scan: F&G=%d (FEAR) → min_score повышен до %.2f", fg_value, detector.min_score)

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
