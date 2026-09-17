"""
Исполнитель ордеров на Binance Spot.

Режимы:
  BINANCE_PAPER_TRADING=true  — логирует сделки, не отправляет ордера (по умолчанию)
  BINANCE_PAPER_TRADING=false — реальные ордера

Реальный BUY:
  1. Маркет-покупка на рассчитанную сумму USDT (риск-сайзинг от дистанции до SL).
  2. OCO-ордер на продажу: лимитный TP + стоп-лосс одним ордером.
     Если OCO не прошёл — fallback на обычный лимитный TP (позиция без SL логируется как WARNING).

Переменные окружения (в .env):
  BINANCE_API_KEY
  BINANCE_API_SECRET
  BINANCE_PAPER_TRADING   (true/false, default=true)
  BINANCE_RISK_PCT        (риск на сделку в % от баланса USDT, default=1.0)
  BINANCE_MAX_POSITION_PCT (макс. размер позиции в % от баланса, default=20.0)
  BINANCE_TP_PCT          (фиксированный TP в %, 0 = TP из сигнала/дефолтов по ТФ)
"""

import math
import os
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
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

def _trade_timeframes() -> Optional[set]:
    """
    Таймфреймы, на которых разрешена автоторговля (BINANCE_TRADE_TIMEFRAMES=3d,1w).
    Пусто/не задано = все. Бэктест 4 лет: BUY на 3d = PF 1.43, на 4h/1d = ~0.87.
    """
    raw = os.getenv("BINANCE_TRADE_TIMEFRAMES", "").strip()
    if not raw:
        return None
    return {tf.strip() for tf in raw.split(",") if tf.strip()}


def _tp_pct(timeframe: str = "") -> float:
    """TP в % от цены покупки по таймфрейму. 0 = брать TP из сигнала."""
    global_pct = float(os.getenv("BINANCE_TP_PCT", "0"))
    if global_pct > 0:
        return global_pct
    defaults = {"1h": 2.0, "4h": 4.0, "1d": 6.0, "3d": 6.0, "1w": 8.0, "1M": 10.0}
    return defaults.get(timeframe, 0.0)

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

