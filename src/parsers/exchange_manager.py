"""Менеджер для работы с несколькими биржами."""
from typing import Dict, List, Optional
import pandas as pd
from .base import BaseParser
from .binance_parser import BinanceParser
from ..utils.logger import setup_logger

logger = setup_logger(__name__)


class ExchangeManager:
    """Управляет несколькими парсерами бирж."""
    
    def __init__(self, enabled_exchanges: Optional[List[str]] = None):
        self.parsers: Dict[str, BaseParser] = {}
        self.enabled_exchanges = enabled_exchanges or ["binance"]
        self._initialize_parsers()
    
    def _initialize_parsers(self):
        """Инициализирует парсеры для включенных бирж."""
        if "binance" in self.enabled_exchanges:
            try:
                self.parsers["binance"] = BinanceParser()
                logger.info("Binance parser initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Binance parser: {e}")
    
    def get_ohlcv(
        self,
        asset: str,
        timeframe: str,
        limit: int = 500,
        exchange: Optional[str] = None
    ) -> pd.DataFrame:
        """Получает OHLCV данные с указанной или первой доступной биржи."""
        if exchange and exchange in self.parsers:
            parser = self.parsers[exchange]
        elif self.parsers:
            parser = list(self.parsers.values())[0]
        else:
            raise ValueError("No available exchanges")
        
        return parser.fetch_ohlcv(asset, timeframe, limit=limit)
    
    def get_volume(self, asset: str, timeframe: str, exchange: Optional[str] = None) -> float:
        """Получает объем торгов."""
        if exchange and exchange in self.parsers:
            parser = self.parsers[exchange]
        elif self.parsers:
            parser = list(self.parsers.values())[0]
        else:
            raise ValueError("No available exchanges")
        
        return parser.fetch_volume(asset, timeframe)

