"""Загрузка рыночной капитализации с CoinGecko (публичный API, без ключа)."""
import time
from typing import Dict, Optional

import requests

try:
    from .logger import setup_logger
except ImportError:
    import logging
    def setup_logger(name: str):  # type: ignore[misc]
        return logging.getLogger(name)

logger = setup_logger(__name__)

_COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
_CACHE_TTL = 3600  # 1 час

_cache_data: Dict[str, float] = {}   # symbol.upper() → market_cap_usd
_cache_ts: float = 0.0


def get_market_caps(force_refresh: bool = False) -> Dict[str, float]:
    """Возвращает словарь {SYMBOL: market_cap_usd} для топ-500 монет.

    Данные кэшируются на 1 час, чтобы не превышать лимиты CoinGecko (~30 req/min).
    """
    global _cache_data, _cache_ts

    if not force_refresh and _cache_data and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache_data

    result: Dict[str, float] = {}
    try:
        for page in (1, 2):  # 2 страницы × 250 = топ-500
            resp = requests.get(
                _COINGECKO_URL,
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": 250,
                    "page": page,
                    "sparkline": "false",
                    "price_change_percentage": "",
                },
                timeout=10,
            )
            resp.raise_for_status()
            for coin in resp.json():
                sym = (coin.get("symbol") or "").upper()
                mc = coin.get("market_cap")
                if sym and mc is not None and sym.isascii() and sym.replace("-", "").isalnum():
                    result[sym] = float(mc)
            time.sleep(0.5)  # пауза между страницами

        _cache_data = result
        _cache_ts = time.time()
        logger.info("CoinGecko: загружено %d монет с market cap", len(result))
    except Exception as exc:
        logger.warning("CoinGecko market cap недоступен: %s", exc)
        # Возвращаем старый кэш если есть, иначе пустой словарь
        return _cache_data or {}

    return result


def filter_by_market_cap(
    symbols: list,
    min_market_cap_usd: float,
) -> list:
    """Оставляет из symbols только те, у которых market cap >= min_market_cap_usd."""
    caps = get_market_caps()
    if not caps:
        logger.warning("Market cap данные недоступны — фильтр по капитализации пропущен")
        return symbols

    filtered = [s for s in symbols if caps.get(s.upper(), 0) >= min_market_cap_usd]
    logger.info(
        "Фильтр по market cap (>=$%.0f): %d → %d монет",
        min_market_cap_usd, len(symbols), len(filtered),
    )
    return filtered
