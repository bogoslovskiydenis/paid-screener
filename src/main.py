"""Главный файл приложения."""
import argparse
import json
import time
from pathlib import Path
from typing import List, Optional
import pandas as pd

try:
    from .utils.config import load_config, get_assets, get_timeframes, Settings
    from .utils.logger import setup_logger
    from .parsers.exchange_manager import ExchangeManager
    from .storage.database import Database
    from .analytics.signals.generator import SignalGenerator
    from .analytics.levels.support_resistance import SupportResistanceAnalyzer
    from .analytics.patterns.head_shoulders import HeadShouldersPattern
    from .analytics.patterns.chart_patterns import ChartPatternDetector
except ImportError:
    from utils.config import load_config, get_assets, get_timeframes, Settings
    from utils.logger import setup_logger
    from parsers.exchange_manager import ExchangeManager
    from storage.database import Database
    from analytics.signals.generator import SignalGenerator
    from analytics.levels.support_resistance import SupportResistanceAnalyzer
    from analytics.patterns.head_shoulders import HeadShouldersPattern
    from analytics.patterns.chart_patterns import ChartPatternDetector

logger = setup_logger(__name__)


class PaidScreener:
    """Основной класс приложения."""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self.settings = Settings()
        self.assets = get_assets(self.config)
        self.timeframes = get_timeframes(self.config)
        
        enabled_exchanges = [
            name for name, exchange_config in self.config.get("exchanges", {}).items()
            if exchange_config.get("enabled", False)
        ]
        
        self.exchange_manager = ExchangeManager(enabled_exchanges)
        self.database = Database(self.settings.database_url)
        self.signal_generator = SignalGenerator(
            min_confidence=self.config.get("analysis", {}).get("signals", {}).get("min_confidence", 0.6)
        )
        self.levels_analyzer = SupportResistanceAnalyzer(
            min_touches=self.config.get("analysis", {}).get("support_resistance", {}).get("min_touches", 2),
            price_tolerance=self.config.get("analysis", {}).get("support_resistance", {}).get("price_tolerance", 0.005)
        )
        self.pattern_analyzer = HeadShouldersPattern(
            min_pattern_length=self.config.get("analysis", {}).get("head_shoulders", {}).get("min_pattern_length", 20),
            symmetry_tolerance=self.config.get("analysis", {}).get("head_shoulders", {}).get("symmetry_tolerance", 0.1)
        )
        self.chart_pattern_detector = ChartPatternDetector(
            min_pattern_length=self.config.get("analysis", {}).get("head_shoulders", {}).get("min_pattern_length", 20),
            price_tolerance=self.config.get("analysis", {}).get("support_resistance", {}).get("price_tolerance", 0.005)
        )
    
    def fetch_and_save_data(
        self,
        asset: str,
        timeframe: str,
        limit: int = 500
    ):
        """Получает и сохраняет данные."""
        try:
            logger.info(f"Fetching data for {asset}/{timeframe}")
            df = self.exchange_manager.get_ohlcv(asset, timeframe, limit=limit)
            
            if df.empty:
                logger.warning(f"No data received for {asset}/{timeframe}")
                return
            
            self.database.save_candles(asset, timeframe, df)
            logger.info(f"Saved {len(df)} candles for {asset}/{timeframe}")
        except Exception as e:
            logger.error(f"Error fetching data for {asset}/{timeframe}: {e}")
    
    def analyze_asset(
        self,
        asset: str,
        timeframe: str
    ) -> dict:
        """Анализирует актив на указанном таймфрейме."""
        try:
            df = self.database.get_candles(asset, timeframe, limit=500)
            
            if len(df) < 100:
                logger.warning(f"Insufficient data for analysis: {asset}/{timeframe}")
                return {}
            
            current_price = float(df.iloc[-1]["close"])
            from .analytics.indicators.rsi import RSICalculator
            rsi_calc = RSICalculator(period=14)
            rsi_analysis = rsi_calc.analyze(df)
            
            results = {
                "current_price": current_price,
                "candles_count": len(df),
                "rsi": rsi_analysis
            }
            
            levels = self.levels_analyzer.find_levels(df)
            if levels:
                results["levels"] = levels
                self.database.save_levels(asset, timeframe, levels)
                
                breakout = self.levels_analyzer.check_breakout(df, levels, volume_confirmation=True)
                if breakout.get("breakout"):
                    results["breakout"] = breakout
                    last_candle = df.iloc[-1]
                    timestamp = last_candle.get("timestamp") if "timestamp" in df.columns else pd.Timestamp.now()
                    if isinstance(timestamp, pd.Timestamp):
                        timestamp = timestamp.to_pydatetime()
                    
                    breakout_data = {
                        "asset": asset,
                        "timeframe": timeframe,
                        "level_type": breakout["level_type"],
                        "level_price": breakout["price"],
                        "level_strength": breakout["strength"],
                        "breakout_price": current_price,
                        "volume_confirmation": breakout.get("volume_confirmation", False),
                        "timestamp": timestamp
                    }
                    self.database.save_breakout(breakout_data)
            
            pattern = self.pattern_analyzer.detect(df)
            if pattern:
                results["pattern"] = pattern
                pattern_data = {
                    "asset": asset,
                    "timeframe": timeframe,
                    **pattern
                }
                self.database.save_pattern(pattern_data)
            
            chart_patterns = self.chart_pattern_detector.detect_all(df)
            if chart_patterns:
                results["chart_patterns"] = chart_patterns
                for chart_pattern in chart_patterns:
                    pattern_data = {
                        "asset": asset,
                        "timeframe": timeframe,
                        "pattern_type": chart_pattern.get("pattern_type"),
                        "pattern_direction": chart_pattern.get("pattern_direction"),
                        "neckline": chart_pattern.get("neckline"),
                        "head_price": chart_pattern.get("head_price") or chart_pattern.get("peak1_price") or chart_pattern.get("apex_price"),
                        "target_price": chart_pattern.get("target_price"),
                        "completion_percentage": chart_pattern.get("completion_percentage", 0.0),
                        "volume_confirmation": chart_pattern.get("volume_confirmation", False),
                        "pattern_metadata": {k: v for k, v in chart_pattern.items() if k not in ["pattern_type", "pattern_direction", "neckline", "head_price", "target_price", "completion_percentage", "volume_confirmation"]}
                    }
                    self.database.save_pattern(pattern_data)
            
            signal = self.signal_generator.generate_signal(asset, timeframe, df)
            if signal:
                results["signal"] = signal
                self.database.save_signal(signal)
            
            return results
        except Exception as e:
            logger.error(f"Error analyzing {asset}/{timeframe}: {e}")
            return {}
    
    def run_analysis(
        self,
        assets: Optional[List[str]] = None,
        timeframes: Optional[List[str]] = None,
        fetch_data: bool = True
    ):
        """Запускает полный анализ."""
        assets = assets or self.assets
        timeframes = timeframes or self.timeframes
        
        if fetch_data:
            for asset in assets:
                for timeframe in timeframes:
                    self.fetch_and_save_data(asset, timeframe)
                    time.sleep(1)
        
        results = {}
        for asset in assets:
            results[asset] = {}
            for timeframe in timeframes:
                results[asset][timeframe] = self.analyze_asset(asset, timeframe)
        
        return results
    
    def export_json(self, results: dict, output_path: str):
        """Экспортирует результаты в JSON."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        def convert_to_dict(obj):
            if isinstance(obj, pd.Timestamp):
                return obj.isoformat()
            elif isinstance(obj, (bool, int, float, str)) or obj is None:
                return obj
            elif isinstance(obj, dict):
                return {k: convert_to_dict(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_to_dict(item) for item in obj]
            elif hasattr(obj, '__dict__'):
                return convert_to_dict(obj.__dict__)
            else:
                return str(obj)
        
        json_data = convert_to_dict(results)
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Results exported to {output_path}")


def main():
    """Главная функция."""
    parser = argparse.ArgumentParser(description="Paid Screener - криптовалютная аналитика")
    parser.add_argument("--asset", type=str, help="Актив для анализа (ETH, SOL)")
    parser.add_argument("--timeframes", type=str, help="Таймфреймы через запятую (15m,4h,1d,1M)")
    parser.add_argument("--min-confidence", type=float, help="Минимальная уверенность сигнала")
    parser.add_argument("--export-json", action="store_true", help="Экспортировать результаты в JSON")
    parser.add_argument("--output", type=str, default="signals.json", help="Путь для экспорта JSON")
    parser.add_argument("--no-fetch", action="store_true", help="Не загружать новые данные")
    parser.add_argument("--config", type=str, default="config/config.yaml", help="Путь к конфигурации")
    
    args = parser.parse_args()
    
    screener = PaidScreener(config_path=args.config)
    
    assets = [args.asset] if args.asset else None
    timeframes = args.timeframes.split(",") if args.timeframes else None
    
    if args.min_confidence:
        screener.signal_generator.min_confidence = args.min_confidence
    
    results = screener.run_analysis(
        assets=assets,
        timeframes=timeframes,
        fetch_data=not args.no_fetch
    )
    
    if args.export_json:
        screener.export_json(results, args.output)
    else:
        print(json.dumps(results, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()

