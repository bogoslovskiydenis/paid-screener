from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
from investiny import historical_data, search_assets

from .base import BaseParser
from ..utils.retry import retry_with_backoff


class InvestingParser(BaseParser):
    """Парсер данных с Investing.com через библиотеку investiny."""

    def __init__(self) -> None:
        super().__init__("investing")
        self._id_cache: Dict[str, int] = {}

    def normalize_symbol(self, asset: str) -> str:
        return asset.upper()

    def _get_investing_id(self, symbol: str) -> int:
        symbol_upper = symbol.upper()
        cached = self._id_cache.get(symbol_upper)
        if cached is not None:
            return cached

        results = search_assets(query=symbol_upper, limit=1, type="Stock")
        if not results:
            raise ValueError(f"Asset {symbol_upper} not found on Investing.com")

        investing_id = int(results[0]["ticker"])
        self._id_cache[symbol_upper] = investing_id
        return investing_id

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        since: Optional[int] = None,
    ) -> pd.DataFrame:
        symbol_norm = self.normalize_symbol(symbol)
        investing_id = self._get_investing_id(symbol_norm)

        interval_map = {
            "5m": 5,
            "15m": 15,
            "1h": 60,
            "4h": 300,
            "1d": "D",
        }
        interval = interval_map.get(timeframe, "D")
        is_intraday = timeframe in ("5m", "15m", "1h", "4h")

        try:
            if is_intraday and since is None:
                data = historical_data(
                    investing_id=investing_id,
                    interval=interval,
                )
            else:
                to_dt = datetime.utcnow()
                if since is not None:
                    from_dt = datetime.utcfromtimestamp(since / 1000.0)
                else:
                    if timeframe in ("5m", "15m"):
                        days_back = 14
                    elif timeframe in ("1h", "4h"):
                        days_back = 60
                    else:
                        days_back = 180
                    from_dt = to_dt - timedelta(days=days_back)

                from_str = from_dt.strftime("%m/%d/%Y")
                to_str = to_dt.strftime("%m/%d/%Y")

                data = historical_data(
                    investing_id=investing_id,
                    from_date=from_str,
                    to_date=to_str,
                    interval=interval,
                )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # рынок закрыт, у investiny нет данных — просто пропускаем этот TF
            if "no_data" in msg:
                return pd.DataFrame()
            raise
        df = pd.DataFrame(data)

        if df.empty:
            return df

        if len(df) > limit:
            df = df.tail(limit)

        lower_to_original = {c.lower(): c for c in df.columns}
        time_col: Optional[str] = None
        for key in ("time", "timestamp", "date", "datetime"):
            original = lower_to_original.get(key)
            if original:
                time_col = original
                break

        if time_col is not None:
            ts = df[time_col]
            if not pd.api.types.is_datetime64_any_dtype(ts):
                if pd.api.types.is_numeric_dtype(ts):
                    max_val = float(ts.max())
                    if max_val > 1e12:
                        df["timestamp"] = pd.to_datetime(ts, unit="ms", utc=True)
                    else:
                        df["timestamp"] = pd.to_datetime(ts, unit="s", utc=True)
                else:
                    df["timestamp"] = pd.to_datetime(ts, utc=True)
            else:
                df["timestamp"] = ts
        else:
            df["timestamp"] = pd.date_range(
                end=to_dt,
                periods=len(df),
                freq="D",
            )

        col_map: Dict[str, str] = {}
        for col in df.columns:
            name = col.lower()
            if name.startswith("open"):
                col_map[col] = "open"
            elif name.startswith("high"):
                col_map[col] = "high"
            elif name.startswith("low"):
                col_map[col] = "low"
            elif name.startswith("close"):
                col_map[col] = "close"
            elif name.startswith("volume"):
                col_map[col] = "volume"

        if col_map:
            df = df.rename(columns=col_map)

        required = ["timestamp", "open", "high", "low", "close", "volume"]
        present = [c for c in required if c in df.columns]
        df = df[present]

        return self.validate_ohlcv(df)

    @retry_with_backoff(max_attempts=3, exceptions=(Exception,))
    def fetch_volume(self, symbol: str, timeframe: str) -> float:
        df = self.fetch_ohlcv(symbol, timeframe, limit=1)
        if df.empty or "volume" not in df.columns:
            return 0.0
        return float(df["volume"].iloc[-1])

