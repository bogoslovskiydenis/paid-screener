"""Fear & Greed Index — индекс страха и жадности крипторынка."""
import time
from typing import Dict, Any, Optional

import requests

try:
    from .logger import setup_logger
except ImportError:
    import logging
    def setup_logger(name: str):
        return logging.getLogger(name)

logger = setup_logger(__name__)

_API_URL = "https://api.alternative.me/fng/"
_CACHE_TTL = 1800  # 30 минут
_cache: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0


def get_fear_greed() -> Dict[str, Any]:
    """
    Возвращает текущий Fear & Greed Index (0–100).

    0–24:  Extreme Fear  → потенциал покупки
    25–49: Fear          → осторожность, но можно
    50–74: Greed         → рынок перегрет
    75–100: Extreme Greed → опасно покупать
    """
    global _cache, _cache_ts

    if _cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        resp = requests.get(_API_URL, params={"limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [{}])[0]

        value = int(data.get("value", 50))
        classification = str(data.get("value_classification", "Neutral"))

        if value <= 24:
            zone = "EXTREME_FEAR"
        elif value <= 49:
            zone = "FEAR"
        elif value <= 74:
            zone = "GREED"
        else:
            zone = "EXTREME_GREED"

        result: Dict[str, Any] = {
            "value": value,
            "classification": classification,
            "zone": zone,
        }

        _cache = result
        _cache_ts = time.time()
        logger.info("Fear & Greed Index: %d (%s)", value, classification)
        return result

    except Exception as exc:
        logger.warning("Fear & Greed API недоступен: %s", exc)
        return _cache or {"value": 50, "classification": "Neutral", "zone": "NEUTRAL"}
