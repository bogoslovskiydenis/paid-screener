"""Менеджер для работы с несколькими биржами."""
from typing import Dict, List, Optional, Set

import pandas as pd

from .base import BaseParser
from .binance_parser import BinanceParser
from .investing_parser import InvestingParser
from ..utils.logger import setup_logger

logger = setup_logger(__name__)


STOCK_SYMBOLS: Set[str] = {
    "MU",
    "SNDK",
    "LITE",
    "TSM",
    "GOOGL",
    "GOOG",
    "ASML",
    "AMD",
    "AMZN",
    "PLTR",
    "MRVL",
    "AVGO",
    "FXI",
    "NFLX",
    "META",
    "NVDA",
    "MSFT",
    "IWM",
    "QQQ",
    "SPY",
    "XLF",
    "AAPL",
    "DIA",
    "XLP",
    "GLD",
    "XOP",
    "SLV",
    "USO",
    "TSLA",
}


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

        if "investing" in self.enabled_exchanges:
            try:
                self.parsers["investing"] = InvestingParser()
                logger.info("Investing parser initialized")
            except Exception as exc:
                logger.warning(f"Failed to initialize Investing parser: {exc}")

    def _select_parser(self, asset: str, exchange: Optional[str]) -> BaseParser:
        if exchange and exchange in self.parsers:
            return self.parsers[exchange]

        asset_upper = (asset or "").upper()
        if asset_upper in STOCK_SYMBOLS and "investing" in self.parsers:
            return self.parsers["investing"]

        if "binance" in self.parsers:
            return self.parsers["binance"]

        if self.parsers:
            return list(self.parsers.values())[0]

        raise ValueError("No available exchanges")

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

