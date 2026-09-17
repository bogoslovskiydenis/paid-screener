"""Отправка уведомлений в Telegram и исполнение сигналов."""
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

_LIQ_SEND_INTERVAL = 4 * 3600
_last_liq_send: float = 0.0

_MARKET_OVERVIEW_INTERVAL = 3600
_last_market_overview_send: float = 0.0


def execute_signal(asset: str, signal: dict, timeframe: str) -> None:
    """Передаёт сигнал в Binance executor и уведомляет в Telegram."""
    if not os.getenv("BINANCE_API_KEY"):
        return
    try:
        from ..trading.binance_executor import execute_signal as _exec
        result = _exec(asset=asset, signal=signal, timeframe=timeframe)
        if result:
            try:
                from telegram_bot import broadcast_trade_executed, TELEGRAM_BOT_TOKEN
                broadcast_trade_executed(
                    token=TELEGRAM_BOT_TOKEN,
                    subscribers_path=Path("data/telegram_subscribers.json"),
                    trade=result,
                )
            except Exception as tg_exc:
                logger.warning("Ошибка Telegram уведомления об ордере: %s", tg_exc)
    except Exception as exc:
        logger.warning("Ошибка Binance executor: %s", exc)


def notify_telegram(
    output_file: str,
    min_confidence: float,
    pump_signals: Optional[list] = None,
    acc_signals: Optional[list] = None,
    results: Optional[dict] = None,
) -> None:
    """Отправляет BUY/SELL, PUMP и accumulation сигналы в Telegram."""
    global _last_market_overview_send
    try:
        from telegram_bot import (
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
            _now = time.time()
            if _now - _last_market_overview_send >= _MARKET_OVERVIEW_INTERVAL:
                broadcast_spot_overview(
                    token=TELEGRAM_BOT_TOKEN,
                    subscribers_path=subscribers_path,
                    results=results,
                )
                _last_market_overview_send = _now
    except Exception as exc:
        logger.warning("Ошибка уведомления Telegram: %s", exc)


def maybe_send_liq_levels(exchange_manager) -> None:
    """Отправляет ликвидационные уровни ETH и BTC в Telegram раз в 4 часа."""
    global _last_liq_send
    now = time.time()
    if now - _last_liq_send < _LIQ_SEND_INTERVAL:
        return

    try:
        from ..analytics.liquidation_analyzer import LiquidationAnalyzer
        from telegram_bot import broadcast_liq_levels, load_subscribers, TELEGRAM_BOT_TOKEN

        subscribers_path = Path("data/telegram_subscribers.json")
        if not load_subscribers(subscribers_path):
            return

        analyzer = LiquidationAnalyzer()
        liq_results = []

        for sym, usdt_sym in [("ETH", "ETHUSDT"), ("BTC", "BTCUSDT")]:
            try:
                ohlcv_15m = exchange_manager.get_ohlcv(sym, "15m", limit=200)
                ohlcv_1h = exchange_manager.get_ohlcv(sym, "1h", limit=168)
                if ohlcv_15m.empty or ohlcv_1h.empty:
                    continue
                current_price = float(ohlcv_1h["close"].iloc[-1])
                res = analyzer.analyze(
                    symbol=usdt_sym,
                    current_price=current_price,
                    ohlcv_15m=ohlcv_15m,
                    ohlcv_1h=ohlcv_1h,
                    hours=48,
                )
                liq_results.append(res)
                time.sleep(0.5)
            except Exception as e:
                logger.warning("Liq scan %s: %s", sym, e)

        if liq_results:
            broadcast_liq_levels(
                token=TELEGRAM_BOT_TOKEN,
                subscribers_path=subscribers_path,
                liq_results=liq_results,
            )
            _last_liq_send = now
            logger.info("Ликвидационные уровни отправлены в Telegram.")

    except Exception as exc:
        logger.warning("maybe_send_liq_levels: %s", exc)
