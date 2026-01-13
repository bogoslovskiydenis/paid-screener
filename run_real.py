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
    from src.analytics.patterns.chart_patterns import ChartPatternDetector
    from src.analytics.indicators.rsi import RSICalculator
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
        chart_pattern_detector = ChartPatternDetector(
            min_pattern_length=config.get("analysis", {}).get("head_shoulders", {}).get("min_pattern_length", 20),
            price_tolerance=config.get("analysis", {}).get("support_resistance", {}).get("price_tolerance", 0.005)
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
                        
                        # Проверка пробоев уровней
                        breakout = levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
                        if breakout.get("breakout"):
                            print(f"  ⚠ ПРОБОЙ УРОВНЯ: {breakout['level_type'].upper()} на ${breakout['price']:.2f}")
                            print(f"    Направление: {breakout.get('breakout_direction', 'N/A')}")
                            print(f"    Подтверждение объемом: {'✓' if breakout.get('volume_confirmation') else '✗'}")
                            
                            last_candle = df.iloc[-1]
                            timestamp = last_candle.get("timestamp") if "timestamp" in df.columns else pd.Timestamp.now()
                            if isinstance(timestamp, pd.Timestamp):
                                timestamp = timestamp.to_pydatetime()
                            
                            breakout_data = {
                                "asset": asset,
                                "timeframe": timeframe,
                                "level_type": breakout["level_type"],
                                "level_price": breakout["price"],
                                "level_strength": breakout["strength"],
                                "breakout_price": float(last_candle["close"]),
                                "volume_confirmation": breakout.get("volume_confirmation", False),
                                "timestamp": timestamp
                            }
                            database.save_breakout(breakout_data)
                    
                    # RSI анализ
                    rsi_calculator = RSICalculator(period=14)
                    rsi_analysis = rsi_calculator.analyze(df)
                    rsi_value = rsi_analysis.get("rsi")
                    rsi_zone = rsi_analysis.get("rsi_zone", "NEUTRAL")
                    rsi_signal = rsi_analysis.get("rsi_signal", "NEUTRAL")
                    
                    if rsi_value:
                        signal_emoji = "🟢" if rsi_signal == "BUY" else "🔴" if rsi_signal == "SELL" else "🟡"
                        print(f"  {signal_emoji} RSI: {rsi_value:.1f} ({rsi_zone})")
                        if rsi_signal != "NEUTRAL":
                            print(f"    Рекомендация: {'ПОКУПАТЬ' if rsi_signal == 'BUY' else 'ПРОДАВАТЬ'} (сила: {rsi_analysis.get('rsi_strength', 0):.1%})")
                    
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
                    
                    # Дополнительные графические паттерны
                    chart_patterns = chart_pattern_detector.detect_all(df)
                    if chart_patterns:
                        for cp in chart_patterns:
                            print(f"  Графический паттерн: {cp['pattern_type']} ({cp['pattern_direction']})")
                            pattern_data = {
                                "asset": asset,
                                "timeframe": timeframe,
                                "pattern_type": cp.get("pattern_type"),
                                "pattern_direction": cp.get("pattern_direction"),
                                "neckline": cp.get("neckline"),
                                "head_price": cp.get("head_price") or cp.get("peak1_price") or cp.get("apex_price"),
                                "target_price": cp.get("target_price"),
                                "completion_percentage": cp.get("completion_percentage", 0.0),
                                "volume_confirmation": cp.get("volume_confirmation", False),
                                "pattern_metadata": {k: v for k, v in cp.items() if k not in ["pattern_type", "pattern_direction", "neckline", "head_price", "target_price", "completion_percentage", "volume_confirmation"]}
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
                    
                    result_data = {
                        "current_price": float(df.iloc[-1]['close']),
                        "candles_count": len(df),
                        "rsi": rsi_analysis,
                        "candlestick_pattern": pattern,
                        "levels": levels,
                        "head_shoulders_pattern": hs_pattern,
                        "chart_patterns": chart_patterns if chart_patterns else None,
                        "signal": signal
                    }
                    
                    # Добавляем информацию о пробое, если есть
                    if levels:
                        breakout = levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
                        if breakout.get("breakout"):
                            result_data["breakout"] = breakout
                    
                    results[asset][timeframe] = result_data
                    
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

