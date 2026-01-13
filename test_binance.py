#!/usr/bin/env python3
"""Тест подключения к Binance и получения данных."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

print("Тестирование подключения к Binance...")
print("=" * 60)

try:
    import ccxt
    print(f"✓ ccxt установлен: {ccxt.__version__}")
except ImportError:
    print("✗ ccxt не установлен. Установите: pip install ccxt")
    sys.exit(1)

try:
    exchange = ccxt.binance({
        "enableRateLimit": True,
        "rateLimit": 1200,
    })
    print("✓ Подключение к Binance создано")
    
    # Тест получения тикера
    print("\nПолучение данных ETH/USDT...")
    ticker = exchange.fetch_ticker("ETH/USDT")
    print(f"✓ Текущая цена ETH: ${ticker['last']:.2f}")
    print(f"  Объем 24ч: ${ticker['quoteVolume']:,.0f}")
    
    # Тест получения свечей
    print("\nПолучение свечей ETH/USDT (4h, последние 10)...")
    ohlcv = exchange.fetch_ohlcv("ETH/USDT", "4h", limit=10)
    print(f"✓ Получено свечей: {len(ohlcv)}")
    
    if ohlcv:
        last_candle = ohlcv[-1]
        print(f"  Последняя свеча:")
        print(f"    Время: {exchange.iso8601(last_candle[0])}")
        print(f"    Open: ${last_candle[1]:.2f}")
        print(f"    High: ${last_candle[2]:.2f}")
        print(f"    Low: ${last_candle[3]:.2f}")
        print(f"    Close: ${last_candle[4]:.2f}")
        print(f"    Volume: {last_candle[5]:.2f}")
    
    # Тест получения большего количества свечей
    print("\nПолучение свечей ETH/USDT (4h, последние 100)...")
    ohlcv = exchange.fetch_ohlcv("ETH/USDT", "4h", limit=100)
    print(f"✓ Получено свечей: {len(ohlcv)}")
    
    print("\n" + "=" * 60)
    print("✓ Все тесты пройдены! Binance API работает корректно")
    print("=" * 60)
    
except Exception as e:
    print(f"\n✗ Ошибка: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

