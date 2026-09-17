#!/usr/bin/env python3
"""
Ручная торговля с автоматическими TP/SL (OCO) — одной командой.

Примеры:
  # купить BTC на 50 USDT, TP +3% и SL -5% выставятся сами (OCO)
  python3 trade.py buy BTC --usd 50 --tp 3 --sl 5

  # то же, но с абсолютными ценами
  python3 trade.py buy BTC --usd 50 --tp-price 63500 --sl-price 58400

  # продать весь свободный остаток актива по маркету
  python3 trade.py sell BTC

  # балансы + открытые ордера
  python3 trade.py status

  # последние реальные сделки по активу
  python3 trade.py history BTC --limit 10

Перед реальным ордером скрипт показывает сводку и просит подтверждение (y).
Флаг --yes отключает вопрос. Ключи берутся из .env (BINANCE_API_KEY/SECRET).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.trading.binance_executor import (  # noqa: E402
    _fill_stats,
    _fmt_decimal,
    _floor_to_step,
    _get_client,
    _lot_filters,
    _place_exit_orders,
    _price_tick,
    _round_qty,
    _symbol,
)

MIN_RECOMMENDED_USD = 15.0  # ниже — OCO может не пройти по minNotional


def _confirm(prompt: str, auto_yes: bool) -> bool:
    if auto_yes:
        return True
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in ("y", "yes", "д", "да")


# ---------------------------------------------------------------------------
# buy
# ---------------------------------------------------------------------------

def cmd_buy(args: argparse.Namespace) -> None:
    asset = args.asset.upper()
    symbol = _symbol(asset)
    client = _get_client()

    ticker = client.get_symbol_ticker(symbol=symbol)
    price = float(ticker["price"])

    # TP/SL: либо проценты от цены, либо абсолютные уровни
    tp = args.tp_price if args.tp_price else price * (1 + args.tp / 100)
    sl = args.sl_price if args.sl_price else price * (1 - args.sl / 100)
    if sl >= price:
        print(f"Ошибка: SL {sl:.6f} должен быть НИЖЕ текущей цены {price:.6f}")
        return
    if tp <= price:
        print(f"Ошибка: TP {tp:.6f} должен быть ВЫШЕ текущей цены {price:.6f}")
        return

    usd = args.usd
    if usd < MIN_RECOMMENDED_USD:
        print(f"⚠️  Сумма {usd:.2f} USDT мала — OCO может не пройти по minNotional (рекомендую >= {MIN_RECOMMENDED_USD:.0f})")

    risk_usd = usd * (price - sl) / price
    reward_usd = usd * (tp - price) / price
    rr = reward_usd / risk_usd if risk_usd > 0 else 0

    print(f"""
