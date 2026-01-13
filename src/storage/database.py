"""Модуль для работы с базой данных."""
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Boolean, JSON
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
            signal = Signal(**signal_data)
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

