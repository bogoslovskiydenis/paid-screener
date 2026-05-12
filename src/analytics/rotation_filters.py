"""Фильтры «перелива» между BTC / ETH / SOL по уже посчитанным метрикам в JSON анализа."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Якорный ТФ для структуры перелива (согласован с config: старшие ТФ)
DEFAULT_ANCHOR_TF = "1d"
DEFAULT_MIN_CONFIDENCE = 0.7

CONDITIONS_RU = """
Условия оповещений (якорный таймфрейм по умолчанию — 1d). Это не гарантия прибыли, а согласованный набор фильтров.

Поле confidence: если сценарий активен — уверенность сигнала по паре или 1.0 по структуре;
если не активен — min(уверенность сигнала, доля пройденных структурных проверок до финала), без ложных 100%.

1) Перелив BTC → ETH (пара ETH/BTC и контекст BTC/USDT)
   — На ETH/BTC: бычий контекст (EMA9>EMA21 ИЛИ бычий тренд по EMA50), RSI пары < 72 и не «жаро» (RSI ≥ 70 отсекаем).
   — На BTC/USDT: RSI > 25 на этом ТФ.
   — Если в блоке пары есть сигнал генератора: его confidence должен быть >= 70%.

2) Перелив ETH → SOL (пара SOL/ETH)
   — Аналогично по EMA/RSI на SOL/ETH; при наличии signal.confidence на паре — >= 70%.

3) Перелив BTC → SOL (пара SOL/BTC)
   — Аналогично по EMA/RSI на SOL/BTC; BTC RSI > 25; при наличии signal.confidence на паре — >= 70%.

