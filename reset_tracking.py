from pathlib import Path

from src.storage.database import Database, Signal, Breakout, Pattern, SupportResistanceLevel, Candle
from src.utils.config import Settings


def reset_database() -> None:
    settings = Settings()
    db = Database(settings.database_url)
    session = db.get_session()
    try:
        # чистим только сигналы и производные объекты
        session.query(Signal).delete()
        session.query(Breakout).delete()
        session.query(Pattern).delete()
        session.query(SupportResistanceLevel).delete()
        session.commit()
    finally:
        session.close()


def reset_active_signals() -> None:
    active_signals_path = Path("data/active_signals.json")
    if active_signals_path.exists():
        active_signals_path.unlink()
    stats_path = Path("data/trade_stats.json")
    if stats_path.exists():
        stats_path.unlink()


def main() -> None:
    reset_database()
    reset_active_signals()


if __name__ == "__main__":
    main()

