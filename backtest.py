#!/usr/bin/env python3
"""
Бэктест генератора сигналов по историческим свечам Binance.

Walk-forward: на каждой свече генерируем сигнал по окну истории (как в live),
затем симулируем исход по последующим свечам: SL / TP1 / таймаут.

Отвечает на вопросы:
  - Коррелирует ли confidence с win rate? (калибровка)
  - Какие таймфреймы/типы/режимы дают положительное ожидание?
  - Правильный ли порог min_confidence?

Запуск:
  python3 backtest.py                          # дефолт: активы из config, 1d
  python3 backtest.py --assets ETH,SOL --timeframes 1d,3d
  python3 backtest.py --step 2 --max-hold 30   # быстрее/короче удержание

Результат: таблицы в stdout + data/backtest_results.json
"""

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.analytics.signals.generator import SignalGenerator
from src.utils.config import load_config

logging.basicConfig(level=logging.ERROR)  # глушим warnings генератора
logger = logging.getLogger("backtest")
logger.setLevel(logging.INFO)

RESULTS_PATH = Path("data/backtest_results.json")


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

def fetch_history(asset: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    """Тянет до `limit` свечей с Binance через ccxt (с пагинацией)."""
    import ccxt
    exchange = ccxt.binance({"enableRateLimit": True})
    symbol = asset if "/" in asset else f"{asset}/USDT"

    all_rows: List[list] = []
    since = None
    while len(all_rows) < limit:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=min(1000, limit - len(all_rows) + 1))
        if not batch:
            break
        if all_rows and batch[0][0] <= all_rows[-1][0]:
            batch = [r for r in batch if r[0] > all_rows[-1][0]]
            if not batch:
                break
        all_rows.extend(batch)
        if since is None:
            # первый запрос вернул самые свежие — начинаем пагинацию с конца истории назад нельзя,
            # поэтому: если хотим больше 1000, перезапрашиваем от старта
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
# Симуляция одного сигнала
# ---------------------------------------------------------------------------

def simulate_trade(
    df: pd.DataFrame,
    start_idx: int,
    signal: Dict[str, Any],
    max_hold: int,
    tp_r: Optional[float] = None,
    fee_pct: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """
    Симулирует сделку от свечи start_idx+1 до SL/TP/таймаута.

    tp_r    — если задан, TP переопределяется как entry ± tp_r × risk (свип дистанции TP)
    fee_pct — комиссия на сторону в % (0.1 = Binance spot); вычитается из результата в R

    Консервативно: если SL и TP задеты одной свечой — считаем SL (worst case).
    Возвращает dict с результатом или None (нет TP/SL в сигнале).
    """
    side = signal["signal_type"]
    entry = float(signal.get("entry_price") or 0)
    sl = float(signal.get("stop_loss") or 0)
    tp_list = signal.get("take_profit") or []
    tp1 = float(tp_list[0]["level"]) if tp_list and isinstance(tp_list[0], dict) else None
    if not entry or not sl or not tp1:
        return None

    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if tp_r is not None:
        tp1 = entry + tp_r * risk if side == "BUY" else entry - tp_r * risk
    reward = abs(tp1 - entry)
    # Комиссия за вход+выход в единицах R
    fee_r = (2 * fee_pct / 100) * entry / risk

    end_idx = min(start_idx + max_hold, len(df) - 1)
    for i in range(start_idx + 1, end_idx + 1):
        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])
        if side == "BUY":
            hit_sl = low <= sl
            hit_tp = high >= tp1
        else:  # SELL — симулируем как шорт для оценки качества сигнала
            hit_sl = high >= sl
            hit_tp = low <= tp1
        if hit_sl:  # SL первым (консервативно, даже если оба в одной свече)
            return {"outcome": "SL", "r": round(-1.0 - fee_r, 3), "bars": i - start_idx}
        if hit_tp:
            return {"outcome": "TP", "r": round(reward / risk - fee_r, 3), "bars": i - start_idx}

    # Таймаут — выход по close
    close = float(df.iloc[end_idx]["close"])
    r = (close - entry) / risk if side == "BUY" else (entry - close) / risk
    return {"outcome": "TIMEOUT", "r": round(r - fee_r, 3), "bars": end_idx - start_idx}


# ---------------------------------------------------------------------------
# Walk-forward по одному активу/ТФ
# ---------------------------------------------------------------------------

