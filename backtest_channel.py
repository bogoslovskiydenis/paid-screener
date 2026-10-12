#!/usr/bin/env python3
"""
Бэктест канальной стратегии (Channel Range Trading).

Логика:
  1. По скользящему окну свечей находим канал: поддержка + сопротивление.
  2. Если цена опускается в зону поддержки и есть подтверждение
     (бычья свеча + RSI < rsi_max) — генерируем BUY.
  3. TP = вход * (1 + tp_pct), SL = вход * (1 - sl_pct).
  4. Симулируем исход по следующим свечам.

Запуск:
  python3 backtest_channel.py                         # AAVE 1h, 90 дней
  python3 backtest_channel.py --timeframe 15m         # 15m таймфрейм
  python3 backtest_channel.py --timeframe 15m 1h      # оба, сравнение
  python3 backtest_channel.py --tp-pct 0.025 --sl-pct 0.015
  python3 backtest_channel.py --deposit 1500
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.analytics.levels.support_resistance import SupportResistanceAnalyzer
from src.analytics.indicators.rsi import RSICalculator

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("channel_bt")

RESULTS_PATH = Path("data/backtest_channel_results.json")


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

def fetch_history(asset: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    import ccxt
    exchange = ccxt.binance({"enableRateLimit": True})
    symbol = asset if "/" in asset else f"{asset}/USDT"

    all_rows: List[list] = []
    since = None
    while len(all_rows) < limit:
        batch = exchange.fetch_ohlcv(
            symbol, timeframe, since=since,
            limit=min(1000, limit - len(all_rows) + 1)
        )
        if not batch:
            break
        if all_rows and batch[0][0] <= all_rows[-1][0]:
            batch = [r for r in batch if r[0] > all_rows[-1][0]]
            if not batch:
                break
        all_rows.extend(batch)
        if since is None:
            if len(all_rows) >= limit or len(batch) < 1000:
                break
            tf_ms = exchange.parse_timeframe(timeframe) * 1000
            since = batch[0][0] - (limit - len(all_rows)) * tf_ms
            all_rows = []
            continue
        since = batch[-1][0] + 1

    if not all_rows:
        return None
    df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df.tail(limit).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Детектор канала
# ---------------------------------------------------------------------------

_sr_analyzer = SupportResistanceAnalyzer(min_touches=2, price_tolerance=0.008)


def detect_channel(
    window_df: pd.DataFrame,
    min_width: float,
    max_width: float,
) -> Optional[Tuple[float, float]]:
    """
    Возвращает (support, resistance) если найден настоящий боковой канал.
    Требует: сопротивление протестировано в последней половине окна —
    иначе это нисходящий тренд, а не канал.
    """
    levels = _sr_analyzer.find_levels(window_df)
    supports = levels.get("support_levels", [])
    resistances = levels.get("resistance_levels", [])
    if not supports or not resistances:
        return None

    current_price = float(window_df.iloc[-1]["close"])
    half = len(window_df) // 2

    best_support = None
    for lvl in sorted(supports, key=lambda x: x["strength"], reverse=True):
        if lvl["price"] < current_price * 0.999:
            best_support = lvl["price"]
            break
    if best_support is None:
        return None

    best_resistance = None
    for lvl in sorted(resistances, key=lambda x: x["strength"], reverse=True):
        if lvl["price"] > best_support * 1.001:
            best_resistance = lvl["price"]
            break
    if best_resistance is None:
        return None

    width = (best_resistance - best_support) / best_support
    if not (min_width <= width <= max_width):
        return None

    # Сопротивление тестировалось в последней половине окна
    recent_highs = window_df.iloc[half:]["high"].values
    if not any(h >= best_resistance * 0.995 for h in recent_highs):
        return None

    # Фильтр тренда: линейная регрессия цены за последние 20 свечей.
    # Если наклон > 0.3% за свечу — это тренд, а не боковик — пропускаем.
    last20 = window_df["close"].iloc[-20:].values
    x = range(len(last20))
    slope = float(pd.Series(last20).diff().mean()) / float(last20[0])
    if abs(slope) > 0.003:
        return None

    return (best_support, best_resistance)


# ---------------------------------------------------------------------------
# Проверка сигнала
# ---------------------------------------------------------------------------

_rsi_calc = RSICalculator(period=14)


def check_buy_signal(
    window_df: pd.DataFrame,
    support: float,
    resistance: float,
    zone_pct: float,
    rsi_max: float,
) -> bool:
    """
    BUY если:
    - цена в зоне поддержки (close между support*(1-zone*0.5) и support*(1+zone))
    - цена ниже середины канала
    - последняя свеча бычья (close > open)
    - RSI < rsi_max
    """
    current = window_df.iloc[-1]
    close = float(current["close"])
    open_ = float(current["open"])
    mid = (support + resistance) / 2

    if not (support * (1 - zone_pct * 0.5) <= close <= support * (1 + zone_pct)):
        return False
    if close >= mid:
        return False
    if close <= open_:
        return False

    rsi_data = _rsi_calc.analyze(window_df)
    rsi_val = rsi_data.get("rsi") if rsi_data else None
    if rsi_val is None or rsi_val >= rsi_max:
        return False

    return True


# ---------------------------------------------------------------------------
# Симуляция сделки
# ---------------------------------------------------------------------------

def simulate_trade(
    df: pd.DataFrame,
    entry_idx: int,
    entry: float,
    tp: float,
    sl: float,
    max_hold: int,
    fee_pct: float,
) -> Dict[str, Any]:
    """SL и TP в одной свече — считаем SL (консервативно)."""
    end_idx = min(entry_idx + max_hold, len(df) - 1)
    for i in range(entry_idx + 1, end_idx + 1):
        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])
        if low <= sl:
            pnl = round((sl - entry) / entry * 100 - 2 * fee_pct, 3)
            return {"outcome": "SL", "bars": i - entry_idx, "pnl_pct": pnl}
        if high >= tp:
            pnl = round((tp - entry) / entry * 100 - 2 * fee_pct, 3)
            return {"outcome": "TP", "bars": i - entry_idx, "pnl_pct": pnl}

    close = float(df.iloc[end_idx]["close"])
    pnl = round((close - entry) / entry * 100 - 2 * fee_pct, 3)
    return {"outcome": "TIMEOUT", "bars": end_idx - entry_idx, "pnl_pct": pnl}


# ---------------------------------------------------------------------------
# Walk-forward бэктест
# ---------------------------------------------------------------------------

def get_htf_trend(htf_df: Optional[pd.DataFrame], ts: pd.Timestamp) -> str:
    """Тренд на 4h по EMA21/EMA50 на момент ts. BULL / BEAR / NEUTRAL."""
    if htf_df is None:
        return "NEUTRAL"
    idx = int(htf_df["timestamp"].searchsorted(ts, side="right")) - 2
    if idx < 50:
        return "NEUTRAL"
    row = htf_df.iloc[idx]
    if row["ema21"] > row["ema50"] and row["close"] > row["ema50"]:
        return "BULL"
    if row["ema21"] < row["ema50"] and row["close"] < row["ema50"]:
        return "BEAR"
    return "NEUTRAL"


def run_backtest(
    asset: str,
    timeframe: str,
    limit: int,
    window: int,
    warmup: int,
    zone_pct: float,
    tp_pct: float,
    sl_pct: float,
    rsi_max: float,
    min_channel_width: float,
    max_channel_width: float,
    max_hold: int,
    fee_pct: float,
    htf_filter: bool = True,
) -> List[Dict[str, Any]]:
    logger.info("Загружаю %s/%s (%d свечей)...", asset, timeframe, limit)
    df = fetch_history(asset, timeframe, limit)
    if df is None or len(df) < warmup + 20:
        logger.error("Недостаточно данных для %s/%s", asset, timeframe)
        return []
    logger.info("Получено %d свечей. Walk-forward...", len(df))

    # Загружаем 4h для фильтра тренда
    htf_df = None
    if htf_filter and timeframe in ("15m", "1h"):
        logger.info("Загружаю 4h для фильтра тренда...")
        htf_df = fetch_history(asset, "4h", 600)
        if htf_df is not None and len(htf_df) >= 60:
            htf_df["ema21"] = htf_df["close"].ewm(span=21, adjust=False).mean()
            htf_df["ema50"] = htf_df["close"].ewm(span=50, adjust=False).mean()
        else:
            htf_df = None

    trades: List[Dict[str, Any]] = []
    open_until = -1

    cnt_total = 0
    cnt_channel = 0
    cnt_zone = 0
    cnt_bullish = 0
    cnt_rsi = 0
    cnt_mid = 0

    for i in range(warmup, len(df) - 1):
        if i <= open_until:
            continue

        cnt_total += 1
        window_df = df.iloc[max(0, i - window + 1): i + 1].reset_index(drop=True)
        channel = detect_channel(window_df, min_channel_width, max_channel_width)
        if channel is None:
            continue
        cnt_channel += 1

        support, resistance = channel
        close = float(window_df.iloc[-1]["close"])
        open_ = float(window_df.iloc[-1]["open"])
        mid = (support + resistance) / 2

        in_zone = support * (1 - zone_pct * 0.5) <= close <= support * (1 + zone_pct)
        if in_zone:
            cnt_zone += 1
        below_mid = close < mid
        if in_zone and below_mid:
            cnt_mid += 1
        bullish = close > open_
        if in_zone and below_mid and bullish:
            cnt_bullish += 1

        rsi_data = _rsi_calc.analyze(window_df)
        rsi_val = rsi_data.get("rsi") if rsi_data else None
        if in_zone and below_mid and bullish and rsi_val is not None and rsi_val < rsi_max:
            cnt_rsi += 1

        if not check_buy_signal(window_df, support, resistance, zone_pct, rsi_max):
            continue

        # Фильтр тренда: не входим в BUY если 4h показывает BEAR
        if htf_filter and htf_df is not None:
            trend = get_htf_trend(htf_df, df.iloc[i]["timestamp"])
            if trend == "BEAR":
                continue

        entry = float(df.iloc[i]["close"])
        tp = entry * (1 + tp_pct)
        sl = entry * (1 - sl_pct)

        result = simulate_trade(df, i, entry, tp, sl, max_hold, fee_pct)
        open_until = i + result["bars"]

        trades.append({
            "timeframe": timeframe,
            "date": str(df.iloc[i]["timestamp"]),
            "entry": round(entry, 4),
            "tp": round(tp, 4),
            "sl": round(sl, 4),
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "channel_width_pct": round((resistance - support) / support * 100, 2),
            "outcome": result["outcome"],
            "bars": result["bars"],
            "pnl_pct": result["pnl_pct"],
            "htf_trend": get_htf_trend(htf_df, df.iloc[i]["timestamp"]) if htf_df is not None else "NA",
        })

    logger.info("[%s/%s] найдено %d сделок", asset, timeframe, len(trades))
    logger.info("  DEBUG: проверено=%d  канал найден=%d  в зоне=%d  ниже середины=%d  бычья свеча=%d  RSI<%.0f=%d",
                cnt_total, cnt_channel, cnt_zone, cnt_mid, cnt_bullish, rsi_max, cnt_rsi)
    return trades


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t["outcome"] == "TP"]
    gross_win = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    total_pnl = sum(t["pnl_pct"] for t in trades)
    return {
        "n": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "avg_pnl": round(total_pnl / n, 3),
        "total_pnl": round(total_pnl, 2),
        "pf": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
    }


def report(all_trades: List[Dict[str, Any]], deposit: float, tp_pct: float, sl_pct: float) -> None:
    if not all_trades:
        print("Сделок нет — попробуй увеличить --zone-pct или --rsi-max")
        return

    timeframes = sorted(set(t["timeframe"] for t in all_trades))

    for tf in timeframes:
        trades = [t for t in all_trades if t["timeframe"] == tf]
        s = _stats(trades)
        if s["n"] == 0:
            continue

        wins = [t for t in trades if t["outcome"] == "TP"]
        losses = [t for t in trades if t["outcome"] == "SL"]
        timeouts = [t for t in trades if t["outcome"] == "TIMEOUT"]
        avg_bars = sum(t["bars"] for t in trades) / len(trades)
        pnl_usd = deposit * s["total_pnl"] / 100
        avg_usd = deposit * s["avg_pnl"] / 100

        print("\n" + "=" * 62)
        print(f"  КАНАЛ [{tf}]  TP +{tp_pct*100:.1f}%  SL -{sl_pct*100:.1f}%  депозит ${deposit:.0f}")
        print("=" * 62)
        print(f"  Сделок:        {s['n']}")
        print(f"  TP (победы):   {len(wins)}  ({s['win_rate']:.1f}%)")
        print(f"  SL (потери):   {len(losses)}  ({100*len(losses)/s['n']:.1f}%)")
        print(f"  TIMEOUT:       {len(timeouts)}  ({100*len(timeouts)/s['n']:.1f}%)")
        print(f"  Win rate:      {s['win_rate']:.1f}%")
        print(f"  Profit factor: {s['pf']}")
        print(f"  Avg удержание: {avg_bars:.1f} свечей")
        print(f"  Avg профит:    {s['avg_pnl']:+.2f}%  (${avg_usd:+.1f} за сделку)")
        print(f"  Итог 90 дней:  {s['total_pnl']:+.1f}%  (${pnl_usd:+.0f})")

        if timeouts:
            avg_to = sum(t["pnl_pct"] for t in timeouts) / len(timeouts)
            hint = "TP слишком близко?" if avg_to > 0 else "SL слишком далеко?"
            print(f"  TIMEOUT avg:   {avg_to:+.2f}% ({hint})")

        print(f"\n  --- Последние 10 сделок [{tf}] ---")
        print(f"  {'Дата':<18}{'Вход':>8}{'Канал':>16}{'Ш%':>5}{'Бары':>6}{'Исход':>9}{'PnL%':>8}")
        for t in trades[-10:]:
            date = t["date"][:16]
            ch = f"{t['support']:.1f}-{t['resistance']:.1f}"
            print(f"  {date:<18}{t['entry']:>8.2f}{ch:>16}{t['channel_width_pct']:>5.1f}"
                  f"{t['bars']:>6}{t['outcome']:>9}{t['pnl_pct']:>+8.2f}%")

    if len(timeframes) > 1:
        print("\n" + "=" * 62)
        print("  СРАВНЕНИЕ ТАЙМФРЕЙМОВ")
        print("=" * 62)
        print(f"  {'ТФ':<8}{'Сделок':>8}{'Win%':>8}{'Avg%':>10}{'PF':>8}{'Итог $':>10}")
        for tf in timeframes:
            trades = [t for t in all_trades if t["timeframe"] == tf]
            s = _stats(trades)
            if s["n"] == 0:
                continue
            usd = deposit * s["total_pnl"] / 100
            print(f"  {tf:<8}{s['n']:>8}{s['win_rate']:>8.1f}{s['avg_pnl']:>+10.3f}{s['pf']:>8}{usd:>+10.0f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Бэктест канальной стратегии")
    parser.add_argument("--asset",     default="AAVE",   help="Тикер (default: AAVE)")
    parser.add_argument("--timeframe", nargs="+",        default=["1h"],
                        help="Таймфреймы: 15m 1h (default: 1h)")
    parser.add_argument("--limit",     type=int,  default=0,
                        help="Кол-во свечей (0=авто: 90 дней для выбранного ТФ)")
    parser.add_argument("--window",    type=int,  default=60,    help="Свечей для детекции канала (default: 60)")
    parser.add_argument("--warmup",    type=int,  default=60,    help="Свечей разогрева (default: 60)")
    parser.add_argument("--zone-pct",  type=float, default=0.012, help="Зона у поддержки 0.012=1.2 проц")
    parser.add_argument("--tp-pct",    type=float, default=0.018, help="TP от входа 0.018=1.8 проц")
    parser.add_argument("--sl-pct",    type=float, default=0.015, help="SL от входа 0.015=1.5 проц")
    parser.add_argument("--rsi-max",   type=float, default=48.0,  help="Макс RSI для входа (default: 48)")
    parser.add_argument("--min-ch",    type=float, default=0.025, help="Мин ширина канала 0.025=2.5 проц")
    parser.add_argument("--max-ch",    type=float, default=0.12,  help="Макс ширина канала 0.12=12 проц")
    parser.add_argument("--max-hold",  type=int,  default=48,    help="Макс свечей удержания (default: 48)")
    parser.add_argument("--fee-pct",     type=float, default=0.1,   help="Комиссия Binance spot (default: 0.1)")
    parser.add_argument("--deposit",     type=float, default=1500.0, help="Депозит в USD (default: 1500)")
    parser.add_argument("--no-htf",      action="store_true",        help="Отключить фильтр тренда по 4h")
    parser.add_argument("--output",      default=str(RESULTS_PATH))
    args = parser.parse_args()

    LIMIT_MAP = {"15m": 8640, "1h": 2160, "4h": 540, "1d": 90}

    all_trades: List[Dict[str, Any]] = []
    for tf in args.timeframe:
        limit = args.limit if args.limit > 0 else LIMIT_MAP.get(tf, 2160)
        trades = run_backtest(
            asset=args.asset,
            timeframe=tf,
            limit=limit,
            window=args.window,
            warmup=args.warmup,
            zone_pct=args.zone_pct,
            tp_pct=args.tp_pct,
            sl_pct=args.sl_pct,
            rsi_max=args.rsi_max,
            min_channel_width=args.min_ch,
            max_channel_width=args.max_ch,
            max_hold=args.max_hold,
            fee_pct=args.fee_pct,
            htf_filter=not args.no_htf,
        )
        all_trades.extend(trades)

    report(all_trades, args.deposit, args.tp_pct, args.sl_pct)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_trades, ensure_ascii=False, indent=1, default=str))
    print(f"\nДетали: {out} ({len(all_trades)} сделок)")


if __name__ == "__main__":
    main()
