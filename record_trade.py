#!/usr/bin/env python3
"""Ручная запись сделки в журнал (та же БД, что и у трекера)."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.storage.database import Database  # noqa: E402
from src.utils.config import Settings  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Добавить сделку в журнал")
    p.add_argument("--asset", required=True)
    p.add_argument("--timeframe", default="manual")
    p.add_argument("--signal-type", required=True, choices=["BUY", "SELL"])
    p.add_argument("--entry", type=float, required=True)
    p.add_argument("--exit-price", type=float, required=True)
    p.add_argument("--result", required=True, choices=["TP", "SL", "TSL"])
    p.add_argument("--strength", default="")
    p.add_argument("--confidence", type=float, default=None)
    p.add_argument("--risk-usd", type=float, default=None)
    p.add_argument("--qty", type=float, default=None)
    p.add_argument("--notes", default="")
    p.add_argument("--opened-at", default="", help="ISO8601, опционально")
    args = p.parse_args()

    side = args.signal_type.upper()
    pnl_usd = None
    pnl_rr = None
    if args.qty is not None and args.qty > 0:
        if side == "BUY":
            pnl_usd = (args.exit_price - args.entry) * args.qty
        else:
            pnl_usd = (args.entry - args.exit_price) * args.qty
        if args.risk_usd is not None and args.risk_usd > 0:
            pnl_rr = pnl_usd / args.risk_usd

    opened_at = None
    if args.opened_at.strip():
        raw = args.opened_at.strip().replace("Z", "+00:00")
        opened_at = datetime.fromisoformat(raw)

    db = Database(Settings().database_url)
    tid = db.save_trade(
        {
            "asset": args.asset.strip(),
            "timeframe": args.timeframe.strip(),
            "signal_type": side,
            "strength": args.strength.strip() or None,
            "entry_price": args.entry,
            "exit_price": args.exit_price,
            "result": args.result,
            "confidence": args.confidence,
            "risk_usd": args.risk_usd,
            "qty": args.qty,
            "pnl_usd": pnl_usd,
            "pnl_rr": pnl_rr,
            "opened_at": opened_at,
            "closed_at": datetime.utcnow(),
            "source": "manual",
            "notes": args.notes.strip() or None,
            "signal_snapshot": None,
        }
    )
    print(f"Записано id={tid}")


if __name__ == "__main__":
    main()