def backtest_asset(
    asset: str,
    timeframe: str,
    limit: int,
    window: int,
    warmup: int,
    step: int,
    max_hold: int,
    min_confidence: float,
    tp_sweep: Optional[List[float]] = None,
    fee_pct: float = 0.0,
) -> List[Dict[str, Any]]:
    df = fetch_history(asset, timeframe, limit)
    if df is None or len(df) < warmup + 10:
        logger.info("[%s/%s] мало данных (%s свечей), пропуск", asset, timeframe, 0 if df is None else len(df))
        return []

    # Тренд старшего ТФ (EMA21/50) — размечаем каждую сделку: по тренду или против
    HTF_MAP = {"1h": "4h", "4h": "1d", "1d": "1w", "3d": "1w"}
    htf_df = None
    htf_tf = HTF_MAP.get(timeframe)
    if htf_tf:
        htf_df = fetch_history(asset, htf_tf, 600)
        if htf_df is not None and len(htf_df) >= 60:
            htf_df["ema21"] = htf_df["close"].ewm(span=21, adjust=False).mean()
            htf_df["ema50"] = htf_df["close"].ewm(span=50, adjust=False).mean()
        else:
            htf_df = None

    def _htf_trend(ts) -> str:
        if htf_df is None:
            return "NA"
        # Последняя ЗАКРЫТАЯ свеча старшего ТФ до ts (без заглядывания в будущее)
        idx = int(htf_df["timestamp"].searchsorted(ts, side="right")) - 2
        if idx < 50:
            return "NA"
        row = htf_df.iloc[idx]
        if row["ema21"] > row["ema50"] and row["close"] > row["ema50"]:
            return "BULL"
        if row["ema21"] < row["ema50"] and row["close"] < row["ema50"]:
            return "BEAR"
        return "MIX"

    generator = SignalGenerator(min_confidence=min_confidence)
    trades: List[Dict[str, Any]] = []
    open_until = -1  # индекс, до которого позиция «занята» (одна позиция на актив/ТФ)

    for i in range(warmup, len(df) - 1, step):
        if i <= open_until:
            continue
        window_df = df.iloc[max(0, i - window + 1): i + 1].reset_index(drop=True)
        try:
            signal = generator.generate_signal(asset, timeframe, window_df)
        except Exception:
            continue
        if not signal:
            continue

        result = simulate_trade(df, i, signal, max_hold, fee_pct=fee_pct)
        if not result:
            continue
        open_until = i + result["bars"]

        base = {
            "asset": asset,
            "timeframe": timeframe,
            "signal_type": signal["signal_type"],
            "strength": signal.get("strength"),
            "confidence": round(float(signal.get("confidence") or 0), 3),
            "mode": signal.get("signal_label") or signal.get("mode") or signal.get("entry_mode") or "score",
            "date": str(df.iloc[i]["timestamp"].date()),
            "entry": float(signal["entry_price"]),
            "sl": float(signal["stop_loss"]),
            "tp1": float(signal["take_profit"][0]["level"]),
            "htf": _htf_trend(df.iloc[i]["timestamp"]),
        }
        trades.append({**base, "tp_r": "signal", **result})

        # Свип дистанции TP: тот же сигнал, TP = entry ± k×risk
        for k in (tp_sweep or []):
            variant = simulate_trade(df, i, signal, max_hold, tp_r=k, fee_pct=fee_pct)
            if variant:
                trades.append({**base, "tp_r": k, **variant})

    logger.info("[%s/%s] %d сделок", asset, timeframe, len(trades))
    return trades


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0}
    wins = [t for t in trades if t["r"] > 0]
    total_r = sum(t["r"] for t in trades)
    gross_win = sum(t["r"] for t in trades if t["r"] > 0)
    gross_loss = abs(sum(t["r"] for t in trades if t["r"] < 0))
    return {
        "n": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "avg_r": round(total_r / n, 3),
        "total_r": round(total_r, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
    }


def _print_table(title: str, groups: Dict[str, List[Dict[str, Any]]]) -> None:
    print(f"\n=== {title} ===")
    print(f"{'группа':<22}{'n':>6}{'win%':>8}{'avg R':>9}{'total R':>10}{'PF':>7}")
    for key in sorted(groups):
        s = _stats(groups[key])
        if s["n"] == 0:
            continue
        print(f"{key:<22}{s['n']:>6}{s['win_rate']:>8}{s['avg_r']:>9}{s['total_r']:>10}{s['profit_factor']:>7}")


def _conf_bucket(c: float) -> str:
    if c < 0.70:
        return "0.60-0.70 (ниже порога)"
    if c < 0.75:
        return "0.70-0.75"
    if c < 0.80:
        return "0.75-0.80"
    if c < 0.85:
        return "0.80-0.85"
    return "0.85+ (STRONG)"


def report(all_rows: List[Dict[str, Any]]) -> None:
    if not all_rows:
        print("Сделок нет — нечего анализировать.")
        return

    # Свип по дистанции TP — главная таблица «какой win rate покупается какой ценой»
    sweep_rows = [t for t in all_rows if t.get("tp_r") != "signal"]
    if sweep_rows:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for t in all_rows:
            key = f"TP={t['tp_r']}R" if t.get("tp_r") != "signal" else "TP из сигнала"
            groups.setdefault(key, []).append(t)
        _print_table("СВИП TP: win rate ↔ прибыль (с комиссией)", groups)

    trades = [t for t in all_rows if t.get("tp_r") == "signal"] or all_rows

    print(f"\n{'='*60}\nБЭКТЕСТ: {len(trades)} сделок (TP из сигнала)")
    s = _stats(trades)
    print(f"Win rate: {s['win_rate']}%  |  avg R: {s['avg_r']}  |  total R: {s['total_r']}  |  PF: {s['profit_factor']}")

    def group_by(key_fn):
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            groups.setdefault(key_fn(t), []).append(t)
        return groups

    _print_table("По confidence (главный вопрос: калибровка)", group_by(lambda t: _conf_bucket(t["confidence"])))
    _print_table("По типу сигнала", group_by(lambda t: t["signal_type"]))
    _print_table("По режиму входа", group_by(lambda t: t.get("mode") or "score"))
    _print_table("Режим × тренд старшего ТФ", group_by(lambda t: f"{t.get('mode') or 'score'}+{t.get('htf','?')}"))
    _print_table("Тренд старшего ТФ × тип (ключ к отбору)", group_by(lambda t: f"{t['signal_type']}+{t.get('htf','?')}"))
    _print_table("По strength", group_by(lambda t: str(t["strength"])))
    _print_table("По таймфрейму", group_by(lambda t: t["timeframe"]))
    _print_table("По исходу", group_by(lambda t: t["outcome"]))
    _print_table("По активу", group_by(lambda t: t["asset"]))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Бэктест генератора сигналов")
    parser.add_argument("--assets", type=str, default="", help="ETH,SOL,... (default: из config, без кросс-пар)")
    parser.add_argument("--timeframes", type=str, default="1d", help="1d,3d,4h,...")
    parser.add_argument("--limit", type=int, default=1000, help="Свечей истории на актив")
    parser.add_argument("--window", type=int, default=300, help="Окно генератора (как в live)")
    parser.add_argument("--warmup", type=int, default=150, help="Свечей до первого сигнала")
    parser.add_argument("--step", type=int, default=1, help="Шаг walk-forward (2 = каждая вторая свеча)")
    parser.add_argument("--max-hold", type=int, default=40, help="Максимум свечей удержания")
    parser.add_argument("--min-confidence", type=float, default=0.6,
                        help="Порог генератора (0.6 — чтобы увидеть и зону ниже live-порога 0.7)")
    parser.add_argument("--tp-sweep", type=str, default="",
                        help="Свип дистанции TP в R, напр. '0.7,1.0,1.5,2.0'")
    parser.add_argument("--fee-pct", type=float, default=0.0, help="Комиссия на сторону, % (Binance spot = 0.1)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=str, default=str(RESULTS_PATH))
    args = parser.parse_args()

    if args.assets:
        assets = [a.strip() for a in args.assets.split(",") if a.strip()]
    else:
        cfg = load_config()
        assets = [a for a in cfg.get("assets", []) if "/" not in a]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    jobs = [(a, tf) for a in assets for tf in timeframes]
    logger.info("Бэктест: %d активов × %s | %d свечей, окно %d, шаг %d",
                len(assets), timeframes, args.limit, args.window, args.step)

    t0 = time.time()
    all_trades: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                backtest_asset, a, tf,
                args.limit, args.window, args.warmup, args.step, args.max_hold, args.min_confidence,
                [float(x) for x in args.tp_sweep.split(",") if x.strip()] or None,
                args.fee_pct,
            ): (a, tf)
            for a, tf in jobs
        }
        for fut in as_completed(futures):
            a, tf = futures[fut]
            try:
                all_trades.extend(fut.result())
            except Exception as exc:
                logger.error("[%s/%s] ошибка: %s", a, tf, exc)

    logger.info("Готово за %.1f мин", (time.time() - t0) / 60)

    report(all_trades)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(all_trades, ensure_ascii=False, indent=1, default=str))
    print(f"\nДетали: {out} ({len(all_trades)} сделок)")


if __name__ == "__main__":
    main()
