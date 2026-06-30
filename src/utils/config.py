"""Утилиты для работы с конфигурацией."""
import os
import yaml
from dataclasses import dataclass
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
        load_dotenv(env_file, encoding="utf-8", override=True)


_apply_dotenv()


class Settings:
    """Настройки приложения (без pydantic — только env и значения по умолчанию)."""

    def __init__(self) -> None:
        self.database_url = os.environ.get("DATABASE_URL", "sqlite:///data/screener.db")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.log_file = os.environ.get("LOG_FILE", "logs/screener.log")


@dataclass
class AnalysisConfig:
    """Параметры аналитики, собранные из config.yaml в один типизированный объект."""
    # support_resistance
    min_touches: int = 2
    price_tolerance: float = 0.005
    # head_shoulders
    min_pattern_length: int = 20
    symmetry_tolerance: float = 0.1
    # signals
    min_confidence: float = 0.7
    test_risk_usd: float = 10.0


def get_analysis_config(config: Dict[str, Any]) -> AnalysisConfig:
    """Читает секцию analysis из конфига и возвращает AnalysisConfig."""
    sr = config.get("analysis", {}).get("support_resistance", {})
    hs = config.get("analysis", {}).get("head_shoulders", {})
    sig = config.get("analysis", {}).get("signals", {})
    return AnalysisConfig(
        min_touches=int(sr.get("min_touches", 2)),
        price_tolerance=float(sr.get("price_tolerance", 0.005)),
        min_pattern_length=int(hs.get("min_pattern_length", 20)),
        symmetry_tolerance=float(hs.get("symmetry_tolerance", 0.1)),
        min_confidence=float(sig.get("min_confidence", 0.7)),
        test_risk_usd=float(sig.get("test_risk_usd", 10.0)),
    )


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
        ["ETH", "SOL", "BTC", "BNB", "XLM", "XRP", "SUI", "LTC", "HBAR", "ADA", "LINK", "AVAX", "ETH/BTC", "SOL/ETH", "SOL/BTC", "XLM/BTC"],
    )


def get_timeframes(config: Dict[str, Any]) -> List[str]:
    """Возвращает список таймфреймов из конфигурации."""
    return config.get("timeframes", ["4h", "1d", "3d", "1w", "1M"])
