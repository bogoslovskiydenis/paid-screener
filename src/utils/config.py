"""Утилиты для работы с конфигурацией."""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, List

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False


def _apply_dotenv() -> None:
    env_file = Path(".env")
    if env_file.exists():
        load_dotenv(env_file, encoding="utf-8")


_apply_dotenv()


class Settings:
    """Настройки приложения (без pydantic — только env и значения по умолчанию)."""

    def __init__(self) -> None:
        self.database_url = os.environ.get("DATABASE_URL", "sqlite:///data/screener.db")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.log_file = os.environ.get("LOG_FILE", "logs/screener.log")


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """Загружает конфигурацию из YAML файла."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_assets(config: Dict[str, Any]) -> List[str]:
    """Возвращает список активов из конфигурации."""
    return config.get(
        "assets",
        ["ETH", "SOL", "BTC", "BNB", "XLM", "ETH/BTC", "SOL/ETH", "SOL/BTC", "XLM/BTC"],
    )


def get_timeframes(config: Dict[str, Any]) -> List[str]:
    """Возвращает список таймфреймов из конфигурации."""
    return config.get("timeframes", ["4h", "1d", "3d", "1w", "1M"])
