"""
Мои сделки на Binance Spot: по чём куплено, средняя цена, PnL.

Только чтение — никаких ордеров не выставляет.

Запуск:
  python3 my_trades.py                  # SOLUSDT по умолчанию
  python3 my_trades.py --symbol ETHUSDT
  python3 my_trades.py --limit 50       # сколько последних сделок показать
"""

import os
import argparse
from datetime import datetime, timezone

from binance.client import Client
from dotenv import load_dotenv


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def main() -> None:
    parser = argparse.ArgumentParser(description="Сделки и средняя цена входа (Binance Spot)")
    parser.add_argument("--symbol", default="SOLUSDT", help="Пара, default=SOLUSDT")
    parser.add_argument("--limit", type=int, default=20, help="Сколько последних сделок вывести")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")
    if not api_key or not api_secret:
        print("Нет BINANCE_API_KEY / BINANCE_API_SECRET в .env")
        return

    client = Client(api_key, api_secret)
    symbol = args.symbol.upper()
    base = symbol.replace("USDT", "")

    trades = client.get_my_trades(symbol=symbol, limit=1000)
    if not trades:
        print(f"Сделок по {symbol} не найдено.")
        return
    trades.sort(key=lambda t: t["time"])

    # ── Таблица последних сделок ──────────────────────────────
    print(f"\n══ Сделки {symbol} (последние {min(args.limit, len(trades))} из {len(trades)}) ══")
    print(f"{'Дата (UTC)':<17} {'Сторона':<8} {'Цена':>12} {'Кол-во':>12} {'Сумма USDT':>12} {'Комиссия':>16}")
    print("─" * 82)
    for t in trades[-args.limit:]:
        side = "BUY" if t["isBuyer"] else "SELL"
        fee = f"{float(t['commission']):.6f} {t['commissionAsset']}"
        print(
            f"{fmt_ts(t['time']):<17} {side:<8} {float(t['price']):>12,.4f} "
            f"{float(t['qty']):>12,.4f} {float(t['quoteQty']):>12,.2f} {fee:>16}"
        )

    # ── Средняя цена текущей позиции (метод средней стоимости) ──
    pos_qty = 0.0
    pos_cost = 0.0
    fees_usdt = 0.0
    fees_base = 0.0
    for t in trades:
        qty = float(t["qty"])
        quote = float(t["quoteQty"])
        if t["isBuyer"]:
            pos_qty += qty
            pos_cost += quote
        else:
            if pos_qty > 1e-12:
                avg = pos_cost / pos_qty
                pos_cost -= min(qty, pos_qty) * avg
            pos_qty -= qty
        if t["commissionAsset"] == "USDT":
            fees_usdt += float(t["commission"])
        elif t["commissionAsset"] == base:
            fees_base += float(t["commission"])

    pos_qty -= fees_base  # комиссия, списанная в монете, уменьшает позицию

    price_now = float(client.get_symbol_ticker(symbol=symbol)["price"])

    print(f"\n══ Позиция {base} ══")
    balance = client.get_asset_balance(asset=base)
    free, locked = float(balance["free"]), float(balance["locked"])
    print(f"На кошельке:      {free + locked:,.4f} {base} (свободно {free:,.4f}, в ордерах {locked:,.4f})")

    if pos_qty > 1e-8 and pos_cost > 0:
        avg_price = pos_cost / (pos_qty + fees_base)
        pnl = (price_now - avg_price) * pos_qty
        pnl_pct = (price_now / avg_price - 1) * 100
        print(f"Средняя цена входа: ${avg_price:,.4f}")
        print(f"Текущая цена:       ${price_now:,.4f}")
        print(f"PnL:                {pnl:+,.2f} USDT ({pnl_pct:+.2f}%)")
    else:
        print(f"Открытой позиции по сделкам не видно. Текущая цена: ${price_now:,.4f}")
    if fees_usdt or fees_base:
        print(f"Комиссии всего:     {fees_usdt:.4f} USDT + {fees_base:.6f} {base}")

    # ── Открытые ордера ───────────────────────────────────────
    open_orders = client.get_open_orders(symbol=symbol)
    if open_orders:
        print(f"\n══ Открытые ордера {symbol} ══")
        for o in open_orders:
            price = float(o["price"])
            qty = float(o["origQty"])
            dist = (price / price_now - 1) * 100 if price > 0 else 0.0
            stop = f" (стоп-триггер ${float(o['stopPrice']):,.2f})" if float(o.get("stopPrice", 0)) else ""
            print(f"{o['side']:<5} {o['type']:<18} {qty:,.4f} по ${price:,.2f} ({dist:+.1f}% от цены){stop}")
    else:
        print(f"\nОткрытых ордеров по {symbol} нет.")


if __name__ == "__main__":
    main()
