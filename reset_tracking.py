from pathlib import Path

from src.utils.config import Settings


def reset_database() -> None:
    settings = Settings()
    db_url = settings.database_url

    if db_url.startswith("sqlite:///"):
        db_path_str = db_url.replace("sqlite:///", "", 1)
        db_path = Path(db_path_str)
        if db_path.exists():
            db_path.unlink()


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

