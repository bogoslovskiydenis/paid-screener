#!/usr/bin/env python3
"""Постоянный сканер: анализ + отправка сигналов в Telegram с безопасным интервалом.

Раньше каждая итерация запускала run_real.py и telegram_bot.py через subprocess —
все компоненты (биржи, БД, генератор) пересоздавались на каждый цикл.
Теперь run_iteration/broadcast_signals вызываются напрямую, компоненты живут
всё время работы процесса.
"""

import argparse
import time
from pathlib import Path
from typing import Optional

from src.utils.config import load_config, get_assets, get_timeframes
from src.utils.logger import setup_logger
from src.runner.components import build_components
from src.runner.iteration import run_iteration, export_results
import telegram_bot

CONFIG_PATH = "config/config.yaml"
OUTPUT_FILE = "data/signals_spot.json"
SUBSCRIBERS_PATH = Path("data/telegram_subscribers.json")
INCLUDE_SELL = True

logger = setup_logger(__name__)


def _build_args(config: dict, min_confidence: float) -> argparse.Namespace:
    """Namespace в формате CLI-аргументов run_real.py — его ждёт run_iteration."""
    return argparse.Namespace(
        asset=",".join(get_assets(config)),
        timeframes=",".join(get_timeframes(config)),
        min_confidence=min_confidence,
        export_json=True,
        output=OUTPUT_FILE,
        limit=500,
        config=CONFIG_PATH,
        loop=False,
        loop_interval=0,
        pump_scan=False,
    )


def send_signals(min_confidence: float) -> None:
    send_args = argparse.Namespace(
        asset="",
        timeframes="",
        include_sell=INCLUDE_SELL,
        min_confidence=min_confidence,
    )
    try:
        telegram_bot.broadcast_signals(
            token=telegram_bot.TELEGRAM_BOT_TOKEN,
            subscribers_path=SUBSCRIBERS_PATH,
            signals_file=Path(OUTPUT_FILE),
            args=send_args,
        )
    except Exception as exc:
        logger.error("Ошибка отправки сигналов в Telegram: %s", exc, exc_info=True)


def main() -> None:
    config = load_config(CONFIG_PATH)
    interval = config.get("updates", {}).get("interval_seconds", 60)
    min_conf = config.get("analysis", {}).get("signals", {}).get("min_confidence", 0.6)
    logger.info("Интервал обновления: %d сек", interval)
    logger.info("Минимальная уверенность сигнала для рассылки: %s", min_conf)

    args = _build_args(config, min_conf)
    components = build_components(args, config)
    logger.info("Компоненты инициализированы")

    last_snapshot: Optional[str] = None

    while True:
        try:
            config = load_config(CONFIG_PATH)  # активы/ТФ можно менять на лету
            args = _build_args(config, min_conf)
            results = run_iteration(args, config, components)
            export_results(results, args)
            snapshot = Path(OUTPUT_FILE).read_text(encoding="utf-8")
        except KeyboardInterrupt:
            logger.info("Остановлено вручную.")
            break
        except Exception as exc:
            logger.error("Ошибка итерации анализа: %s", exc, exc_info=True)
            time.sleep(interval)
            continue

        if snapshot != last_snapshot:
            logger.info("Сигналы изменились, выполняем рассылку...")
            send_signals(min_confidence=min_conf)
            last_snapshot = snapshot
        else:
            logger.info("Сигналы не изменились, рассылка пропущена.")

        time.sleep(interval)


if __name__ == "__main__":
    main()
