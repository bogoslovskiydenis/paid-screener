"""Парсер для Binance."""
import pandas as pd
import time
from typing import Optional
from .base import BaseParser
from ..utils.retry import retry_with_backoff


class BinanceParser(BaseParser):
    """Парсер данных с Binance."""
    
    def __init__(self):
        super().__init__("binance")
        try:
            import ccxt
            self.exchange = ccxt.binance({
                "enableRateLimit": True,
                "rateLimit": 1200,
            })
        except ImportError:
            raise ImportError("ccxt library is required for Binance parser")
    
    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: Optional[int] = None
    ) -> pd.DataFrame:
        """Получает OHLCV данные с Binance."""
        symbol = self.normalize_symbol(symbol)
        timeframe_map = {
            "15m": "15m",
            "4h": "4h",
            "1d": "1d",
            "1M": "1M"
        }
        
        tf = timeframe_map.get(timeframe, timeframe)
        
        # Проверка доступности таймфрейма
        if tf not in self.exchange.timeframes:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Available: {list(self.exchange.timeframes.keys())}")
        
        ohlcv = self.exchange.fetch_ohlcv(symbol, tf, limit=limit, since=since)
        
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        
        return self.validate_ohlcv(df)
    
    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def fetch_volume(self, symbol: str, timeframe: str) -> float:
        """Получает объем торгов с Binance."""
        symbol = self.normalize_symbol(symbol)
        ticker = self.exchange.fetch_ticker(symbol)
        return ticker.get("quoteVolume", 0.0)

