"""Фасад над active_signals в SQLite."""
from typing import Optional
from ..trading.trade_rules import evaluate_for_budget
from ..utils.logger import setup_logger
from ..utils.config import Settings
from .database import Database

logger = setup_logger(__name__)

_db: Optional[Database] = None


def _get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(Settings().database_url)
    return _db


def has_active_sell(asset: str) -> bool:
    return _get_db().has_active_sell(asset)


def has_active_buy(asset: str, exclude_tf: str = "") -> bool:
    return _get_db().has_active_buy(asset, exclude_tf)


def add_signal_to_tracker(signal: dict) -> bool:
    """Добавляет сигнал в БД. Возвращает True если сигнал новый."""
    db = _get_db()
    evaluate_for_budget(signal, db.get_open_signals)
    is_new = db.add_active_signal(signal)
    asset, tf = signal.get("asset"), signal.get("timeframe")
    if is_new:
        logger.info("[%s/%s] ➕ Сигнал добавлен в трекер", asset, tf)
        if signal.get("in_budget"):
            logger.info("[%s/%s] 💰 %s", asset, tf, signal.get("budget_reason"))
        else:
            logger.info("[%s/%s] Вне портфеля: %s", asset, tf, signal.get("budget_reason"))
    else:
        logger.info("[%s/%s] Сигнал уже в трекере, пропускаем", asset, tf)
    return is_new
