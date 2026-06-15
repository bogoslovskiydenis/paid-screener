"""Модуль для работы с базой данных."""
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd
from ..utils.logger import setup_logger

logger = setup_logger(__name__)

Base = declarative_base()


class Candle(Base):
    """Модель для хранения свечей."""
    __tablename__ = "candles"
    
    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SupportResistanceLevel(Base):
    """Модель для уровней поддержки/сопротивления."""
    __tablename__ = "support_resistance_levels"
    
    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    level_type = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    strength = Column(Float, nullable=False)
    touches = Column(Integer, nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)


class Pattern(Base):
    """Модель для обнаруженных паттернов."""
    __tablename__ = "patterns"
    
    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    pattern_type = Column(String, nullable=False)
    pattern_direction = Column(String, nullable=False)
    neckline = Column(Float, nullable=True)
    head_price = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    completion_percentage = Column(Float, nullable=False)
    volume_confirmation = Column(Boolean, default=False)
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)
    pattern_metadata = Column(JSON, nullable=True)


class Signal(Base):
    """Модель для торговых сигналов."""
    __tablename__ = "signals"
    
    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    signal_type = Column(String, nullable=False)
    strength = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    current_price = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(JSON, nullable=True)
    indicators = Column(JSON, nullable=True)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    test_trade = Column(JSON, nullable=True)


