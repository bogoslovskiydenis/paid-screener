import ccxt
import pandas as pd
from src.analytics.signals.generator import SignalGenerator

ex = ccxt.binance()
gen = SignalGenerator(min_confidence=0.0)  # без порога — посмотреть сырую оценку

for tf in ("4h", "1d"):
    ohlcv = ex.fetch_ohlcv("SOL/USDT", tf, limit=500)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    sig = gen.generate_signal("SOL", tf, df)
    if sig:
        print(f"{tf}: {sig['signal_type']} conf={sig['confidence']:.2f} "
              f"strength={sig.get('strength')} entry={sig.get('entry_price'):.2f} "
              f"SL={sig.get('stop_loss'):.2f} type={sig.get('entry_type', sig.get('setup', '?'))}")
        for r in sig.get("reasons", [])[:6]:
            print("   -", r)
    else:
        print(f"{tf}: сигнала нет")
