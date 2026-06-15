"""Парсер для Binance."""
import pandas as pd
import time
from typing import Optional, Any, Dict, List
from .base import BaseParser
from ..utils.retry import retry_with_backoff
from ..utils.logger import setup_logger

logger = setup_logger(__name__)


class BinanceParser(BaseParser):
    """Парсер данных с Binance."""

    def __init__(self, market_type: str = "spot"):
        super().__init__("binance")
        try:
            import ccxt
            options = {
                "enableRateLimit": True,
                "rateLimit": 1200,
                "options": {
                    "defaultType": "future" if market_type == "future" else "spot",
                },
            }
            self.exchange = ccxt.binance(options)
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

    def fetch_funding_rate(self, asset: str) -> Optional[float]:
        """Возвращает текущий funding rate для perpetual фьючерса."""
        try:
            symbol = f"{asset}/USDT:USDT"
            data = self.exchange.fetch_funding_rate(symbol)
            rate = data.get("fundingRate")
            return float(rate) if rate is not None else None
        except Exception:
            return None

    def fetch_long_short_ratio(self, asset: str) -> Optional[float]:
        """
        Возвращает долю лонг-аккаунтов (0–1) из Global L/S Account Ratio Binance.
        Например, 0.65 = 65% аккаунтов держат лонг.
        Требует Binance FAPI; возвращает None при любой ошибке.
        """
        try:
            method = getattr(self.exchange, "fapiPublicGetGlobalLongShortAccountRatio", None)
            if not callable(method):
                return None
            data = method({"symbol": f"{asset}USDT", "period": "1h", "limit": 1})
            if data and isinstance(data, list) and len(data) > 0:
                long_account = data[-1].get("longAccount")
                if long_account is not None:
                    return float(long_account)
            return None
        except Exception:
            return None

    def fetch_open_interest(self, asset: str) -> Optional[float]:
        """
        Возвращает текущий Open Interest (в контрактах) для perpetual фьючерса.
        Используется для отслеживания динамики между итерациями.
        """
        try:
            method = getattr(self.exchange, "fapiPublicGetOpenInterest", None)
            if not callable(method):
                return None
            data = method({"symbol": f"{asset}USDT"})
            oi = data.get("openInterest")
            return float(oi) if oi is not None else None
        except Exception:
            return None

    def fetch_top_usdt_pairs(
        self,
        top_n: int = 50,
        min_volume_usd: float = 20_000_000,
    ) -> List[str]:
        """Возвращает базовые тикеры топ-N USDT-пар по 24ч объёму с Binance."""
        try:
            tickers = self.exchange.fetch_tickers()
            pairs: List[tuple] = []
            for symbol, ticker in tickers.items():
                if not symbol.endswith("/USDT"):
                    continue
                quote_vol = ticker.get("quoteVolume") or 0.0
                if float(quote_vol) < min_volume_usd:
                    continue
                base = symbol.split("/")[0]
                pairs.append((base, float(quote_vol)))
            pairs.sort(key=lambda x: x[1], reverse=True)
            return [base for base, _ in pairs[:top_n]]
        except Exception as exc:
            logger.warning("Не удалось загрузить топ USDT-пары: %s", exc)
            return []

    def fetch_order_book(self, asset: str, limit: int = 100) -> Optional[Dict[str, Any]]:
        """Возвращает стакан заявок: {bids: [[price, qty], ...], asks: [...]}."""
        try:
            symbol = f"{asset}/USDT" if "/" not in asset else asset
            ob = self.exchange.fetch_order_book(symbol, limit=limit)
            return {"bids": ob.get("bids", []), "asks": ob.get("asks", [])}
        except Exception as exc:
            logger.warning("Не удалось загрузить стакан %s: %s", asset, exc)
            return None

