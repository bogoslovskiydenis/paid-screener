#!/usr/bin/env python3
"""Тест подключения к Binance и получения данных."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dotenv import load_dotenv
load_dotenv()

print("Тестирование подключения к Binance...")
print("=" * 60)

# --- 1. Публичные данные через ccxt ---
try:
    import ccxt
    print(f"✓ ccxt установлен: {ccxt.__version__}")
except ImportError:
    print("✗ ccxt не установлен. Установите: pip install ccxt")
    sys.exit(1)

try:
    exchange = ccxt.binance({"enableRateLimit": True})
    ticker = exchange.fetch_ticker("ETH/USDT")
    print(f"✓ ETH/USDT цена: ${ticker['last']:.2f}  (объём 24ч: ${ticker['quoteVolume']:,.0f})")
except Exception as e:
    print(f"✗ Ошибка публичного API: {e}")

# --- 2. Приватный API (ключи из .env) ---
print("\nПроверка API ключей...")
api_key    = os.getenv("BINANCE_API_KEY", "")
api_secret = os.getenv("BINANCE_API_SECRET", "")

if not api_key or not api_secret:
    print("✗ BINANCE_API_KEY / BINANCE_API_SECRET не заданы в .env")
    sys.exit(1)

try:
    from binance.client import Client
    client = Client(api_key, api_secret)

    status = client.get_system_status()
    print(f"✓ Статус Binance: {'работает' if status['status'] == 0 else status}")

    account = client.get_account()
    print(f"✓ Аккаунт доступен (taker fee: {float(account['takerCommission'])/100:.2f}%)")

    balances = [b for b in account["balances"] if float(b["free"]) > 0 or float(b["locked"]) > 0]
    if balances:
        print(f"\n💰 Балансы ({len(balances)} активов):")
        for b in balances:
            print(f"   {b['asset']:6s}  free={b['free']}  locked={b['locked']}")
    else:
        print("   (баланс пустой)")

    print("\n✓ API ключи работают корректно!")

except Exception as e:
    print(f"✗ Ошибка авторизации: {e}")
    sys.exit(1)

print("=" * 60)

