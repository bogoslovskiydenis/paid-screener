"""Утилиты для работы с конфигурацией."""
import yaml
import os
from pathlib import Path
from typing import Dict, Any, List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Настройки приложения."""
    database_url: str = "sqlite:///data/screener.db"
    log_level: str = "INFO"
    log_file: str = "logs/screener.log"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """Загружает конфигурацию из YAML файла."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_assets(config: Dict[str, Any]) -> List[str]:
    """Возвращает список активов из конфигурации."""
    return config.get("assets", ["ETH", "SOL"])


def get_timeframes(config: Dict[str, Any]) -> List[str]:
    """Возвращает список таймфреймов из конфигурации."""
    return config.get("timeframes", ["15m", "4h", "1d", "1M"])

