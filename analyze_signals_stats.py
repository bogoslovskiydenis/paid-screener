#!/usr/bin/env python3
"""Быстрая аналитика по сохранённым сигналам BUY/SELL."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from src.storage.database import Database, Signal
from src.utils.config import Settings


@dataclass
class StatBucket:
    count: int = 0
    strong: int = 0
    medium: int = 0
    weak: int = 0
    confidences: List[float] = field(default_factory=list)
    rr_values: List[float] = field(default_factory=list)

    def add(self, strength: str, confidence: float, rr_best: float | None) -> None:
        self.count += 1
        strength_u = (strength or "").upper()
        if strength_u == "STRONG":
            self.strong += 1
        elif strength_u == "MEDIUM":
            self.medium += 1
        else:
            self.weak += 1

        if isinstance(confidence, (int, float)):
            self.confidences.append(float(confidence))
        if isinstance(rr_best, (int, float)) and rr_best > 0:
            self.rr_values.append(float(rr_best))

    def summary(self) -> Dict[str, Any]:
        conf_arr = np.array(self.confidences, dtype=float) if self.confidences else None
        rr_arr = np.array(self.rr_values, dtype=float) if self.rr_values else None

        return {
            "count": self.count,
            "strong": self.strong,
            "medium": self.medium,
            "weak": self.weak,
            "conf_avg": float(conf_arr.mean()) if conf_arr is not None else None,
            "conf_p25": float(np.percentile(conf_arr, 25)) if conf_arr is not None else None,
            "conf_p75": float(np.percentile(conf_arr, 75)) if conf_arr is not None else None,
            "rr_avg": float(rr_arr.mean()) if rr_arr is not None else None,
            "rr_median": float(np.median(rr_arr)) if rr_arr is not None else None,
            "rr_p25": float(np.percentile(rr_arr, 25)) if rr_arr is not None else None,
            "rr_p75": float(np.percentile(rr_arr, 75)) if rr_arr is not None else None,
        }


def _best_rr(signal_obj: Signal) -> float | None:
    """Считает лучший R/R по сохранённым TP для сигнала."""
    tp_list = signal_obj.take_profit or []
    if not isinstance(tp_list, list):
        return None

    entry = float(signal_obj.entry_price)
    sl = float(signal_obj.stop_loss)
    stype = (signal_obj.signal_type or "").upper()

    rr_vals: List[float] = []
    for tp in tp_list:
        if not isinstance(tp, dict):
            continue
        level = tp.get("level")
        if not isinstance(level, (int, float)):
            continue
        level_f = float(level)
        if stype == "BUY":
            reward = level_f - entry
            risk = entry - sl
        else:
            reward = entry - level_f
            risk = sl - entry
        if risk > 0 and reward > 0:
            rr_vals.append(reward / risk)

    if not rr_vals:
        return None
    return max(rr_vals)


def collect_stats(
    db: Database,
    asset: str | None = None,
) -> Dict[Tuple[str, str], StatBucket]:
    session = db.get_session()
    try:
        query = session.query(Signal)
        if asset:
            query = query.filter(Signal.asset == asset)

        buckets: Dict[Tuple[str, str], StatBucket] = defaultdict(StatBucket)

        for sig in query.order_by(Signal.timestamp.asc()):
            key = (sig.timeframe, sig.signal_type.upper())
            bucket = buckets[key]
            rr_best = _best_rr(sig)
            bucket.add(sig.strength, sig.confidence, rr_best)

        return buckets
    finally:
        session.close()


def print_stats(buckets: Dict[Tuple[str, str], StatBucket]) -> None:
    if not buckets:
        print("Нет сохранённых сигналов в БД.")
        return

    print("Аналитика по сигналам (по таймфрейму и направлению):")
    print("-" * 70)

    for (timeframe, stype), bucket in sorted(
        buckets.items(), key=lambda x: (x[0][0], x[0][1])
    ):
        s = bucket.summary()
        print(f"{timeframe} / {stype}:")
        print(
            f"  всего: {s['count']}, STRONG: {s['strong']}, "
            f"MEDIUM: {s['medium']}, WEAK: {s['weak']}"
        )
        print(
            f"  confidence: avg={s['conf_avg']:.3f} "
            f"[p25={s['conf_p25']:.3f}, p75={s['conf_p75']:.3f}]"
            if s["conf_avg"] is not None
            else "  confidence: нет данных"
        )
        if s["rr_avg"] is not None:
            print(
                f"  best R/R по TP: avg={s['rr_avg']:.2f}, median={s['rr_median']:.2f} "
                f"[p25={s['rr_p25']:.2f}, p75={s['rr_p75']:.2f}]"
            )
        else:
            print("  best R/R по TP: нет данных")
        print()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Аналитика по сохранённым сигналам (BUY/SELL)"
    )
    parser.add_argument(
        "--asset",
        type=str,
        default="",
        help="Фильтр по активу (например, ETH). Пусто = все",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = Settings()
    db = Database(settings.database_url)

    asset = args.asset.strip() or None
    buckets = collect_stats(db, asset=asset)
    print_stats(buckets)


if __name__ == "__main__":
    main()

