"""
Исполнитель ордеров на Binance Spot.

Режимы:
  BINANCE_PAPER_TRADING=true  — логирует сделки, не отправляет ордера (по умолчанию)
  BINANCE_PAPER_TRADING=false — реальные ордера

Переменные окружения (в .env):
  BINANCE_API_KEY
  BINANCE_API_SECRET
  BINANCE_PAPER_TRADING   (true/false, default=true)
  BINANCE_RISK_PCT        (риск на сделку в % от баланса USDT, default=1.0)
"""

import math
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PAPER_TRADES_PATH = Path("data/paper_trades.json")

# ---------------------------------------------------------------------------
# Конфиг из .env
# ---------------------------------------------------------------------------

def _is_paper() -> bool:
    return os.getenv("BINANCE_PAPER_TRADING", "true").lower() != "false"

def _risk_pct() -> float:
    return float(os.getenv("BINANCE_RISK_PCT", "1.0"))

def _max_position_pct() -> float:
    """Максимальный размер позиции в % от баланса (защита от огромных позиций при тесном SL)."""
    return float(os.getenv("BINANCE_MAX_POSITION_PCT", "20.0"))

def _tp_pct() -> float:
    """Фиксированный TP в % от цены покупки. 0 = брать TP из сигнала."""
    return float(os.getenv("BINANCE_TP_PCT", "0"))

def _get_client():
    from binance.client import Client
    from dotenv import load_dotenv
    load_dotenv()
    api_key    = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET не заданы в .env")
    return Client(api_key, api_secret)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _symbol(asset: str) -> str:
    """'ETH' → 'ETHUSDT', 'ETH/BTC' → пропускаем (только USDT-пары)."""
    if "/" in asset:
        return asset.replace("/", "")
    return asset + "USDT"

def _usdt_balance(client) -> float:
    account = client.get_account()
    for b in account["balances"]:
        if b["asset"] == "USDT":
            return float(b["free"])
    return 0.0

def _round_qty(qty: float, step_size: str) -> float:
    """Округляет qty вниз (floor) по stepSize лота — Binance требует строгого кратного."""
    step = float(step_size)
    if step <= 0:
        return qty
    return math.floor(qty / step) * step

def _lot_filters(client, symbol: str) -> tuple[str, float, float]:
    """Возвращает (stepSize, minQty, minNotional) для символа."""
    info = client.get_symbol_info(symbol)
    step_size = "0.001"
    min_qty = 0.0
    min_notional = 5.0
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            step_size = f["stepSize"]
            min_qty = float(f["minQty"])
        elif f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
            min_notional = float(f.get("minNotional") or f.get("minNotionalValue") or 5.0)
    return step_size, min_qty, min_notional

def _price_tick(client, symbol: str) -> str:
    info = client.get_symbol_info(symbol)
    for f in info["filters"]:
        if f["filterType"] == "PRICE_FILTER":
            return f["tickSize"]
    return "0.01"

def _round_price(price: float, tick: str) -> float:
    decimals = len(tick.rstrip("0").split(".")[-1]) if "." in tick else 0
    return round(price, decimals)


# ---------------------------------------------------------------------------
# Paper trading
# ---------------------------------------------------------------------------

def _log_paper_trade(record: Dict[str, Any]) -> None:
    trades = []
    if PAPER_TRADES_PATH.exists():
        try:
            trades = json.loads(PAPER_TRADES_PATH.read_text())
        except Exception:
            trades = []
    trades.append(record)
    PAPER_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAPER_TRADES_PATH.write_text(json.dumps(trades, ensure_ascii=False, indent=2, default=str))


# ---------------------------------------------------------------------------
# Основная функция
# ---------------------------------------------------------------------------

