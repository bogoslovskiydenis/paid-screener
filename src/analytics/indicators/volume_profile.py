"""Volume Profile — распределение объёма по ценовым уровням."""
import numpy as np
import pandas as pd
from typing import Dict, Any


class VolumeProfileCalculator:
    """
    Строит Volume Profile из OHLCV-данных.

    POC  — Point of Control: цена с макс. объёмом (сильнейший магнит).
    VAH  — Value Area High: верхняя граница зоны 70% объёма.
    VAL  — Value Area Low:  нижняя граница зоны 70% объёма.
    HVN  — High Volume Nodes: зоны притяжения (поддержка/сопротивление).
    LVN  — Low Volume Nodes: зоны быстрого прохода (пробой).
    """

    def __init__(self, num_bins: int = 50, value_area_pct: float = 0.70):
        self.num_bins = num_bins
        self.value_area_pct = value_area_pct

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        if len(df) < 20:
            return {
                "poc": None, "vah": None, "val": None,
                "hvn": [], "lvn": [],
                "price_vs_poc": "NEUTRAL",
            }

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        volumes = df["volume"].values.astype(float)

        price_min = float(np.min(lows))
        price_max = float(np.max(highs))
        if price_max <= price_min:
            return {
                "poc": None, "vah": None, "val": None,
                "hvn": [], "lvn": [],
                "price_vs_poc": "NEUTRAL",
            }

        bin_edges = np.linspace(price_min, price_max, self.num_bins + 1)
        bin_volumes = np.zeros(self.num_bins)

        for i in range(len(df)):
            candle_low = lows[i]
            candle_high = highs[i]
            vol = volumes[i]
            for j in range(self.num_bins):
                b_low = bin_edges[j]
                b_high = bin_edges[j + 1]
                overlap = max(0, min(candle_high, b_high) - max(candle_low, b_low))
                candle_range = candle_high - candle_low
                if candle_range > 0 and overlap > 0:
                    bin_volumes[j] += vol * (overlap / candle_range)

        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

        poc_idx = int(np.argmax(bin_volumes))
        poc = float(bin_centers[poc_idx])

        total_vol = float(np.sum(bin_volumes))
        target_vol = total_vol * self.value_area_pct

        sorted_indices = np.argsort(bin_volumes)[::-1]
        cumulative = 0.0
        va_indices = []
        for idx in sorted_indices:
            cumulative += bin_volumes[idx]
            va_indices.append(idx)
            if cumulative >= target_vol:
                break

        va_indices_sorted = sorted(va_indices)
        val_price = float(bin_centers[va_indices_sorted[0]])
        vah_price = float(bin_centers[va_indices_sorted[-1]])

        avg_vol = total_vol / self.num_bins if self.num_bins > 0 else 1.0
        hvn = [round(float(bin_centers[i]), 8)
               for i in range(self.num_bins) if bin_volumes[i] > avg_vol * 1.5]
        lvn = [round(float(bin_centers[i]), 8)
               for i in range(self.num_bins) if 0 < bin_volumes[i] < avg_vol * 0.5]

        cur_price = float(closes[-1])
        if cur_price > poc * 1.01:
            price_vs_poc = "ABOVE"
        elif cur_price < poc * 0.99:
            price_vs_poc = "BELOW"
        else:
            price_vs_poc = "AT_POC"

        return {
            "poc": round(poc, 8),
            "vah": round(vah_price, 8),
            "val": round(val_price, 8),
            "hvn": hvn[:5],
            "lvn": lvn[:5],
            "price_vs_poc": price_vs_poc,
        }
