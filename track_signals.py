"""Трекер активных сигналов: проверяет TP/SL раз в день и пишет аналитику в Telegram."""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.parsers.exchange_manager import ExchangeManager
from src.storage.database import Database
from src.utils.config import Settings, get_assets, load_config
from telegram_bot import (
    TELEGRAM_BOT_TOKEN,
    load_subscribers,
    send_telegram_message,
)

logger = logging.getLogger(__name__)

SUBSCRIBERS_PATH = Path("data/telegram_subscribers.json")

CONFIG_PATH = "config/config.yaml"


def _tracked_assets() -> set:
    """Активы для трекинга — из config.yaml, а не из захардкоженного списка."""
    return {str(a).upper() for a in get_assets(load_config(CONFIG_PATH))}

_TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}


def _candles_needed(timeframe: str, interval_seconds: int) -> int:
    """Сколько свечей нужно запросить чтобы покрыть весь интервал проверки."""
    tf_sec = _TF_SECONDS.get(timeframe, 3600)
    return max(1, int(interval_seconds / tf_sec) + 1)


def _parse_added_at(signal: Dict[str, Any]) -> datetime:
    """
    Время добавления сигнала в трекер (naive UTC).

    Критично для проверки TP/SL: свечи, закрывшиеся ДО добавления сигнала,
    не должны триггерить стоп — иначе минимум старой свечи "закрывает"
    только что созданный сигнал (реальный баг: BTC/3d закрылся по SL на
    минимуме, случившемся за день до создания сигнала).

    Если метки нет (старые сигналы) — ставим текущее время и сохраняем.
    """
    raw = signal.get("added_at") or signal.get("created_at")
    if raw:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    now = datetime.utcnow()
    signal["added_at"] = now.isoformat()
    return now




# ---------------------------------------------------------------------------
# Логика трекинга
# ---------------------------------------------------------------------------

def get_tp_level(signal: Dict[str, Any]) -> Optional[float]:
    """Возвращает первый TP-уровень из сигнала."""
    for tp in (signal.get("take_profit") or []):
        if isinstance(tp, dict) and isinstance(tp.get("level"), (int, float)):
            return float(tp["level"])
    return None


def get_trail_distance(signal: Dict[str, Any]) -> float:
    """ATR × 1.5, иначе 2% от цены входа."""
    atr = signal.get("atr")
    if atr and float(atr) > 0:
        return float(atr) * 1.5
    return float(signal.get("entry_price", 0)) * 0.02


def calc_trailed_sl(signal: Dict[str, Any], best_price: float) -> float:
    """Trailing SL — активируется после прохождения 1R в сторону прибыли."""
    side = str(signal.get("signal_type") or "").upper()
    entry = float(signal["entry_price"])
    original_sl = float(signal["stop_loss"])
    dist = get_trail_distance(signal)
    risk = abs(entry - original_sl)

    if side == "BUY":
        if best_price < entry + risk:
            return original_sl
        return max(original_sl, best_price - dist)
    else:
        if best_price > entry - risk:
            return original_sl
        return min(original_sl, best_price + dist)


def check_hit(
    price_high: float,
    price_low: float,
    signal_type: str,
    tp_level: float,
    sl_level: float,
) -> Tuple[Optional[str], Optional[float]]:
    """Проверяет, задеты ли TP или SL. SL имеет приоритет."""
    side = (signal_type or "").upper()
    hit_sl = price_low <= sl_level if side == "BUY" else price_high >= sl_level
    hit_tp = price_high >= tp_level if side == "BUY" else price_low <= tp_level

    if hit_sl:
        return "SL", sl_level
    if hit_tp:
        return "TP", tp_level
    return None, None


