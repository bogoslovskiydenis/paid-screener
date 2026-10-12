"""Трекер активных сигналов: проверяет TP/SL раз в день и пишет аналитику в Telegram."""
from __future__ import annotations

import argparse
import json
import logging
import threading
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

# Доля позиции, закрываемая на TP1. Остаток едет на трейлинге
# со стопом не ниже безубытка — чтобы не отдавать весь ход за +0.3R.
PARTIAL_EXIT_FRACTION = 0.5


def get_tp_levels(signal: Dict[str, Any]) -> List[float]:
    """Все TP-уровни сигнала по порядку (TP1 — ближайший)."""
    out: List[float] = []
    for tp in (signal.get("take_profit") or []):
        if isinstance(tp, dict) and isinstance(tp.get("level"), (int, float)):
            out.append(float(tp["level"]))
    return out


def get_tp_level(signal: Dict[str, Any]) -> Optional[float]:
    """Возвращает первый TP-уровень из сигнала."""
    levels = get_tp_levels(signal)
    return levels[0] if levels else None


def get_trail_distance(signal: Dict[str, Any]) -> float:
    """ATR × 1.5, иначе 2% от цены входа."""
    atr = signal.get("atr")
    if atr and float(atr) > 0:
        return float(atr) * 1.5
    return float(signal.get("entry_price", 0)) * 0.02


def calc_trailed_sl(signal: Dict[str, Any], best_price: float) -> float:
    """Trailing SL — активируется после прохождения 1R в сторону прибыли.

    После частичного закрытия на TP1 трейлинг включается сразу (1R ждать
    не нужно: цель уже взята), а стоп остатка не опускается ниже безубытка.
    """
    side = str(signal.get("signal_type") or "").upper()
    entry = float(signal["entry_price"])
    original_sl = float(signal["stop_loss"])
    dist = get_trail_distance(signal)
    risk = abs(entry - original_sl)
    tp1_filled = bool(signal.get("tp1_filled"))

    if side == "BUY":
        if tp1_filled:
            return max(original_sl, entry, best_price - dist)
        if best_price < entry + risk:
            return original_sl
        return max(original_sl, best_price - dist)
    else:
        if tp1_filled:
            return min(original_sl, entry, best_price + dist)
        if best_price > entry - risk:
            return original_sl
        return min(original_sl, best_price + dist)


def momentum_still_bullish(side: str, idx: int, closes, ema9, ema21) -> bool:
    """Жив ли тренд на момент касания цели — решает, двигать ли TP выше.

    Смотрим ПРОШЛУЮ свечу (idx-1): внутри текущей порядок хай/лоу неизвестен,
    решение по её закрытию — заглядывание в будущее. Мало истории (<22 свечей
    до касания) → False, закрываемся по цели как раньше.
    """
    j = idx - 1
    if j < 21:
        return False
    if side == "BUY":
        return float(ema9.iloc[j]) > float(ema21.iloc[j]) and float(closes.iloc[j]) > float(ema21.iloc[j])
    return float(ema9.iloc[j]) < float(ema21.iloc[j]) and float(closes.iloc[j]) < float(ema21.iloc[j])


def check_hit(
    price_high: float,
    price_low: float,
    signal_type: str,
    tp_level: Optional[float],
    sl_level: float,
) -> Tuple[Optional[str], Optional[float]]:
    """Проверяет, задеты ли TP или SL. SL имеет приоритет.

    tp_level=None — цели нет (остаток позиции после TP1 без TP2),
    закрытие возможно только по стопу.
    """
    side = (signal_type or "").upper()
    hit_sl = price_low <= sl_level if side == "BUY" else price_high >= sl_level
    hit_tp = (
        False if tp_level is None
        else (price_high >= tp_level if side == "BUY" else price_low <= tp_level)
    )

    if hit_sl:
        return "SL", sl_level
    if hit_tp:
        return "TP", tp_level
    return None, None


def _period_stats_line(label: str, analytics: Dict[str, Any]) -> str:
    """Одна строка сводки за период: сделки, W/L, win rate, P&L."""
    total = analytics.get("total", 0)
    if not total:
        return f"{label}: сделок нет"
    line = (
        f"{label}: <b>{total}</b> | {analytics['wins']}W/{analytics['losses']}L"
        f" | WR <b>{analytics['win_rate']:.1f}%</b>"
    )
    pnl = analytics.get("total_pnl_usd")
    if pnl is not None:
        sign = "+" if pnl >= 0 else ""
        line += f" | P&amp;L <b>{sign}{pnl:.2f}$</b>"
    return line


