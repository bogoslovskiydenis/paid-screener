"""Сканер сигналов накопления среди топ-альтов."""
from ..utils.logger import setup_logger
from ..analytics.accumulation_detector import AccumulationDetector
from .components import Components

logger = setup_logger(__name__)


def run_accumulation_scan(args, config: dict, c: Components) -> list:
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
