#!/usr/bin/env python3
"""Постоянный сканер: анализ + отправка сигналов в Telegram с безопасным интервалом."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.config import load_config


CRYPTO_ASSETS: List[str] = ["ETH"]
STOCK_ASSETS: List[str] = [
    "MU",
    "SNDK",
    "LITE",
    "TSM",
    "GOOGL",
    "GOOG",
    "ASML",
    "AMD",
    "AMZN",
    "PLTR",
    "MRVL",
    "AVGO",
    "FXI",
    "NFLX",
    "META",
    "NVDA",
    "MSFT",
    "IWM",
    "QQQ",
    "SPY",
    "XLF",
    "AAPL",
    "DIA",
    "XLP",
    "GLD",
    "XOP",
    "SLV",
    "USO",
    "TSLA",
]
TIMEFRAMES_CRYPTO: List[str] = ["5m", "15m", "1h", "4h", "1d"]
TIMEFRAMES_STOCKS: List[str] = ["5m", "15m", "1h", "1d"]
OUTPUT_FILE = "signals_eth_futures.json"
INCLUDE_SELL = True


def run_analysis() -> Optional[str]:
    jobs = [
        (CRYPTO_ASSETS, TIMEFRAMES_CRYPTO, "signals_crypto_tmp.json"),
        (STOCK_ASSETS, TIMEFRAMES_STOCKS, "signals_stocks_tmp.json"),
    ]

    combined: Dict[str, Any] = {}
    have_data = False

    for assets, timeframes, tmp_name in jobs:
        if not assets:
            continue

        cmd = [
            "python3",
            "run_real.py",
            "--asset",
            ",".join(assets),
            "--timeframes",
            ",".join(timeframes),
            "--export-json",
            "--output",
            tmp_name,
        ]

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"Ошибка запуска анализа для {assets}: {exc}")
            continue

        path = Path(tmp_name)
        if not path.exists():
            print(f"Файл с сигналами не найден после анализа: {tmp_name}")
            continue

        try:
            part = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Ошибка чтения файла сигналов {tmp_name}: {exc}")
            continue

        if not isinstance(part, dict):
            continue

        for asset, tf_map in part.items():
            if not isinstance(tf_map, dict):
                continue
            dst = combined.setdefault(asset, {})
            dst.update(tf_map)

        have_data = True

    if not have_data:
        print("Не удалось получить данные ни по одному активу.")
        return None

    final_path = Path(OUTPUT_FILE)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        final_path.write_text(
            json.dumps(combined, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"Ошибка записи объединенного файла сигналов: {exc}")
        return None

    try:
        return final_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Ошибка чтения объединенного файла сигналов: {exc}")
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