def build_daily_analytics_message(db: Database) -> str:
    """Формирует суточный отчёт по сигналам для Telegram."""
    analytics = db.get_trade_analytics()
    total = analytics.get("total", 0)
    if total == 0:
        return "📊 Дневная аналитика: закрытых сделок пока нет."

    wins = analytics["wins"]
    losses = analytics["losses"]
    wr = analytics["win_rate"]
    pnl = analytics.get("total_pnl_usd")
    avg_rr = analytics.get("avg_rr_on_wins")

    lines = [
        "📊 <b>Дневная аналитика сигналов</b>",
        f"Всего закрыто: <b>{total}</b> | Победы: {wins} | Потери: {losses}",
        f"Win Rate: <b>{wr:.1f}%</b>",
    ]
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        lines.append(f"Итого P&amp;L: <b>{sign}{pnl:.2f}$</b> (тест $10 риск/сделку)")
    if avg_rr is not None:
        lines.append(f"Средний R/R на победах: <b>{avg_rr:.2f}R</b>")

    by_asset = analytics.get("by_asset", {})
    if by_asset:
        lines.append("\n<b>По активам:</b>")
        for asset, s in sorted(by_asset.items(), key=lambda x: -x[1]["win_rate"]):
            lines.append(f"  {asset}: {s['win_rate']:.0f}%  {s['wins']}W/{s['losses']}L")

    by_tf = analytics.get("by_timeframe", {})
    if by_tf:
        lines.append("\n<b>По таймфреймам:</b>")
        for tf, s in sorted(by_tf.items()):
            lines.append(f"  {tf}: {s['win_rate']:.0f}%  {s['wins']}W/{s['losses']}L")

    day_closed = db.today_closed_count()
    if day_closed:
        lines.append(f"\nЗа сегодня закрыто: <b>{day_closed}</b> сделок")

    return "\n".join(lines)


def build_result_message(
    asset: str,
    timeframe: str,
    signal_type: str,
    strength: str,
    result: str,
    price: float,
    test_pnl_usd: Optional[float] = None,
    test_pnl_rr: Optional[float] = None,
    risk_usd: Optional[float] = None,
    win_rate: Optional[float] = None,
    total_closed: Optional[int] = None,
    wins: Optional[int] = None,
) -> str:
    strength_map = {"STRONG": "СИЛЬНЫЙ", "MEDIUM": "СРЕДНИЙ"}
    strength_ru = strength_map.get((strength or "").upper(), "")
    result_label = {"TP": "TP ✅", "TSL": "Trailing Stop ✅", "SL": "SL ❌"}.get(result, result)

    msg = (
        f"Сделка по сигналу {asset} {timeframe} {signal_type.upper()} "
        f"закрыта по {result_label} по цене {price:.4f}"
        + (f" ({strength_ru})" if strength_ru else "")
    )
    if test_pnl_usd is not None and risk_usd:
        rr_text = f"{test_pnl_rr:+.2f}R" if test_pnl_rr is not None else ""
        msg += f"\nТест при риске {risk_usd:.2f}$: PnL {test_pnl_usd:+.2f}$ {rr_text}"
    if win_rate is not None and total_closed:
        msg += f"\nВсего сделок: {total_closed}, побед: {wins or 0} (успешность {win_rate:.1f}%)"
    return msg


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------