ПОКУПКА {symbol}
  Сумма:     {usd:.2f} USDT (~{usd / price:.6f} {asset})
  Цена:      ~{price:,.6g}
  TP:        {tp:,.6g}  (+{100 * (tp - price) / price:.2f}%)  → +{reward_usd:.2f}$
  SL:        {sl:,.6g}  (-{100 * (price - sl) / price:.2f}%)  → -{risk_usd:.2f}$
  R/R:       {rr:.2f}
  Ордера:    маркет-покупка + OCO (TP-лимит + стоп-лосс)""")

    if not _confirm("Отправить РЕАЛЬНЫЙ ордер?", args.yes):
        print("Отменено.")
        return

    step, min_qty, min_notional = _lot_filters(client, symbol)
    tick = _price_tick(client, symbol)

    order = client.order_market_buy(symbol=symbol, quoteOrderQty=round(usd, 2))
    filled_qty, fill_price = _fill_stats(order, step, fallback_price=price)
    print(f"✅ Куплено {filled_qty} {asset} по средней {fill_price:,.6g}")

    if filled_qty < min_qty or filled_qty * fill_price < min_notional:
        print("⚠️  Куплено меньше minNotional/minQty — выходные ордера не выставить, закрой вручную!")
        return

    exit_result = _place_exit_orders(
        client, symbol,
        qty=filled_qty, fill_price=fill_price,
        tp=tp, sl=sl, tick=tick, step=step,
    )
    if exit_result.get("sl_placed"):
        print(f"✅ OCO выставлен: TP {tp:,.6g} / SL {sl:,.6g}")
    elif exit_result.get("tp_order"):
        print(f"⚠️  Выставлен только TP {tp:,.6g} — ПОЗИЦИЯ БЕЗ СТОП-ЛОССА (OCO не прошёл)")
    else:
        print("❌ Выходные ордера не выставлены — закрой позицию вручную!")


# ---------------------------------------------------------------------------
# sell
# ---------------------------------------------------------------------------

def cmd_sell(args: argparse.Namespace) -> None:
    asset = args.asset.upper()
    symbol = _symbol(asset)
    client = _get_client()

    account = client.get_account()
    free = 0.0
    locked = 0.0
    for b in account["balances"]:
        if b["asset"] == asset:
            free, locked = float(b["free"]), float(b["locked"])
            break

    if locked > 0:
        print(f"⚠️  {locked} {asset} заблокировано в открытых ордерах — сначала сними их (или продай только свободное)")
    if free <= 0:
        print(f"Нет свободного {asset} для продажи.")
        return

    step, min_qty, min_notional = _lot_filters(client, symbol)
    qty = _round_qty(free, step)
    price = float(client.get_symbol_ticker(symbol=symbol)["price"])

    if qty < min_qty or qty * price < min_notional:
        print(f"Свободный остаток {qty} {asset} (~{qty * price:.2f} USDT) меньше минимального лота — продать нельзя.")
        return

    print(f"\nПРОДАЖА {symbol}: {qty} {asset} по маркету (~{qty * price:.2f} USDT)")
    if not _confirm("Отправить РЕАЛЬНЫЙ ордер?", args.yes):
        print("Отменено.")
        return

    order = client.order_market_sell(symbol=symbol, quantity=_fmt_decimal(_floor_to_step(qty, step)))
    got = float(order.get("cummulativeQuoteQty") or 0)
    print(f"✅ Продано {order.get('executedQty')} {asset} за {got:.2f} USDT")


# ---------------------------------------------------------------------------
# status / history
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> None:
    client = _get_client()
    account = client.get_account()

    print("\n— Балансы (без Earn/LD, > $1) —")
    for b in account["balances"]:
        asset = b["asset"]
        if asset.startswith("LD"):
            continue
        free, locked = float(b["free"]), float(b["locked"])
        total = free + locked
        if total <= 0:
            continue
        if asset in ("USDT", "USDC"):
            usd_value = total
        else:
            try:
                usd_value = total * float(client.get_symbol_ticker(symbol=asset + "USDT")["price"])
            except Exception:
                continue
        if usd_value < 1:
            continue
        lock_note = f" (в ордерах: {locked})" if locked > 0 else ""
        print(f"  {asset:6} {total:.8f}  ≈ {usd_value:.2f}${lock_note}")

    print("\n— Открытые ордера —")
    orders = client.get_open_orders()
    if not orders:
        print("  нет")
    for o in orders:
        print(f"  {o['symbol']:10} {o['side']:4} {o['type']:16} qty={o['origQty']} price={o['price']} stop={o.get('stopPrice', '-')}")


def cmd_history(args: argparse.Namespace) -> None:
    asset = args.asset.upper()
    symbol = _symbol(asset)
    client = _get_client()
    trades = client.get_my_trades(symbol=symbol, limit=args.limit)
    if not trades:
        print("Сделок нет.")
        return
    from datetime import datetime
    print(f"\n— Последние {len(trades)} сделок {symbol} —")
    for t in trades:
        side = "КУПИЛ " if t["isBuyer"] else "ПРОДАЛ"
        ts = datetime.utcfromtimestamp(t["time"] / 1000).strftime("%Y-%m-%d %H:%M")
        total = float(t["price"]) * float(t["qty"])
        print(f"  {ts}  {side} {t['qty']} по {float(t['price']):,.6g} = {total:.2f}$  (комиссия {t['commission']} {t['commissionAsset']})")


# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Ручная торговля с авто-TP/SL (OCO)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("buy", help="маркет-покупка + OCO (TP+SL)")
    b.add_argument("asset", help="BTC, ETH, SOL...")
    b.add_argument("--usd", type=float, required=True, help="сумма покупки в USDT")
    b.add_argument("--tp", type=float, default=3.0, help="TP в %% от цены (default 3)")
    b.add_argument("--sl", type=float, default=5.0, help="SL в %% от цены (default 5)")
    b.add_argument("--tp-price", type=float, default=None, help="TP абсолютной ценой (перекрывает --tp)")
    b.add_argument("--sl-price", type=float, default=None, help="SL абсолютной ценой (перекрывает --sl)")
    b.add_argument("--yes", action="store_true", help="не спрашивать подтверждение")
    b.set_defaults(func=cmd_buy)

    s = sub.add_parser("sell", help="продать весь свободный остаток по маркету")
    s.add_argument("asset")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_sell)

    st = sub.add_parser("status", help="балансы и открытые ордера")
    st.set_defaults(func=cmd_status)

    h = sub.add_parser("history", help="последние сделки по активу")
    h.add_argument("asset")
    h.add_argument("--limit", type=int, default=10)
    h.set_defaults(func=cmd_history)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
