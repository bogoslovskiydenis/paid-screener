from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.parsers.exchange_manager import ExchangeManager
from telegram_bot import (
    TELEGRAM_BOT_TOKEN,
    load_subscribers,
    send_telegram_message,
)


ACTIVE_SIGNALS_PATH = Path("data/active_signals.json")
SUBSCRIBERS_PATH = Path("data/telegram_subscribers.json")
STATS_PATH = Path("data/trade_stats.json")

# Только эти активы отслеживаем — остальные (акции) пропускаем
BINANCE_ASSETS = {"ETH", "SOL", "BTC"}


def load_active_signals(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    return data


def save_active_signals(path: Path, signals: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)


def load_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {
            "total_closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
        }
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {
                "total_closed": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
            }
    if not isinstance(data, dict):
        return {
            "total_closed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
        }
    return data


def save_stats(path: Path, stats: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def get_tp_level(signal: Dict[str, Any]) -> Optional[float]:
    tp_list = signal.get("take_profit") or []
    for tp in tp_list:
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
    """Trailing SL активируется только после прохождения 1R в сторону прибыли."""
    side = str(signal.get("signal_type") or "").upper()
    entry = float(signal.get("entry_price"))
    original_sl = float(signal.get("stop_loss"))
    dist = get_trail_distance(signal)
    risk = abs(entry - original_sl)

    if side == "BUY":
        # Начинаем тянуть только когда цена прошла хотя бы 1R вверх
        if best_price < entry + risk:
            return original_sl
        return max(original_sl, best_price - dist)
    else:
        # Начинаем тянуть только когда цена прошла хотя бы 1R вниз
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
    side = (signal_type or "").upper()

    if side == "BUY":
        hit_sl = price_low <= sl_level
        hit_tp = price_high >= tp_level
    else:
        hit_sl = price_high >= sl_level
        hit_tp = price_low <= tp_level

    # SL приоритет: если оба задеты на одной свече — считаем убыток
    if hit_sl:
        return "SL", sl_level
    if hit_tp:
        return "TP", tp_level
    return None, None


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
    side = (signal_type or "").upper()
    strength_ru = ""
    strength_upper = (strength or "").upper()
    if strength_upper == "STRONG":
        strength_ru = "СИЛЬНЫЙ"
    elif strength_upper == "MEDIUM":
        strength_ru = "СРЕДНИЙ"

    direction_text = "SELL" if side == "SELL" else "BUY"
    result_label = {
        "TP": "TP ✅",
        "TSL": "Trailing Stop ✅",
        "SL": "SL ❌",
    }.get(result, result)

    base = (
        f"Сделка по сигналу {asset} {timeframe} {direction_text} "
        f"закрыта по {result_label} по цене {price:.4f} {f'({strength_ru})' if strength_ru else ''}"
    )

    if test_pnl_usd is not None and risk_usd:
        rr_text = f"{test_pnl_rr:+.2f}R" if test_pnl_rr is not None else ""
        base += f"\nТест при риске {risk_usd:.2f}$: PnL {test_pnl_usd:+.2f}$ {rr_text}"

    if win_rate is not None and total_closed:
        base += f"\nВсего закрытых сделок: {total_closed}, из них прибыльных: {wins or 0} (успешность {win_rate:.2f}%)"

    return base


def track_signals(interval_seconds: int = 60) -> None:
    exchange_manager = ExchangeManager(["binance", "investing"])
    subscribers = load_subscribers(SUBSCRIBERS_PATH)
    if not subscribers:
        print("Подписчиков нет, отслеживать некому.")
        return

    while True:
        signals = load_active_signals(ACTIVE_SIGNALS_PATH)
        if not signals:
            time.sleep(interval_seconds)
            continue

        updated_signals: List[Dict[str, Any]] = []
        stats = load_stats(STATS_PATH)
        total_closed = int(stats.get("total_closed", 0) or 0)
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)

        for signal in signals:
            if signal.get("status") in {"TP", "SL"}:
                updated_signals.append(signal)
                continue

            asset = str(signal.get("asset") or "")
            timeframe = str(signal.get("timeframe") or "")
            signal_type = str(signal.get("signal_type") or "")

            if not asset or not timeframe or not signal_type:
                updated_signals.append(signal)
                continue

            # Акции не отслеживаем — только Binance крипто
            if asset.upper() not in BINANCE_ASSETS:
                updated_signals.append(signal)
                continue

            tp_level = get_tp_level(signal)
            if tp_level is None:
                updated_signals.append(signal)
                continue

            entry_price = float(signal.get("entry_price"))
            side = signal_type.upper()

            # Инициализируем best_price при первом цикле
            if "best_price" not in signal:
                signal["best_price"] = entry_price

            try:
                df = exchange_manager.get_ohlcv(asset, timeframe, limit=1)
            except Exception as exc:
                print(f"Ошибка получения цены для {asset} {timeframe}: {exc}")
                updated_signals.append(signal)
                time.sleep(1.0)
                continue

            if df.empty:
                updated_signals.append(signal)
                continue

            time.sleep(0.4)

            last = df.iloc[-1]
            price_high = float(last["high"])
            price_low = float(last["low"])

            # Обновляем best_price в сторону прибыли
            prev_best = float(signal["best_price"])
            if side == "BUY":
                signal["best_price"] = max(prev_best, price_high)
            else:
                signal["best_price"] = min(prev_best, price_low)

            # Рассчитываем trailing SL
            trailed_sl = calc_trailed_sl(signal, float(signal["best_price"]))
            prev_trailed = signal.get("trailed_sl")
            signal["trailed_sl"] = trailed_sl

            # Логируем движение SL
            if prev_trailed is None or abs(trailed_sl - float(prev_trailed)) > 0.0001:
                move_pct = (trailed_sl - entry_price) / entry_price * 100
                print(f"  TSL {asset} {timeframe}: SL → {trailed_sl:.4f} ({move_pct:+.2f}% от входа)")

            result, price = check_hit(
                price_high=price_high,
                price_low=price_low,
                signal_type=signal_type,
                tp_level=tp_level,
                sl_level=trailed_sl,
            )

            # Если SL сработал в прибыли — это TSL (победа), не обычный SL
            if result == "SL":
                if side == "BUY" and trailed_sl > entry_price:
                    result = "TSL"
                    price = trailed_sl
                elif side == "SELL" and trailed_sl < entry_price:
                    result = "TSL"
                    price = trailed_sl

            if result is None or price is None:
                updated_signals.append(signal)
                continue

            signal["status"] = result
            signal["closed_price"] = price

            test_trade = signal.get("test_trade") or {}
            risk_usd = float(test_trade.get("risk_usd", 0.0)) if isinstance(test_trade, dict) else 0.0
            qty = float(test_trade.get("qty", 0.0)) if isinstance(test_trade, dict) else 0.0

            test_pnl_usd = None
            test_pnl_rr = None
            if qty > 0 and risk_usd > 0:
                side = (signal_type or "").upper()
                if side == "BUY":
                    pnl = (price - float(signal.get("entry_price"))) * qty
                else:
                    pnl = (float(signal.get("entry_price")) - price) * qty
                test_pnl_usd = pnl
                test_pnl_rr = pnl / risk_usd

                signal["test_pnl_usd"] = test_pnl_usd
                signal["test_pnl_rr"] = test_pnl_rr

            # обновляем статистику успешности (TSL = победа)
            total_closed += 1
            if result in ("TP", "TSL"):
                wins += 1
            elif result == "SL":
                losses += 1

            win_rate = (wins / total_closed) * 100.0 if total_closed > 0 else 0.0
            stats.update(
                {
                    "total_closed": total_closed,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                }
            )

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
                    send_telegram_message(
                        token=TELEGRAM_BOT_TOKEN,
                        chat_id=chat_id,
                        text=text,
                    )
                except Exception as exc:
                    print(f"Ошибка отправки результата {asset} {timeframe} подписчику {chat_id}: {exc}")

            updated_signals.append(signal)

        # оставляем только незакрытые сигналы
        open_signals = [s for s in updated_signals if s.get("status") not in {"TP", "SL", "TSL"}]
        save_active_signals(ACTIVE_SIGNALS_PATH, open_signals)
        save_stats(STATS_PATH, stats)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    track_signals()

