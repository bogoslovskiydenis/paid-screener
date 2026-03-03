#!/usr/bin/env python3
"""Постоянный сканер: анализ + отправка сигналов в Telegram с безопасным интервалом."""

import subprocess
import time
from pathlib import Path
from typing import List, Optional

from src.utils.config import load_config


ASSETS: List[str] = ["ETH", "XRP", "SOL", "ADA", "NEAR"]
TIMEFRAMES: List[str] = ["15m", "1h", "4h", "1d"]
OUTPUT_FILE = "signals_eth_xrp_sol.json"
INCLUDE_SELL = True


def run_analysis() -> Optional[str]:
    cmd = [
        "python3",
        "run_real.py",
        "--asset",
        ",".join(ASSETS),
        "--timeframes",
        ",".join(TIMEFRAMES),
        "--export-json",
        "--output",
        OUTPUT_FILE,
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Ошибка запуска анализа: {exc}")
        return None

    path = Path(OUTPUT_FILE)
    if not path.exists():
        print(f"Файл с сигналами не найден после анализа: {OUTPUT_FILE}")
        return None

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Ошибка чтения файла сигналов: {exc}")
        return None


def send_signals(min_confidence: float) -> None:
    cmd = [
        "python3",
        "telegram_bot.py",
        "--mode",
        "send",
        "--signals-file",
        OUTPUT_FILE,
        "--min-confidence",
        str(min_confidence),
    ]
    if INCLUDE_SELL:
        cmd.append("--include-sell")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Ошибка отправки сигналов в Telegram: {exc}")


def main() -> None:
    config = load_config("config/config.yaml")
    interval = config.get("updates", {}).get("interval_seconds", 60)
    min_conf = config.get("analysis", {}).get("signals", {}).get("min_confidence", 0.6)
    print(f"Интервал обновления: {interval} сек")
    print(f"Минимальная уверенность сигнала для рассылки: {min_conf}")

    last_snapshot: Optional[str] = None

    while True:
        snapshot = run_analysis()
        if snapshot is None:
            time.sleep(interval)
            continue

        if snapshot != last_snapshot:
            print("Сигналы изменились, выполняем рассылку...")
            send_signals(min_confidence=min_conf)
            last_snapshot = snapshot
        else:
            print("Сигналы не изменились, рассылка пропущена.")

        time.sleep(interval)


if __name__ == "__main__":
    main()