def execute_signal(
    asset: str,
    signal: Dict[str, Any],
    timeframe: str = "?",
) -> Optional[Dict[str, Any]]:
    """
    Исполняет сигнал BUY или SELL для спот-актива.

    signal — dict с полями: signal_type, entry_price, stop_loss, take_profit
    Возвращает dict с результатом или None при ошибке.
    """
    signal_type: str = signal.get("signal_type", "")
    if signal_type not in ("BUY", "SELL"):
        return None

    if signal_type == "SELL":
        logger.info("[executor] SELL %s — только уведомление, автопродажа отключена", asset)
        return None

    if "/" in asset:
        logger.info("[executor] Кросс-пара %s пропущена (только USDT)", asset)
        return None

    symbol = _symbol(asset)
    entry  = float(signal.get("entry_price") or 0)
    sl     = float(signal.get("stop_loss") or 0)
    tp_list = signal.get("take_profit") or []
    tp1    = float(tp_list[0]["level"]) if tp_list and isinstance(tp_list[0], dict) else None

    if not entry or not sl:
        logger.warning("[executor] %s/%s: нет entry/sl, пропускаем", asset, timeframe)
        return None

    paper = _is_paper()
    risk  = _risk_pct()

    if paper:
        # Paper trading — просто логируем
        record = {
            "ts": datetime.utcnow().isoformat(),
            "mode": "PAPER",
            "asset": asset,
            "symbol": symbol,
            "timeframe": timeframe,
            "signal_type": signal_type,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp1,
            "risk_pct": risk,
        }
        _log_paper_trade(record)
        logger.info(
            "[PAPER] %s %s @ %.4f  SL=%.4f  TP=%.4f  risk=%.1f%%",
            signal_type, symbol, entry, sl, tp1 or 0, risk,
        )
        return record

    # -----------------------------------------------------------------------
    # Реальный ордер
    # -----------------------------------------------------------------------
    try:
        client = _get_client()

        step, min_qty, min_notional = _lot_filters(client, symbol)
        tick  = _price_tick(client, symbol)

        if signal_type == "BUY":
            balance = _usdt_balance(client)
            max_pos_pct = _max_position_pct()
            max_spend = balance * (max_pos_pct / 100)
            risk_amount = balance * (risk / 100)
            sl_dist = abs(entry - sl)
            if sl_dist == 0:
                logger.warning("[executor] SL distance = 0, пропускаем")
                return None
            qty = _round_qty(risk_amount / sl_dist, step)

            if qty < min_qty:
                logger.warning(
                    "[executor] %s: qty=%.8f < minQty=%.8f (balance=%.2f USDT, risk=%.1f%%), пропускаем",
                    symbol, qty, min_qty, balance, risk,
                )
                return None
            if qty * entry < min_notional:
                logger.warning(
                    "[executor] %s: notional=%.2f < minNotional=%.2f, пропускаем",
                    symbol, qty * entry, min_notional,
                )
                return None
            # Ограничение: не более MAX_POSITION_PCT% баланса на одну позицию
            if qty * entry > max_spend:
                qty = _round_qty(max_spend / entry, step)
                logger.warning(
                    "[executor] %s: позиция ограничена до %.1f%% баланса = %.2f USDT (qty=%.8f)",
                    symbol, max_pos_pct, qty * entry, qty,
                )
                if qty < min_qty or qty * entry < min_notional:
                    logger.warning("[executor] %s: после ограничения qty слишком мал, пропускаем", symbol)
                    return None

            spend_usdt = round(qty * entry, 2)
            logger.info(
                "[REAL] BUY %s: ~%.6f @ market (~%.2f USDT)  TP=%.4f  balance=%.2f USDT",
                symbol, qty, spend_usdt, tp1 or 0, balance,
            )

            # Рыночная покупка на рассчитанную сумму USDT (заполняется мгновенно)
            order = client.order_market_buy(
                symbol=symbol,
                quoteOrderQty=spend_usdt,
            )

            # Реально купленное количество (может отличаться от расчётного из-за цены)
            filled_qty = _round_qty(float(order.get("executedQty") or qty), step)
            fill_price = float(order.get("fills", [{}])[0].get("price") or entry) if order.get("fills") else entry

            result = {
                "ts": datetime.utcnow().isoformat(),
                "mode": "REAL",
                "asset": asset,
                "symbol": symbol,
                "timeframe": timeframe,
                "signal_type": "BUY",
                "entry_order": order,
                "qty": filled_qty,
                "entry_price": fill_price,
                "stop_loss": None,
                "take_profit": tp1,
            }

            # Лимитный ордер на продажу только по TP — стоп-лосс не ставим
            fixed_tp = _tp_pct()
            if fixed_tp > 0:
                # Фиксированный TP: +N% от реальной цены покупки
                tp1 = fill_price * (1 + fixed_tp / 100)
                logger.info("[REAL] %s: TP зафиксирован +%.1f%% от %.6f = %.6f", symbol, fixed_tp, fill_price, tp1)
            if tp1 and filled_qty >= min_qty:
                otp = _round_price(tp1, tick)
                # TP должен быть выше цены покупки
                if otp <= fill_price:
                    logger.warning(
                        "[executor] %s: TP=%.6f <= цена покупки=%.6f — TP-ордер не выставляем",
                        symbol, otp, fill_price,
                    )
                else:
                    try:
                        tp_order = client.order_limit_sell(
                            symbol=symbol,
                            quantity=filled_qty,
                            price=str(otp),
                            timeInForce="GTC",
                        )
                        result["tp_order"] = tp_order
                        logger.info("[REAL] TP-ордер выставлен: %.6f (×%.6f)", otp, filled_qty)
                    except Exception as tp_exc:
                        logger.error("[executor] %s: ошибка TP-ордера: %s", symbol, tp_exc)
            elif not tp1:
                logger.warning("[executor] %s: нет TP в сигнале — ордер на продажу не выставлен", symbol)

            return result

        elif signal_type == "SELL":
            # Для SELL: закрываем позицию (продаём имеющийся актив)
            account = client.get_account()
            qty = 0.0
            for b in account["balances"]:
                if b["asset"] == asset:
                    qty = _round_qty(float(b["free"]), step)
                    break
            if qty <= 0:
                logger.info("[REAL] SELL %s: нет позиции, пропускаем", symbol)
                return None

            order = client.order_market_sell(symbol=symbol, quantity=qty)
            logger.info("[REAL] SELL %s qty=%.6f (маркет)", symbol, qty)
            return {
                "ts": datetime.utcnow().isoformat(),
                "mode": "REAL",
                "asset": asset,
                "symbol": symbol,
                "timeframe": timeframe,
                "signal_type": "SELL",
                "sell_order": order,
                "qty": qty,
            }

    except Exception as exc:
        logger.error("[executor] Ошибка исполнения %s %s: %s", signal_type, symbol, exc)
        return None
