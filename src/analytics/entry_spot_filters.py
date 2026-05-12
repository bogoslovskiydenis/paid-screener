"""Метрики качества входа в спот-лонг (USDT-базы) по уже посчитанному JSON анализа."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_ANCHOR_TF = "1d"
DEFAULT_TIMING_TF = "4h"
SPOT_USDT_ASSETS = ("ETH", "SOL", "BTC", "BNB", "XLM")

METRICS_RU = """
Метрики для более аккуратного входа в лонг (спот), без новых API:

Старший ТФ (по умолчанию 1d):
  • EMA: бычий тренд или бычий кросс EMA9/EMA21.
  • RSI пары <72 и не «жарко» (≥70).

Младший ТФ (по умолчанию 4h):
  • RSI не в перекупе на этом ТФ, rsi < 68.
  • MACD сигнал не SELL.
  • Ближайшая поддержка снизу не дальше 2% от цены (если уровни есть).

Если есть BUY от генератора — дополнительно проверка confidence ≥ порога.

«ДА» = все обязательные условия для актива выполнены.
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


def _macd_sig(block: Dict[str, Any]) -> str:
    m = block.get("macd")
    if not isinstance(m, dict):
        return ""
    return str(m.get("macd_signal") or "").upper()


def _nearest_support_dist_pct(levels: Any, price: float) -> Optional[float]:
    if not isinstance(levels, dict) or price <= 0:
        return None
    supports = levels.get("support_levels") or []
    if not supports:
        return None
    below = []
    for s in supports:
        if isinstance(s, dict) and isinstance(s.get("price"), (int, float)):
            p = float(s["price"])
            if p <= price:
                below.append(p)
    if not below:
        return None
    sup = max(below)
    return (price - sup) / price * 100.0


def _score_asset_long(
    results: Dict[str, Any],
    asset: str,
    anchor_tf: str,
    timing_tf: str,
    min_confidence: float,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    ab = _blk(results, asset, anchor_tf)
    tb = _blk(results, asset, timing_tf)

    if not ab or not tb:
        checks.append(
            {"name": "данные anchor/timing", "ok": False, "detail": f"{anchor_tf}/{timing_tf}"}
        )
        return {
            "asset": asset,
            "anchor_tf": anchor_tf,
            "timing_tf": timing_tf,
            "active": False,
            "confidence": 0.0,
            "signal_confidence": None,
            "structure_ok": False,
            "checks": checks,
        }

    ema_a = _ema(ab)
    trend = str(ema_a.get("trend") or "")
    cross = str(ema_a.get("ema_cross") or "")
    anchor_trend_ok = trend == "BULLISH" or cross == "BULLISH"
    checks.append(
        {
            "name": f"{anchor_tf} EMA (контекст)",
            "ok": anchor_trend_ok,
            "detail": f"trend={trend}, cross={cross}",
        }
    )

    rsi_a = _rsi(ab)
    hot_a = rsi_a is not None and rsi_a >= 70.0
    rsi_a_ok = rsi_a is None or (rsi_a < 72.0 and not hot_a)
    checks.append(
        {
            "name": f"{anchor_tf} RSI",
            "ok": rsi_a_ok,
            "detail": f"rsi={rsi_a}, zone={_rsi_zone(ab)}",
        }
    )

    zone_t = _rsi_zone(tb)
    rsi_t = _rsi(tb)
    timing_rsi_ok = zone_t.upper() != "OVERBOUGHT" and (rsi_t is None or rsi_t < 68.0)
    checks.append(
        {
            "name": f"{timing_tf} RSI (не вершина)",
            "ok": timing_rsi_ok,
            "detail": f"rsi={rsi_t}, zone={zone_t}",
        }
    )

    mac_t = _macd_sig(tb)
    mac_ok = mac_t != "SELL"
    checks.append(
        {
            "name": f"{timing_tf} MACD",
            "ok": mac_ok,
            "detail": mac_t or "N/A",
        }
    )

    cp = float(tb.get("current_price") or 0)
    levels = tb.get("levels")
    dist = _nearest_support_dist_pct(levels, cp) if cp else None
    near_support = dist is not None and dist <= 2.0
    checks.append(
        {
            "name": "близость к поддержке (≤2%)",
            "ok": True if dist is None else near_support,
            "detail": f"dist%={dist:.3f}" if dist is not None else "уровней нет",
        }
    )

    structural = anchor_trend_ok and rsi_a_ok and timing_rsi_ok and mac_ok

    sig = tb.get("signal") if isinstance(tb.get("signal"), dict) else None
    sig_buy = sig and str(sig.get("signal_type") or "").upper() == "BUY"
    sig_conf = float(sig["confidence"]) if sig and sig.get("confidence") is not None else None
    pre_frac = sum(1 for c in checks if c.get("ok")) / max(len(checks), 1)

    if sig_buy and sig_conf is not None:
        gate = sig_conf >= min_confidence
        checks.append(
            {
                "name": f"BUY сигнал ≥ {min_confidence:.0%}",
                "ok": gate,
                "detail": f"{sig_conf:.1%}",
            }
        )
        active = structural and gate
        conf_rep = sig_conf if active else min(sig_conf, pre_frac)
        return {
            "asset": asset,
            "anchor_tf": anchor_tf,
            "timing_tf": timing_tf,
            "active": active,
            "confidence": round(conf_rep, 4),
            "signal_confidence": round(sig_conf, 4),
            "structure_ok": structural,
            "checks": checks,
        }

    checks.append({"name": "BUY сигнал генератора", "ok": False, "detail": "нет"})
    inner = 1.0 if structural else pre_frac
    active = structural and inner >= min_confidence
    return {
        "asset": asset,
        "anchor_tf": anchor_tf,
        "timing_tf": timing_tf,
        "active": active,
        "confidence": round(inner, 4),
        "signal_confidence": None,
        "structure_ok": structural,
        "checks": checks,
    }


def compute_spot_long_bundle(
    results: Dict[str, Any],
    anchor_tf: str = DEFAULT_ANCHOR_TF,
    timing_tf: str = DEFAULT_TIMING_TF,
    min_confidence: float = 0.7,
) -> Dict[str, Any]:
    scenarios = [
        _score_asset_long(results, a, anchor_tf, timing_tf, min_confidence)
        for a in SPOT_USDT_ASSETS
    ]
    hot = [s for s in scenarios if s.get("active")]
    return {
        "anchor_tf": anchor_tf,
        "timing_tf": timing_tf,
        "min_confidence": min_confidence,
        "active_count": len(hot),
        "assets_ok": [s["asset"] for s in hot],
        "scenarios": scenarios,
        "metrics_ru": METRICS_RU.strip(),
    }


def format_spot_long_console(bundle: Dict[str, Any]) -> str:
    lines = [
        "",
        "—— Спот-лонг: качество входа ——",
        f"{bundle.get('anchor_tf')} + {bundle.get('timing_tf')} | порог {float(bundle.get('min_confidence') or 0):.0%}",
    ]
    for s in bundle.get("scenarios") or []:
        mark = "ДА " if s.get("active") else "нет"
        sig_c = s.get("signal_confidence")
        sig_txt = "—" if sig_c is None else str(sig_c)
        lines.append(
            f"[{mark}] {s.get('asset')}  confidence={s.get('confidence')}  "
            f"сигнал={sig_txt}  структура={'да' if s.get('structure_ok') else 'нет'}"
        )
        for c in s.get("checks") or []:
            ok = "✓" if c.get("ok") else "✗"
            lines.append(f"    {ok} {c.get('name')}: {c.get('detail')}")
    return "\n".join(lines)
