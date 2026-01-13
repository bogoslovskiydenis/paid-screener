"""Базовый класс для парсеров."""
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import pandas as pd
from datetime import datetime


class BaseParser(ABC):
    """Базовый класс для всех парсеров."""
    
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.rate_limit_delay = 0
    
    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: int = None
    ) -> pd.DataFrame:
        """Получает OHLCV данные."""
        pass
    
    @abstractmethod
    def fetch_volume(self, symbol: str, timeframe: str) -> float:
        """Получает объем торгов."""
        pass
    
    def normalize_symbol(self, asset: str) -> str:
        """Нормализует символ для биржи."""
        return f"{asset}/USDT"
    
    def validate_ohlcv(self, df: pd.DataFrame) -> pd.DataFrame:
        """Валидирует OHLCV данные."""
        required_columns = ["open", "high", "low", "close", "volume", "timestamp"]
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f"Missing required columns in OHLCV data")
        
        df = df.dropna()
        df = df[df["volume"] > 0]
        df = df.sort_values("timestamp")
        
        return df

