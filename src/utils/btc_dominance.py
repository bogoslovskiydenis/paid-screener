"""BTC Dominance — доля BTC в общей капитализации крипторынка."""
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

_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_CACHE_TTL = 3600  # 1 час
_cache: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0


def get_btc_dominance() -> Dict[str, Any]:
    """
    Доминация BTC растёт → деньги уходят из альтов (медведь для альтов).
    Доминация BTC падает → альтсезон (бык для альтов).

    Порог альтсезона: < 50%.
    Порог BTC-сезона: > 60%.
    """
    global _cache, _cache_ts

    if _cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache

    try:
        resp = requests.get(_GLOBAL_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", {})

        btc_dom = float(data.get("market_cap_percentage", {}).get("btc", 50))
        eth_dom = float(data.get("market_cap_percentage", {}).get("eth", 15))
        total_mcap = float(data.get("total_market_cap", {}).get("usd", 0))
        mcap_change_24h = float(data.get("market_cap_change_percentage_24h_usd", 0))

        if btc_dom > 60:
            season = "BTC_SEASON"
        elif btc_dom < 50:
            season = "ALT_SEASON"
        else:
            season = "NEUTRAL"

        result: Dict[str, Any] = {
            "btc_dominance": round(btc_dom, 1),
            "eth_dominance": round(eth_dom, 1),
            "total_market_cap_usd": total_mcap,
            "market_cap_change_24h_pct": round(mcap_change_24h, 2),
            "season": season,
        }

        _cache = result
        _cache_ts = time.time()
        logger.info(
            "BTC Dominance: %.1f%% | ETH: %.1f%% | Total MCap: $%.0fB | Сезон: %s",
            btc_dom, eth_dom, total_mcap / 1e9, season,
        )
        return result

    except Exception as exc:
        logger.warning("CoinGecko global data недоступен: %s", exc)
        return _cache or {
            "btc_dominance": 50.0, "eth_dominance": 15.0,
            "total_market_cap_usd": 0, "market_cap_change_24h_pct": 0,
            "season": "NEUTRAL",
        }