def track_signals(interval_seconds: int = 86400) -> None:
    """Запускает цикл мониторинга активных сигналов с дневным интервалом."""
    exchange_manager = ExchangeManager(["binance"])
    db = Database(Settings().database_url)
    subscribers = load_subscribers(SUBSCRIBERS_PATH)

    if not subscribers:
        logger.warning("Подписчиков нет — трекер запущен, но уведомления отправлять некому.")

    hours = interval_seconds / 3600
    logger.info("Трекер сигналов запущен. Интервал: %.1f ч.", hours)

    while True:
        signals = db.get_open_signals()
        if not signals:
            logger.debug("Нет активных сигналов, ожидание...")
            time.sleep(interval_seconds)
            continue

        logger.info("Проверяем %d активных сигналов...", len(signals))

        try:
            tracked_assets = _tracked_assets()
        except Exception as exc:
            logger.warning("Не удалось загрузить конфиг (%s), используем дефолтный список активов", exc)
            tracked_assets = {str(a).upper() for a in get_assets({})}

        wins = 0
        losses = 0
        total_closed = 0

        for signal in signals:
            db_id: int = signal["_db_id"]
            try:
                asset = str(signal.get("asset") or "")
                timeframe = str(signal.get("timeframe") or "")
                signal_type = str(signal.get("signal_type") or "")

                if not asset or not timeframe or not signal_type:
                    db.update_active_signal(db_id, data=signal)
                    continue

                # manual-сделки (trade.py) трекаем даже если актива нет в конфиге
                if asset.upper() not in tracked_assets and timeframe != "manual":
                    db.update_active_signal(db_id, data=signal)
                    continue

                tp_level = get_tp_level(signal)
                if tp_level is None:
                    db.update_active_signal(db_id, data=signal)
                    continue

                try:
                    entry_price = float(signal["entry_price"])
                    float(signal["stop_loss"])  # обязателен для calc_trailed_sl
                except (KeyError, TypeError, ValueError):
                    logger.warning("[%s/%s] Пропуск: некорректные entry_price/stop_loss", asset, timeframe)
                    db.update_active_signal(db_id, data=signal)
                    continue

                side = signal_type.upper()

                if "best_price" not in signal:
                    signal["best_price"] = entry_price

                fetch_tf = "1h" if timeframe == "manual" else timeframe
                limit = _candles_needed(fetch_tf, interval_seconds)
                try:
                    df = exchange_manager.get_ohlcv(asset, fetch_tf, limit=limit)
                except Exception as exc:
                    logger.warning("Ошибка получения цены для %s/%s: %s", asset, timeframe, exc)
                    db.update_active_signal(db_id, data=signal)
                    time.sleep(1.0)
                    continue

                if df.empty:
                    db.update_active_signal(db_id, data=signal)
                    continue

                time.sleep(0.4)

                added_dt = _parse_added_at(signal)
                tf_sec = _TF_SECONDS.get(fetch_tf, 86400)

                result: Optional[str] = None
                price: Optional[float] = None

                for _, candle in df.iterrows():
                    ts = candle.get("timestamp")
                    ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts

                    if ts_dt is not None:
                        if ts_dt + timedelta(seconds=tf_sec) <= added_dt:
                            continue
                        if ts_dt < added_dt:
                            price_high = price_low = float(candle["close"])
                        else:
                            price_high = float(candle["high"])
                            price_low = float(candle["low"])
                    else:
                        price_high = float(candle["high"])
                        price_low = float(candle["low"])

                    prev_best = float(signal["best_price"])
                    signal["best_price"] = (
                        max(prev_best, price_high) if side == "BUY" else min(prev_best, price_low)
                    )

                    trailed_sl = calc_trailed_sl(signal, float(signal["best_price"]))
                    signal["trailed_sl"] = trailed_sl

                    result, price = check_hit(
                        price_high=price_high,
                        price_low=price_low,
                        signal_type=signal_type,
                        tp_level=tp_level,
                        sl_level=trailed_sl,
                    )

                    if result == "SL":
                        if side == "BUY" and trailed_sl > entry_price:
                            result, price = "TSL", trailed_sl
                        elif side == "SELL" and trailed_sl < entry_price:
                            result, price = "TSL", trailed_sl

                    if result is not None:
                        break

                # trailed_sl отсутствует, если все свечи старше added_at (свежий сигнал на 1d/3d)
                if "trailed_sl" in signal:
                    move_pct = (float(signal["trailed_sl"]) - entry_price) / entry_price * 100
                    logger.info("TSL %s/%s: SL → %.4f (%+.2f%% от входа)", asset, timeframe, float(signal["trailed_sl"]), move_pct)

                if result is None or price is None:
                    db.update_active_signal(db_id, data=signal)
                    continue

                signal["status"] = result
                signal["closed_price"] = price

                test_trade = signal.get("test_trade") or {}
                risk_usd = float(test_trade.get("risk_usd", 0.0)) if isinstance(test_trade, dict) else 0.0
                qty = float(test_trade.get("qty", 0.0)) if isinstance(test_trade, dict) else 0.0

                test_pnl_usd = None
                test_pnl_rr = None
                if qty > 0 and risk_usd > 0:
                    pnl = (price - float(signal["entry_price"])) * qty if side == "BUY" else (float(signal["entry_price"]) - price) * qty
                    test_pnl_usd = pnl
                    test_pnl_rr = pnl / risk_usd
                    signal["test_pnl_usd"] = test_pnl_usd
                    signal["test_pnl_rr"] = test_pnl_rr

                total_closed += 1
                if result in ("TP", "TSL"):
                    wins += 1
                elif result == "SL":
                    losses += 1
                win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0

                db.update_active_signal(db_id, data=signal, status=result)

                logger.info(
                    "Сигнал закрыт: %s/%s %s → %s по цене %.4f | PnL %s",
                    asset, timeframe, signal_type, result, price,
                    f"{test_pnl_usd:+.2f}$" if test_pnl_usd is not None else "N/A",
                )

                try:
                    db.save_trade({
                        "asset": asset,
                        "timeframe": timeframe,
                        "signal_type": signal_type,
                        "strength": str(signal.get("strength") or ""),
                        "entry_price": float(signal["entry_price"]),
                        "exit_price": float(price),
                        "result": result,
                        "confidence": float(signal.get("confidence") or 0.0),
                        "risk_usd": risk_usd if risk_usd > 0 else None,
                        "qty": qty if qty > 0 else None,
                        "pnl_usd": test_pnl_usd,
                        "pnl_rr": test_pnl_rr,
                        "opened_at": None,
                        "closed_at": datetime.utcnow(),
                        "source": "tracker",
                        "signal_snapshot": {k: v for k, v in signal.items() if k != "_db_id"},
                    })
                except Exception as exc:
                    logger.error("Ошибка записи сделки в БД: %s", exc)

                text = build_result_message(
                    asset=asset,
                    timeframe=timeframe,
                    signal_type=signal_type,
                    strength=str(signal.get("strength") or ""),
                    result=result,
                    price=price,
                    test_pnl_usd=test_pnl_usd,
                    test_pnl_rr=test_pnl_rr,
                    risk_usd=risk_usd if risk_usd > 0 else None,
                    win_rate=win_rate,
                    total_closed=total_closed,
                    wins=wins,
                )
                for chat_id in subscribers:
                    try:
                        send_telegram_message(token=TELEGRAM_BOT_TOKEN, chat_id=chat_id, text=text)
                    except Exception as exc:
                        logger.warning("Ошибка отправки результата %s/%s подписчику %d: %s", asset, timeframe, chat_id, exc)
            except Exception:
                # один битый сигнал не должен ронять весь трекер
                logger.exception("Ошибка обработки сигнала id=%s, пропускаем до следующей итерации", db_id)

        # Дневная аналитика — отправляем всем подписчикам
        try:
            analytics_text = build_daily_analytics_message(db)
            for chat_id in subscribers:
                try:
                    send_telegram_message(
                        token=TELEGRAM_BOT_TOKEN,
                        chat_id=chat_id,
                        text=analytics_text,
                    )
                except Exception as exc:
                    logger.warning("Ошибка отправки аналитики подписчику %d: %s", chat_id, exc)
        except Exception as exc:
            logger.error("Ошибка формирования дневной аналитики: %s", exc)

        logger.info("Следующая проверка через %.1f ч.", interval_seconds / 3600)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Трекер сигналов TP/SL + дневная аналитика")
    parser.add_argument(
        "--interval",
        type=int,
        default=86400,
        help="Интервал проверки в секундах (default: 86400 = 24 часа)",
    )
    _args = parser.parse_args()
    track_signals(interval_seconds=_args.interval)