def build_daily_analytics_message(db: Database) -> str:
    """Отчёт по сигналам для Telegram: сегодня / 7 дней / всё время (UTC)."""
    now = datetime.utcnow()
    day = db.get_trade_analytics(since=now.replace(hour=0, minute=0, second=0, microsecond=0))
    week = db.get_trade_analytics(since=now - timedelta(days=7))
    all_time = db.get_trade_analytics()

    if not all_time.get("total"):
        return "📊 Аналитика сигналов: закрытых сделок пока нет."

    from src.utils.config import get_analysis_config
    risk_usd = get_analysis_config(load_config(CONFIG_PATH)).test_risk_usd
    lines = [
        f"📊 <b>Аналитика сигналов</b> (тест ${risk_usd:g} риск/сделку)",
        _period_stats_line("Сегодня", day),
        _period_stats_line("7 дней", week),
        _period_stats_line("Всё время", all_time),
    ]
    avg_rr = week.get("avg_rr_on_wins")
    if avg_rr is not None:
        lines.append(f"Средний R на победах за 7 дней: <b>{avg_rr:.2f}R</b>")
    # Матожидание — главная метрика: >0 система зарабатывает, <0 сливает
    exp_week = week.get("avg_rr_all")
    exp_all = all_time.get("avg_rr_all")
    if exp_week is not None or exp_all is not None:
        parts = []
        if exp_week is not None:
            parts.append(f"7 дней: <b>{exp_week:+.2f}R</b>")
        if exp_all is not None:
            parts.append(f"всё время: <b>{exp_all:+.2f}R</b>")
        lines.append("Матожидание на сделку — " + " | ".join(parts))

    # Разбивки по активам/ТФ — за неделю: срез за всю историю не показывает,
    # что работает сейчас. Если за неделю пусто — за всю историю.
    detail = week if week.get("total") else all_time
    detail_label = "7 дней" if week.get("total") else "всё время"

    by_asset = detail.get("by_asset", {})
    if by_asset:
        lines.append(f"\n<b>По активам ({detail_label}):</b>")
        for asset, s in sorted(by_asset.items(), key=lambda x: -x[1]["win_rate"]):
            lines.append(
                f"  {asset}: {s['win_rate']:.0f}%  {s['wins']}W/{s['losses']}L  {s['pnl_usd']:+.1f}$"
            )

    by_tf = detail.get("by_timeframe", {})
    if by_tf:
        lines.append(f"\n<b>По таймфреймам ({detail_label}):</b>")
        for tf, s in sorted(by_tf.items()):
            lines.append(
                f"  {tf}: {s['win_rate']:.0f}%  {s['wins']}W/{s['losses']}L  {s['pnl_usd']:+.1f}$"
            )

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
    tp1_price: Optional[float] = None,
    tp1_pnl_usd: Optional[float] = None,
    tp_extensions: int = 0,
) -> str:
    strength_map = {"STRONG": "СИЛЬНЫЙ", "MEDIUM": "СРЕДНИЙ"}
    strength_ru = strength_map.get((strength or "").upper(), "")
    result_label = {
        "TP": "TP ✅",
        "TSL": "Trailing Stop ✅",
        "MOM": "затуханию роста ✅",
        "SL": "SL ❌",
    }.get(result, result)

    msg = (
        f"Сделка по сигналу {asset} {timeframe} {signal_type.upper()} "
        f"закрыта по {result_label} по цене {price:.4f}"
        + (f" ({strength_ru})" if strength_ru else "")
    )
    if tp1_price is not None:
        part_txt = f" ({tp1_pnl_usd:+.2f}$)" if tp1_pnl_usd is not None else ""
        msg += (
            f"\nЧастями: {PARTIAL_EXIT_FRACTION * 100:.0f}% на TP1 {tp1_price:.4f}{part_txt}"
            f" + остаток по {price:.4f}"
        )
    if tp_extensions:
        msg += f"\nЦель продлевалась {tp_extensions} раз — тренд продолжался"
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

                tp_levels = get_tp_levels(signal)
                if not tp_levels:
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

                test_trade = signal.get("test_trade") or {}
                risk_usd = float(test_trade.get("risk_usd", 0.0)) if isinstance(test_trade, dict) else 0.0
                qty = float(test_trade.get("qty", 0.0)) if isinstance(test_trade, dict) else 0.0

                fetch_tf = "1h" if timeframe == "manual" else timeframe
                # минимум 60 свечей — нужны для EMA9/21 при решении двигать TP
                limit = max(_candles_needed(fetch_tf, interval_seconds), 60)
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

                # Для решения «двигать ли цель»: EMA9/21 по всей загруженной истории
                closes_series = df["close"].astype(float)
                ema9_series = closes_series.ewm(span=9, adjust=False).mean()
                ema21_series = closes_series.ewm(span=21, adjust=False).mean()

                result: Optional[str] = None
                price: Optional[float] = None

                for row_i, (_, candle) in enumerate(df.iterrows()):
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

                    # Трейлим по best_price ПРОШЛЫХ свечей: порядок хай/лоу внутри
                    # текущей свечи неизвестен, подтягивать стоп её же хаем и тут же
                    # проверять её лоу — заглядывание в будущее
                    prev_best = float(signal["best_price"])
                    trailed_sl = calc_trailed_sl(signal, prev_best)
                    signal["trailed_sl"] = trailed_sl

                    # Текущая цель: до частичного закрытия — TP1, после — TP2+
                    # с возможным продлением; выше последнего TP — только трейлинг
                    tp1_filled = bool(signal.get("tp1_filled"))
                    tp_idx = int(signal.get("tp_index", 1 if tp1_filled else 0))
                    tp_target: Optional[float] = (
                        tp_levels[tp_idx] if tp_idx < len(tp_levels) else None
                    )

                    # После TP1 рост затух (EMA по закрытию ПРОШЛОЙ свечи) —
                    # закрываем остаток по открытию текущей, не дожидаясь ни
                    # следующей цели, ни отката до трейлинг-стопа. При гэпе
                    # сквозь стоп ветка не срабатывает — ниже отработает SL/TSL.
                    # row_i >= 22 — иначе False означает «мало истории», не слом.
                    if tp1_filled and row_i >= 22 and not momentum_still_bullish(
                        side, row_i, closes_series, ema9_series, ema21_series
                    ):
                        open_price = float(candle.get("open", candle["close"]))
                        above_stop = (
                            open_price > trailed_sl if side == "BUY" else open_price < trailed_sl
                        )
                        if above_stop:
                            result, price = "MOM", open_price
                            break

                    result, price = check_hit(
                        price_high=price_high,
                        price_low=price_low,
                        signal_type=signal_type,
                        tp_level=tp_target,
                        sl_level=trailed_sl,
                    )

                    signal["best_price"] = (
                        max(prev_best, price_high) if side == "BUY" else min(prev_best, price_low)
                    )

                    if result == "TP" and not tp1_filled:
                        # Частичное закрытие на TP1: фиксируем долю, остаток едет
                        # дальше на трейлинге со стопом не ниже безубытка
                        signal["tp1_filled"] = True
                        signal["tp1_price"] = price
                        signal["tp_index"] = 1
                        if qty > 0:
                            closed_qty = qty * PARTIAL_EXIT_FRACTION
                            part_pnl = (
                                (price - entry_price) * closed_qty if side == "BUY"
                                else (entry_price - price) * closed_qty
                            )
                            signal["realized_pnl_usd"] = part_pnl
                        result, price = None, None
                        # остальные проверки этой же свечи пропускаем: порядок
                        # хай/лоу внутри свечи неизвестен (заглядывание в будущее)
                        continue

                    if result == "TP" and tp1_filled and momentum_still_bullish(
                        side, row_i, closes_series, ema9_series, ema21_series
                    ):
                        # Цена дошла до цели, но тренд жив — двигаем TP на
                        # следующий уровень вместо закрытия; выше последнего
                        # уровня остаток ведёт только трейлинг-стоп
                        signal["tp_index"] = tp_idx + 1
                        signal["tp_extensions"] = int(signal.get("tp_extensions") or 0) + 1
                        logger.info(
                            "TP продлён %s/%s: цель %.4f взята, тренд жив → следующая %s",
                            asset, timeframe, price,
                            f"{tp_levels[tp_idx + 1]:.4f}" if tp_idx + 1 < len(tp_levels) else "трейлинг",
                        )
                        result, price = None, None
                        continue

                    if result == "SL":
                        if side == "BUY" and trailed_sl > entry_price:
                            result, price = "TSL", trailed_sl
                        elif side == "SELL" and trailed_sl < entry_price:
                            result, price = "TSL", trailed_sl
                        elif tp1_filled:
                            # стоп остатка в безубытке, часть прибыли уже взята
                            result, price = "TSL", trailed_sl

                    if result is not None:
                        break

                # trailed_sl отсутствует, если все свечи старше added_at (свежий сигнал на 1d/3d)
                if "trailed_sl" in signal:
                    move_pct = (float(signal["trailed_sl"]) - entry_price) / entry_price * 100
                    logger.info("TSL %s/%s: SL → %.4f (%+.2f%% от входа)", asset, timeframe, float(signal["trailed_sl"]), move_pct)

                # TP1 взят частично — сообщаем один раз, позиция остаётся активной
                if signal.get("tp1_filled") and not signal.get("tp1_notified"):
                    signal["tp1_notified"] = True
                    part_pnl = signal.get("realized_pnl_usd")
                    part_txt = f" ({float(part_pnl):+.2f}$)" if part_pnl is not None else ""
                    partial_text = (
                        f"🎯 {asset} {timeframe} {signal_type.upper()}: TP1 "
                        f"{float(signal['tp1_price']):.4f} достигнут — закрыто "
                        f"{PARTIAL_EXIT_FRACTION * 100:.0f}%{part_txt}\n"
                        f"Остаток в трейлинге, стоп не ниже безубытка {entry_price:.4f}"
                    )
                    for chat_id in subscribers:
                        try:
                            send_telegram_message(token=TELEGRAM_BOT_TOKEN, chat_id=chat_id, text=partial_text)
                        except Exception as exc:
                            logger.warning("Ошибка отправки TP1-уведомления %s/%s подписчику %d: %s", asset, timeframe, chat_id, exc)

                if result is None or price is None:
                    db.update_active_signal(db_id, data=signal)
                    continue

                signal["status"] = result
                signal["closed_price"] = price

                test_pnl_usd = None
                test_pnl_rr = None
                if qty > 0 and risk_usd > 0:
                    remaining_qty = qty * (1.0 - PARTIAL_EXIT_FRACTION) if signal.get("tp1_filled") else qty
                    pnl = (
                        (price - entry_price) * remaining_qty if side == "BUY"
                        else (entry_price - price) * remaining_qty
                    )
                    pnl += float(signal.get("realized_pnl_usd") or 0.0)
                    test_pnl_usd = pnl
                    test_pnl_rr = pnl / risk_usd
                    signal["test_pnl_usd"] = test_pnl_usd
                    signal["test_pnl_rr"] = test_pnl_rr

                total_closed += 1
                if result in ("TP", "TSL", "MOM"):
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
                        "opened_at": _parse_added_at(signal),
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
                    tp1_price=float(signal["tp1_price"]) if signal.get("tp1_filled") and signal.get("tp1_price") else None,
                    tp1_pnl_usd=float(signal["realized_pnl_usd"]) if signal.get("realized_pnl_usd") is not None else None,
                    tp_extensions=int(signal.get("tp_extensions") or 0),
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


def start_tracker_thread(interval_seconds: int = 86400) -> threading.Thread:
    """Запускает трекер фоновым потоком внутри процесса сканера.

    Раньше трекер был отдельным процессом и после перезагрузки бота его
    забывали поднять — открытые сигналы висели незакрытыми (реальный кейс:
    с 16 июня по 20 сентября 2026). Теперь он живёт и умирает вместе со
    сканером. При исключении внутри цикла поток перезапускает трекер сам.
    """

    def _runner() -> None:
        while True:
            try:
                track_signals(interval_seconds=interval_seconds)
            except Exception:
                logger.exception("Трекер упал, перезапуск через 60 сек")
                time.sleep(60)

    thread = threading.Thread(target=_runner, name="signal-tracker", daemon=True)
    thread.start()
    logger.info("Трекер сигналов запущен фоновым потоком (интервал %.1f ч)", interval_seconds / 3600)
    return thread


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
