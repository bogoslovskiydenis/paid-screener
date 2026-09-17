"""Главная итерация скринера: обход всех активов и таймфреймов."""
import json
import argparse
from pathlib import Path

from ..utils.logger import setup_logger
from ..utils.config import get_assets, get_timeframes
from ..utils.fear_greed import get_fear_greed
from ..utils.btc_dominance import get_btc_dominance
from ..analytics.rotation_filters import compute_rotation_bundle, format_rotation_console
from ..analytics.entry_spot_filters import compute_spot_long_bundle, format_spot_long_console
from ..analytics.context_bundles import merge_context_bundles_into_results, format_context_bundles_console
from ..analytics.multi_tf_confluence import compute_confluence
from .components import Components
from .timeframe_analyzer import analyze_timeframe
from .pump_scan import run_pump_scan
from .accumulation_scan import run_accumulation_scan

logger = setup_logger(__name__)


def run_iteration(args: argparse.Namespace, config: dict, c: Components) -> dict:
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
    ls_ratio_cache: dict = {}

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
                results[asset][timeframe] = analyze_timeframe(
                    asset, timeframe, df, c,
                    btc_buy_allowed_cache, funding_rate_cache, ls_ratio_cache,
                    args.min_confidence, args.limit,
                )
            except Exception as exc:
                logger.error("Ошибка %s/%s: %s", asset, timeframe, exc, exc_info=True)
                results[asset][timeframe] = {"error": str(exc)}

    rotation_bundle = compute_rotation_bundle(results, anchor_tf="1d", min_confidence=args.min_confidence)
    results["_rotation"] = rotation_bundle
    logger.info(format_rotation_console(rotation_bundle))

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

    pump_signals = run_pump_scan(args, config, c)
    results["_pump_signals"] = pump_signals

    acc_signals = run_accumulation_scan(args, config, c)
    results["_accumulation_signals"] = acc_signals

    return results


def export_results(results: dict, args: argparse.Namespace) -> None:
    """Сохраняет results в JSON или выводит в stdout."""
    if args.export_json:
        output_file = Path(args.output)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str, ensure_ascii=False)
        logger.info("Результаты сохранены в %s", args.output)
    else:
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