Если по паре нет объекта signal — учитывается только структурная часть (при полном проходе проверок уверенность считается 100%).
"""


def _blk(results: Dict[str, Any], asset: str, tf: str) -> Optional[Dict[str, Any]]:
    a = results.get(asset)
    if not isinstance(a, dict):
        return None
    b = a.get(tf)
    if not isinstance(b, dict) or b.get("error"):
        return None
    return b


def _rsi(block: Dict[str, Any]) -> Optional[float]:
    r = block.get("rsi")
    if not isinstance(r, dict):
        return None
    v = r.get("rsi")
    return float(v) if isinstance(v, (int, float)) else None


def _rsi_zone(block: Dict[str, Any]) -> str:
    r = block.get("rsi")
    if not isinstance(r, dict):
        return ""
    return str(r.get("rsi_zone") or "")


def _ema(block: Dict[str, Any]) -> Dict[str, Any]:
    e = block.get("ema")
    return e if isinstance(e, dict) else {}


def _check_pair_bullish(block: Optional[Dict[str, Any]], pair_label: str) -> Tuple[bool, List[Dict[str, Any]]]:
    checks: List[Dict[str, Any]] = []
    if not block:
        checks.append({"name": f"данные {pair_label}", "ok": False, "detail": "нет блока на якорном ТФ"})
        return False, checks

    rsi = _rsi(block)
    ema = _ema(block)
    trend = str(ema.get("trend") or "")
    cross = str(ema.get("ema_cross") or "")
    momentum_ok = cross == "BULLISH" or trend == "BULLISH"
    checks.append(
        {
            "name": f"{pair_label} EMA",
            "ok": momentum_ok,
            "detail": f"trend={trend}, ema_cross={cross}",
        }
    )

    hot_rsi = rsi is not None and rsi >= 70.0
    rsi_ok = rsi is None or rsi < 72.0
    checks.append(
        {
            "name": f"{pair_label} RSI",
            "ok": rsi_ok and not hot_rsi,
            "detail": f"rsi={rsi}, zone={_rsi_zone(block)}",
        }
    )

    active = momentum_ok and rsi_ok and not hot_rsi
    return active, checks


def _finalize_scenario(
    structural_active: bool,
    pair_block: Optional[Dict[str, Any]],
    checks: List[Dict[str, Any]],
    min_confidence: float,
) -> Tuple[bool, float, Optional[float], List[Dict[str, Any]]]:
    """Итог: active, confidence (для отчёта), signal_confidence (или None), checks."""
    pre_frac = (
        sum(1 for c in checks if c.get("ok")) / max(len(checks), 1) if checks else 0.0
    )
    pb = pair_block or {}
    sig = pb.get("signal")
    if isinstance(sig, dict) and sig.get("confidence") is not None:
        sig_conf = float(sig["confidence"])
        gate_ok = sig_conf >= min_confidence
        checks.append(
            {
                "name": f"уверенность сигнала по паре >= {min_confidence:.0%}",
                "ok": gate_ok,
                "detail": f"{sig_conf:.1%}",
            }
        )
        active = structural_active and gate_ok
        if active:
            conf_report = sig_conf
        else:
            conf_report = min(sig_conf, pre_frac)
        return active, conf_report, sig_conf, checks

    passed = sum(1 for c in checks if c.get("ok"))
    n = len(checks)
    structural_ratio = (passed / n) if n else 0.0
    inner_conf = 1.0 if structural_active else structural_ratio
    checks.append(
        {
            "name": "сигнал генератора по паре",
            "ok": True,
            "detail": "нет — только структура; при полном проходе confidence=100%",
        }
    )
    checks.append(
        {
            "name": f"итог >= {min_confidence:.0%}",
            "ok": inner_conf >= min_confidence,
            "detail": f"{inner_conf:.1%}",
        }
    )
    active = structural_active and inner_conf >= min_confidence
    return active, inner_conf, None, checks


def _scenario_btc_to_eth(
    results: Dict[str, Any], tf: str, min_confidence: float
) -> Dict[str, Any]:
    eth_btc = _blk(results, "ETH/BTC", tf)
    btc = _blk(results, "BTC", tf)
    checks: List[Dict[str, Any]] = []

    pair_ok, pc = _check_pair_bullish(eth_btc, "ETH/BTC")
    checks.extend(pc)

    btc_ok = True
    if btc:
        br = _rsi(btc)
        if br is not None:
            btc_ok = br > 25.0
            checks.append({"name": "BTC/USDT RSI (не дно паники)", "ok": btc_ok, "detail": f"rsi={br:.1f}"})
    else:
        btc_ok = False
        checks.append({"name": "BTC/USDT", "ok": False, "detail": "нет данных на ТФ"})

    structural = pair_ok and btc_ok
    active, confidence, sig_c, checks = _finalize_scenario(structural, eth_btc, checks, min_confidence)
    return {
        "scenario": "BTC_TO_ETH",
        "label_ru": "Перелив BTC → ETH",
        "anchor_tf": tf,
        "confidence": round(confidence, 4),
        "signal_confidence": None if sig_c is None else round(sig_c, 4),
        "structure_ok": structural,
        "min_confidence": min_confidence,
        "active": active,
        "checks": checks,
    }


def _scenario_eth_to_sol(
    results: Dict[str, Any], tf: str, min_confidence: float
) -> Dict[str, Any]:
    sol_eth = _blk(results, "SOL/ETH", tf)
    structural, checks = _check_pair_bullish(sol_eth, "SOL/ETH")
    active, confidence, sig_c, checks = _finalize_scenario(structural, sol_eth, checks, min_confidence)
    return {
        "scenario": "ETH_TO_SOL",
        "label_ru": "Перелив ETH → SOL",
        "anchor_tf": tf,
        "confidence": round(confidence, 4),
        "signal_confidence": None if sig_c is None else round(sig_c, 4),
        "structure_ok": structural,
        "min_confidence": min_confidence,
        "active": active,
        "checks": checks,
    }


def _scenario_btc_to_sol(
    results: Dict[str, Any], tf: str, min_confidence: float
) -> Dict[str, Any]:
    sol_btc = _blk(results, "SOL/BTC", tf)
    btc = _blk(results, "BTC", tf)
    checks: List[Dict[str, Any]] = []

    pair_ok, pc = _check_pair_bullish(sol_btc, "SOL/BTC")
    checks.extend(pc)

    btc_ok = True
    if btc:
        br = _rsi(btc)
        if br is not None:
            btc_ok = br > 25.0
            checks.append({"name": "BTC/USDT RSI", "ok": btc_ok, "detail": f"rsi={br:.1f}"})
    else:
        checks.append({"name": "BTC/USDT", "ok": False, "detail": "нет данных"})

    structural = pair_ok and btc_ok
    active, confidence, sig_c, checks = _finalize_scenario(structural, sol_btc, checks, min_confidence)
    return {
        "scenario": "BTC_TO_SOL",
        "label_ru": "Перелив BTC → SOL",
        "anchor_tf": tf,
        "confidence": round(confidence, 4),
        "signal_confidence": None if sig_c is None else round(sig_c, 4),
        "structure_ok": structural,
        "min_confidence": min_confidence,
        "active": active,
        "checks": checks,
    }


def compute_rotation_scenarios(
    results: Dict[str, Any],
    anchor_tf: str = DEFAULT_ANCHOR_TF,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> List[Dict[str, Any]]:
    """Три сценария с флагом active, confidence и проверками."""
    return [
        _scenario_btc_to_eth(results, anchor_tf, min_confidence),
        _scenario_eth_to_sol(results, anchor_tf, min_confidence),
        _scenario_btc_to_sol(results, anchor_tf, min_confidence),
    ]


def compute_rotation_bundle(
    results: Dict[str, Any],
    anchor_tf: str = DEFAULT_ANCHOR_TF,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> Dict[str, Any]:
    """Пакет для JSON: сценарии + текст условий."""
    scenarios = compute_rotation_scenarios(results, anchor_tf, min_confidence)
    hot = [s for s in scenarios if s.get("active")]
    return {
        "anchor_tf": anchor_tf,
        "min_confidence": min_confidence,
        "active_count": len(hot),
        "active_scenarios": [s["scenario"] for s in hot],
        "scenarios": scenarios,
        "conditions_ru": CONDITIONS_RU.strip(),
    }


def format_rotation_console(bundle: Dict[str, Any]) -> str:
    lines = [
        "",
        "—— Переливы (фильтры) ——",
        f"ТФ: {bundle.get('anchor_tf')} | порог confidence: {float(bundle.get('min_confidence') or 0):.0%}",
    ]
    for s in bundle.get("scenarios") or []:
        mark = "ДА " if s.get("active") else "нет"
        conf = s.get("confidence")
        sig_c = s.get("signal_confidence")
        st_ok = s.get("structure_ok")
        sig_txt = "—" if sig_c is None else f"{float(sig_c):.0%}"
        lines.append(
            f"[{mark}] {s.get('label_ru')} ({s.get('scenario')})  "
            f"confidence={conf}  сигнал={sig_txt}  структура={'да' if st_ok else 'нет'}"
        )
        for c in s.get("checks") or []:
            ok = "✓" if c.get("ok") else "✗"
            lines.append(f"    {ok} {c.get('name')}: {c.get('detail')}")
    return "\n".join(lines)
