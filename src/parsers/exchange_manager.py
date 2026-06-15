"""Менеджер для работы с несколькими биржами."""
from typing import Dict, List, Optional, Set

import pandas as pd

from .base import BaseParser
from .binance_parser import BinanceParser
from ..utils.logger import setup_logger
from ..utils.market_cap import filter_by_market_cap

logger = setup_logger(__name__)

BINANCE_USDT_BASES: Set[str] = {"ETH", "SOL", "BTC", "BNB", "XLM"}

STABLECOINS: Set[str] = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDN", "USDP", "UST",
    "EURS", "FRAX", "LUSD", "USDX", "GUSD", "HUSD", "FDUSD", "PYUSD",
    "USDD", "USTC", "USDJ", "BIDR", "IDRT", "BKRW", "AEUR", "EURT",
    "BFUSD", "SUSDE", "USDE", "EUSDT", "USDTB", "USDF",
    "RLUSD", "USD1", "USDS", "USDG", "USDP0", "GHO", "CRVUSD", "USDB",
    "USDX0", "USDY", "USDM", "USDL", "EURC", "EURI", "XAUT", "PAXG",
}


def _is_pegged(symbol: str) -> bool:
    """True для монет, привязанных к фиату/металлу (стейблы и токенизированные активы).

    Эвристика: символ начинается или заканчивается на USD/EUR (RLUSD, USD1, EURC…),
    либо явно перечислен в STABLECOINS.
    """
    s = symbol.upper()
    if s in STABLECOINS:
        return True
    for peg in ("USD", "EUR", "GBP"):
        if s.startswith(peg) or s.endswith(peg):
            return True
    return False


class ExchangeManager:
    """Управляет несколькими парсерами бирж."""

    def __init__(self, enabled_exchanges: Optional[List[str]] = None):
        self.parsers: Dict[str, BaseParser] = {}
        self.enabled_exchanges = enabled_exchanges or ["binance"]
        self._initialize_parsers()

    def _initialize_parsers(self) -> None:
        """Инициализирует парсеры для включенных бирж."""
        if "binance" in self.enabled_exchanges:
            try:
                self.parsers["binance"] = BinanceParser()
                logger.info("Binance parser initialized")
            except Exception as exc:
                logger.warning(f"Failed to initialize Binance parser: {exc}")

    def _select_parser(self, asset: str, exchange: Optional[str]) -> BaseParser:
        if exchange and exchange in self.parsers:
            return self.parsers[exchange]

        asset_upper = (asset or "").strip().upper().replace(" ", "")
        if "/" in asset_upper and "binance" in self.parsers:
            return self.parsers["binance"]
        if asset_upper in BINANCE_USDT_BASES and "binance" in self.parsers:
            return self.parsers["binance"]

        # Fallback: динамические альты (pump scan) идут через Binance
        if "binance" in self.parsers:
            return self.parsers["binance"]

        raise ValueError(
            f"Нет источника данных для актива {asset!r} "
            f"(спот: {sorted(BINANCE_USDT_BASES)} или кросс-пара вида ETH/BTC)"
        )

    def get_ohlcv(
        self,
        asset: str,
        timeframe: str,
        limit: int = 500,
        exchange: Optional[str] = None
    ) -> pd.DataFrame:
        """Получает OHLCV данные с подходящей биржи."""
        parser = self._select_parser(asset, exchange)
        return parser.fetch_ohlcv(asset, timeframe, limit=limit)

    def get_volume(
        self,
        asset: str,
        timeframe: str,
        exchange: Optional[str] = None,
    ) -> float:
        """Получает объем торгов."""
        parser = self._select_parser(asset, exchange)
        return parser.fetch_volume(asset, timeframe)

    def get_funding_rate(self, asset: str) -> Optional[float]:
        """Возвращает funding rate для крипто-фьючерса (только Binance)."""
        parser = self.parsers.get("binance")
        if parser is None:
            return None
        fetch = getattr(parser, "fetch_funding_rate", None)
        return fetch(asset) if callable(fetch) else None

    def get_long_short_ratio(self, asset: str) -> Optional[float]:
        """
        Возвращает долю лонг-аккаунтов (0–1) из Global L/S Ratio (только Binance).
        0.65 = 65% аккаунтов держат лонг.
        """
        parser = self.parsers.get("binance")
        if parser is None:
            return None
        fetch = getattr(parser, "fetch_long_short_ratio", None)
        return fetch(asset) if callable(fetch) else None

    def get_top_alts(
        self,
        top_n: int = 30,
        min_volume_usd: float = 5_000_000,
        min_market_cap_usd: float = 0,
        exclude: Optional[Set[str]] = None,
    ) -> List[str]:
        """Возвращает базовые тикеры топ-N альтов по объёму с фильтром по market cap."""
        parser = self.parsers.get("binance")
        if parser is None:
            return []
        fetch = getattr(parser, "fetch_top_usdt_pairs", None)
        if not callable(fetch):
            return []
        excl = (exclude or set()) | BINANCE_USDT_BASES | STABLECOINS
        # Берём с запасом чтобы после фильтров осталось достаточно
        all_bases = fetch(top_n=top_n * 3 + len(excl), min_volume_usd=min_volume_usd)
        # Исключаем явные стейблы/привязанные к фиату активы (RLUSD, USD1, EURC…)
        candidates = [b for b in all_bases if b not in excl and not _is_pegged(b)]
        if min_market_cap_usd > 0:
            candidates = filter_by_market_cap(candidates, min_market_cap_usd)
        return candidates[:top_n]

    def get_open_interest(self, asset: str) -> Optional[float]:
        """
        Возвращает текущий Open Interest в контрактах (только Binance).
        Используется для расчёта динамики OI между итерациями.
        """
        parser = self.parsers.get("binance")
        if parser is None:
            return None
        fetch = getattr(parser, "fetch_open_interest", None)
        return fetch(asset) if callable(fetch) else None

    def get_order_book(self, asset: str, limit: int = 100) -> Optional[Dict]:
        """Возвращает стакан заявок (bids/asks) для актива."""
        parser = self.parsers.get("binance")
        if parser is None:
            return None
        fetch = getattr(parser, "fetch_order_book", None)
        return fetch(asset, limit=limit) if callable(fetch) else None

    def get_recent_trades(self, asset: str, limit: int = 100) -> Optional[list]:
        """Возвращает последние сделки для расчёта trade imbalance.

        Каждая сделка: {side: 'buy'|'sell', price, amount, cost}.
        side='buy'  → агрессивный покупатель (тейкер купил).
        side='sell' → агрессивный продавец  (тейкер продал).
        """
        parser = self.parsers.get("binance")
        if parser is None:
            return None
        fetch = getattr(parser, "fetch_trades", None)
        if not callable(fetch):
            return None
        try:
            symbol = f"{asset}/USDT" if "/" not in asset else asset
            return fetch(symbol, limit=limit)
        except Exception:
            return None
