#!/usr/bin/env python3
"""Запуск проекта с реальными данными с Binance."""
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "src"))

print("=" * 70)
print("Paid Screener - Анализ с реальными данными Binance")
print("=" * 70)
print()

# Проверка зависимостей
try:
    import ccxt
    import yaml
    import pandas as pd
    import numpy as np
    from sqlalchemy import create_engine
    print("✓ Все зависимости установлены")
except ImportError as e:
    print(f"✗ Отсутствует зависимость: {e}")
    print("\nУстановите зависимости:")
    print("  pip install -r requirements.txt")
    sys.exit(1)

# Импорт модулей проекта
try:
    from src.utils.config import load_config, get_assets, get_timeframes, Settings
    from src.utils.logger import setup_logger
    from src.parsers.exchange_manager import ExchangeManager
    from src.storage.database import Database
    from src.analytics.signals.generator import SignalGenerator
    from src.analytics.levels.support_resistance import SupportResistanceAnalyzer
    from src.analytics.patterns.head_shoulders import HeadShouldersPattern
    print("✓ Модули проекта загружены")
except ImportError as e:
    print(f"✗ Ошибка импорта модулей: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

logger = setup_logger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Paid Screener - реальные данные Binance")
    parser.add_argument("--asset", type=str, default="ETH", help="Актив (ETH, SOL)")
    parser.add_argument("--timeframes", type=str, default="4h", help="Таймфреймы через запятую (15m,4h,1d,1M)")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="Минимальная уверенность сигнала")
    parser.add_argument("--export-json", action="store_true", help="Экспорт в JSON")
    parser.add_argument("--output", type=str, default="signals.json", help="Файл вывода")
    parser.add_argument("--limit", type=int, default=500, help="Количество свечей для загрузки")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Путь к конфигурации")
    
    args = parser.parse_args()
    
    try:
        # Загрузка конфигурации
        config = load_config(args.config)
        settings = Settings()
        
        assets = [args.asset] if args.asset else get_assets(config)
        timeframes = args.timeframes.split(",") if args.timeframes else get_timeframes(config)
        
        print(f"Активы: {', '.join(assets)}")
        print(f"Таймфреймы: {', '.join(timeframes)}")
        print(f"Количество свечей: {args.limit}")
        print("=" * 70)
        print()
        
        # Инициализация компонентов
        enabled_exchanges = [
            name for name, exchange_config in config.get("exchanges", {}).items()
            if exchange_config.get("enabled", False)
        ]
        
        exchange_manager = ExchangeManager(enabled_exchanges)
        database = Database(settings.database_url)
        
        signal_generator = SignalGenerator(min_confidence=args.min_confidence)
        levels_analyzer = SupportResistanceAnalyzer(
            min_touches=config.get("analysis", {}).get("support_resistance", {}).get("min_touches", 2),
            price_tolerance=config.get("analysis", {}).get("support_resistance", {}).get("price_tolerance", 0.005)
        )
        pattern_analyzer = HeadShouldersPattern(
            min_pattern_length=config.get("analysis", {}).get("head_shoulders", {}).get("min_pattern_length", 20),
            symmetry_tolerance=config.get("analysis", {}).get("head_shoulders", {}).get("symmetry_tolerance", 0.1)
        )
        
        results = {}
        
        # Обработка каждого актива и таймфрейма
        for asset in assets:
            results[asset] = {}
            
            for timeframe in timeframes:
                print(f"Обработка {asset}/{timeframe}...")
                print("-" * 70)
                
                try:
                    # Загрузка данных с Binance
                    print(f"Загрузка данных с Binance...")
                    df = exchange_manager.get_ohlcv(asset, timeframe, limit=args.limit)
                    
                    if df.empty:
                        print(f"⚠ Нет данных для {asset}/{timeframe}")
                        results[asset][timeframe] = {"error": "No data"}
                        continue
                    
                    print(f"✓ Загружено свечей: {len(df)}")
                    print(f"  Диапазон: {df['timestamp'].min()} - {df['timestamp'].max()}")
                    print(f"  Текущая цена: ${df.iloc[-1]['close']:.2f}")
                    print(f"  Min: ${df['low'].min():.2f}, Max: ${df['high'].max():.2f}")
                    
                    # Сохранение в БД
                    database.save_candles(asset, timeframe, df)
                    print("✓ Данные сохранены в БД")
                    
                    # Анализ
                    print("\nВыполнение анализа...")
                    
                    # Уровни поддержки/сопротивления
                    levels = levels_analyzer.find_levels(df)
                    support_count = len(levels.get("support_levels", []))
                    resistance_count = len(levels.get("resistance_levels", []))
                    print(f"  Уровни: поддержка {support_count}, сопротивление {resistance_count}")
                    
                    if levels:
                        database.save_levels(asset, timeframe, levels)
                    
                    # Свечные паттерны
                    from src.analytics.candlestick.patterns import CandlestickPatternAnalyzer
                    candlestick_analyzer = CandlestickPatternAnalyzer()
                    pattern = candlestick_analyzer.analyze(df)
                    if pattern:
                        print(f"  Свечной паттерн: {pattern}")
                    
                    # Паттерн Голова и плечи
                    hs_pattern = pattern_analyzer.detect(df)
                    if hs_pattern:
                        print(f"  Паттерн Голова и плечи: {hs_pattern['pattern_type']} ({hs_pattern['pattern_direction']})")
                        pattern_data = {
                            "asset": asset,
                            "timeframe": timeframe,
                            **hs_pattern
                        }
                        database.save_pattern(pattern_data)
                    
                    # Генерация сигнала
                    signal = signal_generator.generate_signal(asset, timeframe, df)
                    if signal:
                        print(f"\n  ✓ СИГНАЛ: {signal['signal_type']} ({signal['strength']})")
                        print(f"    Уверенность: {signal['confidence']:.1%}")
                        print(f"    Вход: ${signal['entry_price']:.2f}")
                        print(f"    Стоп-лосс: ${signal['stop_loss']:.2f}")
                        print(f"    Тейк-профиты: {len(signal.get('take_profit', []))}")
                        database.save_signal(signal)
                    else:
                        print("  Сигнал: не сгенерирован (низкая уверенность)")
                    
                    results[asset][timeframe] = {
                        "current_price": float(df.iloc[-1]['close']),
                        "candles_count": len(df),
                        "candlestick_pattern": pattern,
                        "levels": levels,
                        "head_shoulders_pattern": hs_pattern,
                        "signal": signal
                    }
                    
                except Exception as e:
                    logger.error(f"Ошибка при обработке {asset}/{timeframe}: {e}")
                    print(f"✗ Ошибка: {e}")
                    results[asset][timeframe] = {"error": str(e)}
                
                print()
        
        # Экспорт результатов
        if args.export_json:
            output_file = Path(args.output)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, default=str, ensure_ascii=False)
            print(f"✓ Результаты сохранены в {args.output}")
        else:
            print("Результаты:")
            print(json.dumps(results, indent=2, default=str, ensure_ascii=False))
        
        print()
        print("=" * 70)
        print("Анализ завершен!")
        print("=" * 70)
        
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

