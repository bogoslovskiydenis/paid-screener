"""Полный анализ одного актива/таймфрейма."""
import pandas as pd

from ..utils.logger import setup_logger
from ..utils.format_price import fmt_price
from ..analytics.market_sentiment import analyze_market_sentiment
from ..analytics.market_structure import detect_structure, detect_order_blocks
from ..analytics.orderbook_analyzer import analyze_orderbook
from ..storage.signals_store import has_active_sell, has_active_buy, add_signal_to_tracker
from ..notifications.telegram_notify import execute_signal
from .btc_filter import btc_allows_buy
from .components import Components

logger = setup_logger(__name__)

FUNDING_USDT_ASSETS = frozenset({"ETH", "SOL", "BTC"})
FUNDING_RATE_THRESHOLD = 0.0005


def analyze_timeframe(
    asset: str,
    timeframe: str,
    df: pd.DataFrame,
    c: Components,
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

    rsi_analysis = c.rsi_calculator.analyze(df)
    rsi_value = rsi_analysis.get("rsi")
    rsi_zone = rsi_analysis.get("rsi_zone", "NEUTRAL")
    rsi_signal = rsi_analysis.get("rsi_signal", "NEUTRAL")
    if rsi_value:
        logger.info("[%s/%s] RSI: %.1f (%s) → %s", asset, timeframe, rsi_value, rsi_zone, rsi_signal)

    pattern = c.candlestick_analyzer.analyze(df)
    if pattern:
        logger.info("[%s/%s] Свечной паттерн: %s", asset, timeframe, pattern)

    hs_pattern = c.pattern_analyzer.detect(df)
    if hs_pattern:
        logger.info(
            "[%s/%s] Г&П: %s (%s)",
            asset, timeframe, hs_pattern["pattern_type"], hs_pattern["pattern_direction"],
        )

    chart_patterns = c.chart_pattern_detector.detect_all(df)
    for cp in (chart_patterns or []):
        logger.info("[%s/%s] Паттерн: %s (%s)", asset, timeframe, cp["pattern_type"], cp["pattern_direction"])

    # BTC-фильтр (только для USDT-альтов)
    _btc_buy_allowed = True
    if "/" not in asset and asset in ("ETH", "SOL"):
        if timeframe in btc_buy_allowed_cache:
            _btc_buy_allowed = btc_buy_allowed_cache[timeframe]
        else:
            try:
                btc_df = c.exchange_manager.get_ohlcv("BTC", timeframe, limit=limit)
            except Exception as exc:
                logger.warning("Не удалось загрузить BTC для фильтра: %s", exc)
                btc_df = None
            _btc_buy_allowed = btc_allows_buy(btc_df, c.levels_analyzer)
            btc_buy_allowed_cache[timeframe] = _btc_buy_allowed

    funding_rate = None
    if "/" not in asset and asset in FUNDING_USDT_ASSETS:
        if asset not in funding_rate_cache:
            funding_rate_cache[asset] = c.exchange_manager.get_funding_rate(asset)
        funding_rate = funding_rate_cache[asset]
        if funding_rate is not None:
            logger.info("[%s/%s] Funding Rate: %+.4f%%", asset, timeframe, funding_rate * 100)

    signal = c.signal_generator.generate_signal(asset, timeframe, df)

    if signal and signal.get("signal_type") == "BUY" and not _btc_buy_allowed:
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

    sentiment_result = None
    if "/" not in asset and asset in FUNDING_USDT_ASSETS:
        if asset not in ls_ratio_cache:
            ls_ratio_cache[asset] = c.exchange_manager.get_long_short_ratio(asset)
        long_ratio = ls_ratio_cache.get(asset)

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
        is_new = add_signal_to_tracker(signal)
        if is_new:
            if signal.get("signal_type") == "BUY" and has_active_sell(asset):
                logger.info("[%s/%s] BUY заблокирован — есть активный SELL по %s", asset, timeframe, asset)
            elif signal.get("signal_type") == "BUY" and has_active_buy(asset, exclude_tf=timeframe):
                logger.info("[%s/%s] BUY заблокирован — %s уже куплен на другом таймфрейме", asset, timeframe, asset)
            else:
                if signal.get("signal_type") == "SELL" and has_active_buy(asset, exclude_tf=timeframe):
                    logger.warning(
                        "[%s/%s] ⚠️ КОНФЛИКТ: SELL (conf=%.0f%%) по %s при открытой BUY-позиции — "
                        "автопродажа отключена, проверь позицию вручную!",
                        asset, timeframe, signal.get("confidence", 0) * 100, asset,
                    )
                execute_signal(asset, signal, timeframe)
    else:
        logger.info("[%s/%s] Сигнал не сгенерирован (низкая уверенность)", asset, timeframe)

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

    _vwap = c.vwap_calculator.analyze(df)
    if _vwap.get("vwap"):
        logger.info(
            "[%s/%s] VWAP=%s (%s, %.1f%%)",
            asset, timeframe, fmt_price(_vwap["vwap"]),
            _vwap["vwap_signal"], _vwap["vwap_distance_pct"],
        )

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

    _structure = detect_structure(df)
    if _structure["structure"] != "UNKNOWN":
        parts = [f"Структура: {_structure['structure']} ({_structure['trend_strength']:.0%})"]
        if _structure.get("bos"):
            parts.append(_structure["bos"]["description"])
        if _structure.get("choch"):
            parts.append(_structure["choch"]["description"])
        logger.info("[%s/%s] %s", asset, timeframe, " | ".join(parts))

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