class Trade(Base):
    """Закрытая сделка (журнал для анализа)."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    signal_type = Column(String, nullable=False)
    strength = Column(String, nullable=True)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    result = Column(String, nullable=False)
    confidence = Column(Float, nullable=True)
    risk_usd = Column(Float, nullable=True)
    qty = Column(Float, nullable=True)
    pnl_usd = Column(Float, nullable=True)
    pnl_rr = Column(Float, nullable=True)
    opened_at = Column(DateTime, nullable=True, index=True)
    closed_at = Column(DateTime, nullable=False, index=True)
    source = Column(String, nullable=False, default="tracker")
    notes = Column(Text, nullable=True)
    signal_snapshot = Column(JSON, nullable=True)


class Breakout(Base):
    """Модель для пробоев уровней."""
    __tablename__ = "breakouts"
    
    id = Column(Integer, primary_key=True)
    asset = Column(String, nullable=False, index=True)
    timeframe = Column(String, nullable=False, index=True)
    level_type = Column(String, nullable=False)
    level_price = Column(Float, nullable=False)
    level_strength = Column(Float, nullable=False)
    breakout_price = Column(Float, nullable=False)
    volume_confirmation = Column(Boolean, default=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Database:
    """Класс для работы с базой данных."""
    
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        logger.info(f"Database initialized: {database_url}")
    
    def get_session(self) -> Session:
        """Возвращает сессию базы данных."""
        return self.Session()
    
    def save_candles(self, asset: str, timeframe: str, df: pd.DataFrame):
        """Сохраняет свечи в базу данных."""
        session = self.get_session()
        try:
            for _, row in df.iterrows():
                candle = Candle(
                    asset=asset,
                    timeframe=timeframe,
                    timestamp=row["timestamp"],
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"]
                )
                session.merge(candle)
            session.commit()
            logger.debug(f"Saved {len(df)} candles for {asset}/{timeframe}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving candles: {e}")
            raise
        finally:
            session.close()
    
    def get_candles(
        self,
        asset: str,
        timeframe: str,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """Получает свечи из базы данных."""
        session = self.get_session()
        try:
            query = session.query(Candle).filter(
                Candle.asset == asset,
                Candle.timeframe == timeframe
            ).order_by(Candle.timestamp.desc())
            
            if limit:
                query = query.limit(limit)
            
            candles = query.all()
            
            if not candles:
                return pd.DataFrame()
            
            data = [{
                "timestamp": c.timestamp,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume
            } for c in reversed(candles)]
            
            return pd.DataFrame(data)
        finally:
            session.close()
    
    def save_signal(self, signal_data: Dict[str, Any]):
        """Сохраняет торговый сигнал."""
        session = self.get_session()
        try:
            import numpy as np
            # Конвертируем numpy типы и bool в стандартные Python типы для JSON сериализации
            signal_data_copy = signal_data.copy()
            if "indicators" in signal_data_copy and isinstance(signal_data_copy["indicators"], dict):
                indicators = signal_data_copy["indicators"].copy()
                for key, value in indicators.items():
                    if isinstance(value, (bool, np.bool_)):
                        indicators[key] = bool(value)
                    elif isinstance(value, (np.integer, np.floating)):
                        indicators[key] = float(value) if isinstance(value, np.floating) else int(value)
                signal_data_copy["indicators"] = indicators
            
            # Конвертируем numpy типы в основных полях
            for key, value in signal_data_copy.items():
                if isinstance(value, (np.integer, np.floating)):
                    signal_data_copy[key] = float(value) if isinstance(value, np.floating) else int(value)
                elif isinstance(value, (bool, np.bool_)):
                    signal_data_copy[key] = bool(value)

            _SIGNAL_COLUMNS = {c.name for c in Signal.__table__.columns}
            signal_data_copy = {k: v for k, v in signal_data_copy.items() if k in _SIGNAL_COLUMNS}
            signal = Signal(**signal_data_copy)
            session.add(signal)
            session.commit()
            logger.info(f"Signal saved: {signal_data.get('signal_type')} for {signal_data.get('asset')}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving signal: {e}")
            raise
        finally:
            session.close()
    
    def save_pattern(self, pattern_data: Dict[str, Any]):
        """Сохраняет обнаруженный паттерн."""
        session = self.get_session()
        try:
            pattern = Pattern(**pattern_data)
            session.add(pattern)
            session.commit()
            logger.info(f"Pattern saved: {pattern_data.get('pattern_type')} for {pattern_data.get('asset')}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving pattern: {e}")
            raise
        finally:
            session.close()
    
    def save_levels(self, asset: str, timeframe: str, levels_data: Dict[str, Any]):
        """Сохраняет уровни поддержки/сопротивления."""
        session = self.get_session()
        try:
            for level_type in ["support_levels", "resistance_levels"]:
                levels = levels_data.get(level_type, [])
                for level in levels:
                    sr_level = SupportResistanceLevel(
                        asset=asset,
                        timeframe=timeframe,
                        level_type=level_type.replace("_levels", ""),
                        price=level["price"],
                        strength=level["strength"],
                        touches=level["touches"]
                    )
                    session.merge(sr_level)
            session.commit()
            logger.debug(f"Saved levels for {asset}/{timeframe}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving levels: {e}")
            raise
        finally:
            session.close()
    
    def save_breakout(self, breakout_data: Dict[str, Any]):
        """Сохраняет пробой уровня."""
        session = self.get_session()
        try:
            breakout = Breakout(**breakout_data)
            session.add(breakout)
            session.commit()
            logger.info(f"Breakout saved: {breakout_data.get('level_type')} at {breakout_data.get('level_price')} for {breakout_data.get('asset')}")
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving breakout: {e}")
            raise
        finally:
            session.close()

    def save_trade(self, trade_data: Dict[str, Any]) -> int:
        """Сохраняет закрытую сделку в журнал."""
        session = self.get_session()
        try:
            import numpy as np

            def _json_safe(obj: Any) -> Any:
                if isinstance(obj, dict):
                    return {k: _json_safe(v) for k, v in obj.items()}
                if isinstance(obj, list):
                    return [_json_safe(v) for v in obj]
                if isinstance(obj, datetime):
                    return obj.isoformat()
                if isinstance(obj, (np.integer, np.floating)):
                    return float(obj) if isinstance(obj, np.floating) else int(obj)
                if isinstance(obj, (bool, np.bool_)):
                    return bool(obj)
                return obj

            data = dict(trade_data)
            snap = data.pop("signal_snapshot", None)
            if snap is not None:
                snap = _json_safe(snap)

            closed_at = data.pop("closed_at", None) or datetime.utcnow()
            trade = Trade(
                asset=data["asset"],
                timeframe=data["timeframe"],
                signal_type=data["signal_type"],
                strength=data.get("strength"),
                entry_price=float(data["entry_price"]),
                exit_price=float(data["exit_price"]),
                result=data["result"],
                confidence=data.get("confidence"),
                risk_usd=data.get("risk_usd"),
                qty=data.get("qty"),
                pnl_usd=data.get("pnl_usd"),
                pnl_rr=data.get("pnl_rr"),
                opened_at=data.get("opened_at"),
                closed_at=closed_at,
                source=data.get("source") or "tracker",
                notes=data.get("notes"),
                signal_snapshot=snap,
            )
            session.add(trade)
            session.commit()
            tid = int(trade.id)
            logger.info(f"Trade saved: id={tid} {trade.asset} {trade.result}")
            return tid
        except Exception as e:
            session.rollback()
            logger.error(f"Error saving trade: {e}")
            raise
        finally:
            session.close()

    def get_trade_analytics(self) -> Dict[str, Any]:
        """Агрегированная аналитика по закрытым сделкам из таблицы trades."""
        session = self.get_session()
        try:
            trades = session.query(Trade).all()
            if not trades:
                return {"total": 0}

            total = len(trades)
            wins = sum(1 for t in trades if t.result in ("TP", "TSL"))
            losses = sum(1 for t in trades if t.result == "SL")
            win_rate = wins / total * 100 if total else 0.0

            pnl_values = [t.pnl_usd for t in trades if t.pnl_usd is not None]
            rr_values = [t.pnl_rr for t in trades if t.pnl_rr is not None]

            def _group_stats(key_fn) -> Dict[str, Any]:
                groups: Dict[str, list] = {}
                for t in trades:
                    k = key_fn(t)
                    groups.setdefault(k, []).append(t)
                out = {}
                for k, ts in sorted(groups.items()):
                    w = sum(1 for t in ts if t.result in ("TP", "TSL"))
                    out[k] = {
                        "total": len(ts),
                        "wins": w,
                        "losses": len(ts) - w,
                        "win_rate": round(w / len(ts) * 100, 1),
                        "pnl_usd": round(sum(t.pnl_usd for t in ts if t.pnl_usd), 2),
                    }
                return out

            def _conf_bucket(t: Trade) -> str:
                c = t.confidence or 0
                if c >= 0.85:
                    return "0.85+"
                if c >= 0.80:
                    return "0.80-0.85"
                if c >= 0.75:
                    return "0.75-0.80"
                return "<0.75"

            return {
                "total": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 1),
                "total_pnl_usd": round(sum(pnl_values), 2) if pnl_values else None,
                "avg_rr_on_wins": round(
                    sum(r for r in rr_values if r and r > 0) / max(1, sum(1 for r in rr_values if r and r > 0)), 2
                ) if rr_values else None,
                "by_asset": _group_stats(lambda t: t.asset),
                "by_timeframe": _group_stats(lambda t: t.timeframe),
                "by_strength": _group_stats(lambda t: t.strength or "?"),
                "by_confidence": _group_stats(_conf_bucket),
            }
        finally:
            session.close()

    def get_trades(
        self,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Возвращает закрытые сделки (новые первые)."""
        session = self.get_session()
        try:
            q = session.query(Trade).order_by(Trade.closed_at.desc())
            if asset:
                q = q.filter(Trade.asset == asset)
            if limit:
                q = q.limit(limit)
            rows = q.all()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append(
                    {
                        "id": r.id,
                        "asset": r.asset,
                        "timeframe": r.timeframe,
                        "signal_type": r.signal_type,
                        "strength": r.strength,
                        "entry_price": r.entry_price,
                        "exit_price": r.exit_price,
                        "result": r.result,
                        "confidence": r.confidence,
                        "risk_usd": r.risk_usd,
                        "qty": r.qty,
                        "pnl_usd": r.pnl_usd,
                        "pnl_rr": r.pnl_rr,
                        "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                        "closed_at": r.closed_at.isoformat() if r.closed_at else None,
                        "source": r.source,
                        "notes": r.notes,
                        "signal_snapshot": r.signal_snapshot,
                    }
                )
            return out
        finally:
            session.close()

