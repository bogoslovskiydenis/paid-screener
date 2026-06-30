#!/usr/bin/env python3
"""
Скан уровней ликвидации лонгов для фьючерсных пар.

Вариант A — реальные принудительные ликвидации с Binance (последние 48ч).
Вариант B — прогнозная тепловая карта: где откроется каскад если цена падает.

Запуск:
  python3 liquidation_scan.py                     # ETH по умолчанию
  python3 liquidation_scan.py --symbols ETH,BTC,BNB
  python3 liquidation_scan.py --symbols ETH --hours 72
"""

import sys
import argparse
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    import ccxt
    import pandas as pd
    from urllib3.exceptions import NotOpenSSLWarning  # type: ignore
    import warnings
    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except ImportError as e:
    print(f"✗ Зависимость не найдена: {e}")
    print("  Установите: pip install -r requirements.txt")
    sys.exit(1)

from src.analytics.liquidation_analyzer import (
    LiquidationAnalyzer,
    format_liq_console,
    BUCKET_SIZES,
)
from src.utils.logger import setup_logger

logger = setup_logger("liquidation_scan", level="INFO")

SYMBOL_MAP = {
    "ETH":  "ETHUSDT",
    "BTC":  "BTCUSDT",
    "SOL":  "SOLUSDT",
    "BNB":  "BNBUSDT",
    "SUI":  "SUIUSDT",
    "XLM":  "XLMUSDT",
}


def fetch_ohlcv(
    exchange: ccxt.Exchange, usdt_symbol: str, tf: str, limit: int
) -> pd.DataFrame:
    """Загружает OHLCV для заданного таймфрейма."""
    base = usdt_symbol.replace("USDT", "")
    pair = f"{base}/USDT"
    try:
        raw = exchange.fetch_ohlcv(pair, tf, limit=limit)
        return pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
    except Exception as e:
        logger.warning(f"fetch_ohlcv {pair} {tf}: {e}")
        return pd.DataFrame()


def get_current_price(exchange: ccxt.Exchange, usdt_symbol: str) -> float:
    base = usdt_symbol.replace("USDT", "")
    pair = f"{base}/USDT"
    try:
        ticker = exchange.fetch_ticker(pair)
        return float(ticker["last"])
    except Exception as e:
        logger.error(f"Не удалось получить цену {pair}: {e}")
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Liquidation Level Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbols", type=str, default="ETH",
        help="Активы через запятую: ETH,BTC,SOL,BNB (по умолчанию: ETH)"
    )
    parser.add_argument(
        "--hours", type=int, default=48,
        help="Глубина истории ликвидаций в часах (по умолчанию: 48)"
    )
    parser.add_argument(
        "--top", type=int, default=8,
        help="Сколько уровней показывать (по умолчанию: 8)"
    )
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    unknown = [s for s in symbols if s not in SYMBOL_MAP]
    if unknown:
        print(f"Неизвестные символы: {unknown}. Доступны: {list(SYMBOL_MAP)}")
        sys.exit(1)

    print("✓ Инициализация Binance Futures...")
    exchange = ccxt.binance({
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })
    analyzer = LiquidationAnalyzer()

    for idx, sym in enumerate(symbols):
        usdt_sym = SYMBOL_MAP[sym]

        price = get_current_price(exchange, usdt_sym)
        if price <= 0:
            print(f"[{sym}] Пропускаем — не удалось получить цену")
            continue

        print(f"\n[{sym}] Цена: ${price:,.4f} | Анализ ликвидаций...")

        ohlcv_15m = fetch_ohlcv(exchange, usdt_sym, "15m", limit=200)
        ohlcv_1h  = fetch_ohlcv(exchange, usdt_sym, "1h",  limit=168)

        result = analyzer.analyze(
            symbol=usdt_sym,
            current_price=price,
            ohlcv_15m=ohlcv_15m,
            ohlcv_1h=ohlcv_1h,
            hours=args.hours,
        )

        print(format_liq_console(result, top_n=args.top))

        if idx < len(symbols) - 1:
            time.sleep(1.5)

    print("\n✓ Готово.")


if __name__ == "__main__":
    main()
