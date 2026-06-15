#!/usr/bin/env python3
"""Отчёт по результатам сигналов — показывает win rate, P&L, лучшие активы."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.config import Settings
from src.storage.database import Database


def _bar(win_rate: float, width: int = 20) -> str:
    filled = round(win_rate / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _pnl_str(pnl: float | None) -> str:
    if pnl is None:
        return "N/A"
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.2f}$"


def print_section(title: str, data: dict) -> None:
    print(f"\n  {title}")
    print("  " + "─" * 50)
    for key, stats in data.items():
        wr = stats["win_rate"]
        bar = _bar(wr)
        pnl = _pnl_str(stats.get("pnl_usd"))
        print(
            f"  {key:<12} {bar} {wr:5.1f}%  "
            f"{stats['wins']}W/{stats['losses']}L  PnL {pnl}"
        )


def main() -> None:
    db = Database(Settings().database_url)
    stats = db.get_trade_analytics()

    if stats.get("total", 0) == 0:
        print("Нет закрытых сделок в базе данных.")
        print("Запусти track_signals.py рядом с run_real.py — он запишет результаты.")
        return

    total = stats["total"]
    wins = stats["wins"]
    losses = stats["losses"]
    wr = stats["win_rate"]
    pnl = stats.get("total_pnl_usd")
    avg_rr = stats.get("avg_rr_on_wins")

    print("\n" + "=" * 54)
    print("  АНАЛИТИКА СИГНАЛОВ")
    print("=" * 54)
    print(f"  Всего сделок:  {total}")
    print(f"  Побед:         {wins}")
    print(f"  Потерь:        {losses}")
    print(f"  Win Rate:      {_bar(wr)} {wr:.1f}%")
    if pnl is not None:
        print(f"  Итого P&L:     {_pnl_str(pnl)}  (тест $10 риск/сделку)")
    if avg_rr is not None:
        print(f"  Средний R/R:   {avg_rr:.2f}R  (на победах)")

    print_section("По активу", stats.get("by_asset", {}))
    print_section("По таймфрейму", stats.get("by_timeframe", {}))
    print_section("По силе сигнала", stats.get("by_strength", {}))
    print_section("По уверенности (confidence)", stats.get("by_confidence", {}))
    print()


if __name__ == "__main__":
    main()
