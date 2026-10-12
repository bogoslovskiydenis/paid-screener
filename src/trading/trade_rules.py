"""Правила отбора сигналов в торговый портфель и общий бюджет риска.

Решение принимается один раз при добавлении сигнала в трекер и сохраняется
в самом сигнале (in_budget / budget_reason). Трекер продолжает вести ВСЕ
сигналы (полная статистика), но реальные деньги получают только сигналы
в бюджете.

ENV:
  TRADE_MIN_CONFIDENCE      (default 0.75) — порог уверенности для торговли
  TRADE_MIN_RR_TP2          (default 1.5)  — мин. R/R к TP2 (или TP1, если TP2 нет)
  TRADE_MAX_TOTAL_RISK_PCT  (default 3.0)  — общий открытый риск, % депозита
  TRADE_VIRTUAL_DEPOSIT_USD (default 1000) — депозит для расчёта бюджета в paper-режиме
"""
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..utils.logger import setup_logger

logger = setup_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def min_confidence() -> float:
    return _env_float("TRADE_MIN_CONFIDENCE", 0.75)


def min_rr_tp2() -> float:
    return _env_float("TRADE_MIN_RR_TP2", 1.5)


def max_total_risk_pct() -> float:
    return _env_float("TRADE_MAX_TOTAL_RISK_PCT", 3.0)


def virtual_deposit_usd() -> float:
    return _env_float("TRADE_VIRTUAL_DEPOSIT_USD", 1000.0)


def _tp_levels(signal: Dict[str, Any]) -> List[float]:
    out = []
    for tp in signal.get("take_profit") or []:
        if isinstance(tp, dict) and tp.get("level") is not None:
            out.append(float(tp["level"]))
    return out


def rr_to_target(signal: Dict[str, Any]) -> Optional[float]:
    """R/R к TP2 (реальная цель остатка после частичного TP1); без TP2 — к TP1."""
    try:
        entry = float(signal["entry_price"])
        sl = float(signal["stop_loss"])
    except (KeyError, TypeError, ValueError):
        return None
    risk = entry - sl
    if risk <= 0:
        return None
    tps = _tp_levels(signal)
    if not tps:
        return None
    target = tps[1] if len(tps) >= 2 else tps[0]
    return (target - entry) / risk


def check_tradeable(signal: Dict[str, Any]) -> Tuple[bool, str]:
    """Проходит ли сигнал фильтры качества (без учёта бюджета)."""
    if signal.get("signal_type") != "BUY":
        return False, "не BUY — только уведомление"
    conf = float(signal.get("confidence") or 0.0)
    if conf < min_confidence():
        return False, f"confidence {conf:.2f} < {min_confidence():.2f}"
    rr = rr_to_target(signal)
    if rr is None:
        return False, "нет TP/SL для оценки R/R"
    if rr < min_rr_tp2():
        return False, f"R/R к TP2 {rr:.2f} < {min_rr_tp2():.2f}"
    return True, "ok"


def open_risk_usd(open_signals: List[Dict[str, Any]]) -> float:
    """Суммарный риск открытых сигналов портфеля.

    После частичного TP1 стоп остатка не ниже безубытка — позиция
    бюджет не занимает.
    """
    total = 0.0
    for s in open_signals:
        if not s.get("in_budget") or s.get("signal_type") != "BUY":
            continue
        if s.get("tp1_filled"):
            continue
        tt = s.get("test_trade") or {}
        try:
            total += float(tt.get("risk_usd") or 0.0)
        except (TypeError, ValueError):
            continue
    return total


def evaluate_for_budget(
    signal: Dict[str, Any],
    get_open_signals: Callable[[], List[Dict[str, Any]]],
) -> bool:
    """Ставит in_budget/budget_reason на сигнале. Возвращает in_budget."""
    ok, reason = check_tradeable(signal)
    if not ok:
        signal["in_budget"] = False
        signal["budget_reason"] = reason
        return False

    tt = signal.get("test_trade") or {}
    try:
        this_risk = float(tt.get("risk_usd") or 0.0)
    except (TypeError, ValueError):
        this_risk = 0.0
    if this_risk <= 0:
        signal["in_budget"] = False
        signal["budget_reason"] = "нет test_trade.risk_usd"
        return False

    budget = virtual_deposit_usd() * max_total_risk_pct() / 100.0
    try:
        used = open_risk_usd(get_open_signals())
    except Exception as exc:
        logger.warning("Не удалось посчитать открытый риск (%s) — сигнал вне бюджета", exc)
        signal["in_budget"] = False
        signal["budget_reason"] = "ошибка расчёта открытого риска"
        return False

    if used + this_risk > budget + 1e-9:
        signal["in_budget"] = False
        signal["budget_reason"] = f"бюджет занят: {used:.2f}$ + {this_risk:.2f}$ > {budget:.2f}$"
        return False

    signal["in_budget"] = True
    signal["budget_reason"] = f"в портфеле ({used + this_risk:.2f}$ из {budget:.2f}$)"
    return True
