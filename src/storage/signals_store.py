"""Хранилище активных сигналов (active_signals.json)."""
import json
from datetime import datetime
from pathlib import Path

from ..utils.logger import setup_logger

logger = setup_logger(__name__)

ACTIVE_SIGNALS_PATH = Path("data/active_signals.json")


def _load_signals() -> list:
    if not ACTIVE_SIGNALS_PATH.exists():
        return []
    try:
        with ACTIVE_SIGNALS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def has_active_sell(asset: str) -> bool:
    """Есть ли активный SELL-сигнал по этому активу в трекере."""
    for s in _load_signals():
        if (
            s.get("asset") == asset
            and s.get("signal_type") == "SELL"
            and s.get("status") not in {"TP", "SL", "TSL"}
        ):
            return True
    return False


def has_active_buy(asset: str, exclude_tf: str = "") -> bool:
    """Есть ли уже активная BUY-позиция по этому активу на ДРУГОМ таймфрейме."""
    for s in _load_signals():
        if (
            s.get("asset") == asset
            and s.get("signal_type") == "BUY"
            and s.get("status") not in {"TP", "SL", "TSL"}
            and s.get("timeframe") != exclude_tf
        ):
            return True
    return False


def add_signal_to_tracker(signal: dict) -> bool:
    """Добавляет сигнал в active_signals.json. Возвращает True если сигнал новый."""
    existing = _load_signals()
    asset = signal.get("asset")
    tf = signal.get("timeframe")
    stype = signal.get("signal_type")

    for s in existing:
        if (
            s.get("asset") == asset
            and s.get("timeframe") == tf
            and s.get("signal_type") == stype
            and s.get("status") not in {"TP", "SL", "TSL"}
        ):
            logger.info("[%s/%s] Сигнал уже в трекере, пропускаем", asset, tf)
            return False

    signal["added_at"] = datetime.utcnow().isoformat()
    existing.append(signal)
    ACTIVE_SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ACTIVE_SIGNALS_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, default=str)
    logger.info("[%s/%s] ➕ Сигнал добавлен в трекер (%d активных)", asset, tf, len(existing))
    return True