def _floor_to_step(value: float, step: str) -> Decimal:
    """Округляет value вниз до кратного step (Decimal — без float-артефактов)."""
    d_step = Decimal(step)
    if d_step <= 0:
        return Decimal(str(value))
    return (Decimal(str(value)) / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step

def _round_qty(qty: float, step_size: str) -> float:
    """Округляет qty вниз (floor) по stepSize лота — Binance требует строгого кратного."""
    return float(_floor_to_step(qty, step_size))

def _fmt_decimal(d: Decimal) -> str:
    """Decimal → строка без экспоненты и хвостовых нулей ('0.05000' → '0.05')."""
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s

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

def _fill_stats(order: Dict[str, Any], step: str, fallback_price: float) -> tuple[float, float]:
    """
    Возвращает (qty_после_комиссии, средневзвешенная_цена) из ответа маркет-ордера.

    Комиссия в купленном активе (когда fee не в BNB) вычитается из qty —
    иначе последующий SELL на полный executedQty упадёт с insufficient balance.
    """
    executed_qty = float(order.get("executedQty") or 0)
    cum_quote = float(order.get("cummulativeQuoteQty") or 0)
    avg_price = cum_quote / executed_qty if executed_qty > 0 and cum_quote > 0 else fallback_price

    base_asset = order.get("symbol", "").replace("USDT", "")
    fee_in_base = 0.0
    for fill in order.get("fills", []):
        if fill.get("commissionAsset") == base_asset:
            fee_in_base += float(fill.get("commission") or 0)

    net_qty = _round_qty(executed_qty - fee_in_base, step)
    return net_qty, avg_price

def _has_open_sell_order(client, symbol: str) -> bool:
    """Есть ли уже открытый SELL-ордер по символу (значит позиция уже ведётся)."""
    try:
        for o in client.get_open_orders(symbol=symbol):
            if o.get("side") == "SELL":
                return True
    except Exception as exc:
        logger.warning("[executor] %s: не удалось проверить открытые ордера: %s", symbol, exc)
    return False


# ---------------------------------------------------------------------------
# Выходные ордера (OCO: TP + SL)
# ---------------------------------------------------------------------------

def _place_exit_orders(
    client,
    symbol: str,
    qty: float,
    fill_price: float,
    tp: float,
    sl: float,
    tick: str,
    step: str,
) -> Dict[str, Any]:
    """
    Выставляет OCO-ордер на продажу: лимитный TP + стоп-лосс.
    Fallback: обычный лимитный TP без SL (с громким WARNING).

    Возвращает dict {"tp_order": ..., "sl_placed": bool}.
    """
    result: Dict[str, Any] = {"sl_placed": False}

    tp_price = _floor_to_step(tp, tick)
    stop_price = _floor_to_step(sl, tick)
    # стоп-лимит чуть ниже стоп-триггера, чтобы лимитка успела исполниться
    stop_limit = _floor_to_step(sl * 0.997, tick)
    qty_str = _fmt_decimal(_floor_to_step(qty, step))

    # OCO для SELL требует: stopPrice < текущая цена < price
    if float(tp_price) <= fill_price:
        logger.warning(
            "[executor] %s: TP=%s <= цена покупки=%.6f — выходные ордера не выставлены",
            symbol, _fmt_decimal(tp_price), fill_price,
        )
        return result
    if float(stop_price) >= fill_price:
        logger.warning(
            "[executor] %s: SL=%s >= цена покупки=%.6f — SL некорректен, ставим только TP",
            symbol, _fmt_decimal(stop_price), fill_price,
        )
        stop_price = None

    if stop_price is not None:
        try:
            oco = client.create_oco_order(
                symbol=symbol,
                side="SELL",
                quantity=qty_str,
                price=_fmt_decimal(tp_price),               # лимитный TP
                stopPrice=_fmt_decimal(stop_price),         # триггер SL
                stopLimitPrice=_fmt_decimal(stop_limit),    # лимитка после триггера
                stopLimitTimeInForce="GTC",
            )
            result["tp_order"] = oco
            result["sl_placed"] = True
            logger.info(
                "[REAL] %s: OCO выставлен — TP=%s, SL=%s (×%s)",
                symbol, _fmt_decimal(tp_price), _fmt_decimal(stop_price), qty_str,
            )
            return result
        except Exception as oco_exc:
            logger.error("[executor] %s: OCO не прошёл (%s), fallback на лимитный TP", symbol, oco_exc)

    # Fallback: только лимитный TP, позиция без стоп-лосса
    try:
        tp_order = client.order_limit_sell(
            symbol=symbol,
            quantity=qty_str,
            price=_fmt_decimal(tp_price),
            timeInForce="GTC",
        )
        result["tp_order"] = tp_order
        logger.warning(
            "[REAL] %s: выставлен только TP=%s — ПОЗИЦИЯ БЕЗ СТОП-ЛОССА",
            symbol, _fmt_decimal(tp_price),
        )
    except Exception as tp_exc:
        logger.error(
            "[executor] %s: ошибка TP-ордера: %s — ПОЗИЦИЯ БЕЗ ВЫХОДНЫХ ОРДЕРОВ, закрой вручную!",
            symbol, tp_exc,
        )
    return result


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
    if not isinstance(trades, list):
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
    Исполняет сигнал BUY для спот-актива (SELL — только уведомление).

    signal — dict с полями: signal_type, entry_price, stop_loss, take_profit
    Возвращает dict с результатом или None при ошибке/пропуске.
    """
    signal_type: str = signal.get("signal_type", "")
    if signal_type != "BUY":
        if signal_type == "SELL":
            logger.info("[executor] SELL %s — только уведомление, автопродажа отключена", asset)
        return None

    if "/" in asset:
        logger.info("[executor] Кросс-пара %s пропущена (только USDT)", asset)
        return None

    allowed_tfs = _trade_timeframes()
    if allowed_tfs is not None and timeframe not in allowed_tfs:
        logger.info(
            "[executor] %s/%s: таймфрейм не в BINANCE_TRADE_TIMEFRAMES (%s) — только уведомление",
            asset, timeframe, ",".join(sorted(allowed_tfs)),
        )
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
            "ts": datetime.now(timezone.utc).isoformat(),
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
    # Реальный BUY
    # -----------------------------------------------------------------------
    try:
        client = _get_client()

        # Защита от дублей на уровне биржи: открытый SELL-ордер = позиция уже ведётся
        if _has_open_sell_order(client, symbol):
            logger.info("[executor] %s: уже есть открытый SELL-ордер — пропускаем BUY", symbol)
            return None

        step, min_qty, min_notional = _lot_filters(client, symbol)
        tick = _price_tick(client, symbol)

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
            "[REAL] BUY %s: ~%.6f @ market (~%.2f USDT)  SL=%.4f  TP=%.4f  balance=%.2f USDT",
            symbol, qty, spend_usdt, sl, tp1 or 0, balance,
        )

        # Рыночная покупка на рассчитанную сумму USDT (заполняется мгновенно)
        order = client.order_market_buy(
            symbol=symbol,
            quoteOrderQty=spend_usdt,
        )

        # Реально купленное количество (за вычетом комиссии в активе) и средняя цена
        filled_qty, fill_price = _fill_stats(order, step, fallback_price=entry)
        if filled_qty < min_qty:
            logger.error(
                "[executor] %s: после комиссии qty=%.8f < minQty — выходные ордера не выставить, закрой вручную!",
                symbol, filled_qty,
            )

        result = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": "REAL",
            "asset": asset,
            "symbol": symbol,
            "timeframe": timeframe,
            "signal_type": "BUY",
            "entry_order": order,
            "qty": filled_qty,
            "entry_price": fill_price,
            "stop_loss": sl,
            "take_profit": tp1,
        }

        # Фиксированный TP: +N% от реальной цены покупки (если задан)
        fixed_tp = _tp_pct(timeframe)
        if fixed_tp > 0:
            tp1 = fill_price * (1 + fixed_tp / 100)
            logger.info("[REAL] %s: TP зафиксирован +%.1f%% от %.6f = %.6f", symbol, fixed_tp, fill_price, tp1)
        result["take_profit"] = tp1

        if tp1 and filled_qty >= min_qty:
            exit_result = _place_exit_orders(
                client, symbol,
                qty=filled_qty, fill_price=fill_price,
                tp=tp1, sl=sl, tick=tick, step=step,
            )
            result.update(exit_result)
        elif not tp1:
            logger.warning("[executor] %s: нет TP в сигнале — выходные ордера не выставлены", symbol)

        return result

    except Exception as exc:
        logger.error("[executor] Ошибка исполнения %s %s: %s", signal_type, symbol, exc)
        return None
