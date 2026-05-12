#!/usr/bin/env python3
"""Сводка и экспорт журнала закрытых сделок (SQLite data/screener.db)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.storage.database import Database  # noqa: E402
from src.utils.config import Settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Анализ записанных сделок")
    ap.add_argument("--asset", default="", help="Фильтр по активу")
    ap.add_argument("--export", default="", help="Путь для экспорта JSON")
    ap.add_argument("--limit", type=int, default=0, help="Макс. строк (0 = все)")
    args = ap.parse_args()

    db = Database(Settings().database_url)
    lim = args.limit if args.limit > 0 else None
    rows = db.get_trades(asset=args.asset.strip() or None, limit=lim)
    if not rows:
        print("Нет записей.")
        return

    settled = [r for r in rows if r.get("result") in ("TP", "SL", "TSL")]
    wins = sum(1 for r in settled if r["result"] in ("TP", "TSL"))
    losses = sum(1 for r in settled if r["result"] == "SL")
    print(f"В выборке: {len(rows)} записей, из них закрытий TP/SL/TSL: {len(settled)}")
    if wins + losses > 0:
        print(f"W/L: {wins}/{losses}, win rate: {100.0 * wins / (wins + losses):.1f}%")

    pnls = [float(r["pnl_usd"]) for r in rows if r.get("pnl_usd") is not None]
    if pnls:
        print(f"Сумма PnL (где указан): {sum(pnls):+.2f} USD")

    by_asset: dict[str, list[str]] = {}
    for r in rows:
        by_asset.setdefault(r["asset"], []).append(str(r.get("result")))

    print("\nПо активам:")
    for a in sorted(by_asset.keys()):
        xs = by_asset[a]
        w = sum(1 for x in xs if x in ("TP", "TSL"))
        ell = sum(1 for x in xs if x == "SL")
        print(f"  {a}: всего {len(xs)}, W/L {w}/{ell}")

    if args.export.strip():
        Path(args.export.strip()).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nЭкспорт: {args.export}")


if __name__ == "__main__":
    main()
