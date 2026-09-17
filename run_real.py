#!/usr/bin/env python3
"""Запуск проекта с реальными данными с Binance."""
import sys
import argparse
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    import ccxt        # noqa: F401
    import yaml        # noqa: F401
    import pandas      # noqa: F401
    import numpy       # noqa: F401
    from sqlalchemy import create_engine  # noqa: F401
    print("✓ Все зависимости установлены")
except ImportError as e:
    print(f"✗ Отсутствует зависимость: {e}")
    print("  Установите: pip install -r requirements.txt")
    sys.exit(1)

try:
    from src.utils.config import load_config
    from src.utils.logger import setup_logger
    from src.runner.components import build_components
    from src.runner.iteration import run_iteration, export_results
    from src.notifications.telegram_notify import notify_telegram, maybe_send_liq_levels
    print("✓ Модули проекта загружены")
except ImportError as e:
    print(f"✗ Ошибка импорта модулей: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

logger = setup_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Paid Screener — реальные данные Binance")
    parser.add_argument("--asset", type=str, default="",
                        help="Актив или несколько через запятую (ETH,SOL). Пусто = все из конфига")
    parser.add_argument("--timeframes", type=str, default="",
                        help="Переопределить таймфреймы (необязательно)")
    parser.add_argument("--min-confidence", type=float, default=0.7,
                        help="Минимальная уверенность сигнала")
    parser.add_argument("--export-json", action="store_true", help="Экспорт в JSON")
    parser.add_argument("--output", type=str, default="data/signals.json", help="Файл вывода")
    parser.add_argument("--limit", type=int, default=500, help="Количество свечей")
    parser.add_argument("--config", type=str, default="config/config.yaml",
                        help="Путь к конфигурации")
    parser.add_argument("--loop", action="store_true", help="Бесконечный цикл")
    parser.add_argument("--loop-interval", type=int, default=300,
                        help="Интервал между итерациями, сек (default: 300)")
    parser.add_argument("--pump-scan", action="store_true",
                        help="Сканировать топ-альты на памп (дополнительно к основному анализу)")

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Paid Screener — реальные данные Binance")
    logger.info("=" * 60)

    init_config = load_config(args.config)
    components = build_components(args, init_config)
    logger.info("Компоненты инициализированы")

    while True:
        try:
            config = load_config(args.config)
            results = run_iteration(args, config, components)
            export_results(results, args)

            logger.info("=" * 60)
            logger.info("Анализ завершён")
            logger.info("=" * 60)

            if args.export_json:
                notify_telegram(
                    args.output,
                    args.min_confidence,
                    pump_signals=results.get("_pump_signals") or [],
                    acc_signals=results.get("_accumulation_signals") or [],
                    results=results,
                )

            if args.loop:
                maybe_send_liq_levels(components.exchange_manager)

        except KeyboardInterrupt:
            logger.info("Остановлено вручную.")
            break
        except Exception as exc:
            logger.error("Ошибка итерации: %s", exc, exc_info=True)

        if not args.loop:
            break

        logger.info("Следующий запуск через %d сек... (Ctrl+C для остановки)", args.loop_interval)
        time.sleep(args.loop_interval)


if __name__ == "__main__":
    main()
