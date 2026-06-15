"""Метрики качества входа в спот-лонг (USDT-базы) по уже посчитанному JSON анализа."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_ANCHOR_TF = "1d"
DEFAULT_TIMING_TF = "4h"
SPOT_USDT_ASSETS = ("ETH", "SOL", "BTC", "BNB", "XLM", "SUI")

METRICS_RU = """
Метрики для входа в лонг (спот):

Старший ТФ (1d):
  • EMA: бычий тренд или бычий кросс EMA9/EMA21.
  • RSI <70.

Младший ТФ (4h):
  • RSI не в перекупе, rsi < 68.
  • MACD не SELL.
  • Структура рынка не BEARISH (≥80%).

MTF Confluence:
  • Направление не BEARISH или score ≥ 40%.

Рыночный контекст:
  • Fear & Greed > 20 (не Extreme Fear) — иначе требуется BUY сигнал.

BUY сигнал генератора — обязателен для финального «ДА».
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


def _confluence(results: Dict[str, Any], asset: str) -> Optional[Dict[str, Any]]:
    a = results.get(asset)
    if not isinstance(a, dict):
        return None
    c = a.get("_confluence")
    return c if isinstance(c, dict) else None


def _fear_greed(results: Dict[str, Any]) -> Optional[int]:
    ctx = results.get("_market_context")
    if not isinstance(ctx, dict):
        return None
    fg = ctx.get("fear_greed")
    if not isinstance(fg, dict):
        return None
    v = fg.get("value")
    return int(v) if isinstance(v, (int, float)) else None


def _market_structure_bearish_pct(block: Dict[str, Any]) -> Optional[float]:
    ms = block.get("market_structure")
    if not isinstance(ms, dict):
        return None
    struct = str(ms.get("structure") or "")
    strength = ms.get("trend_strength")
    if struct == "BEARISH" and isinstance(strength, (int, float)):
        return float(strength)
    return 0.0


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

    # 1. EMA на старшем ТФ
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

    # 2. RSI на старшем ТФ
    rsi_a = _rsi(ab)
    rsi_a_ok = rsi_a is None or rsi_a < 70.0
    checks.append(
        {
            "name": f"{anchor_tf} RSI",
            "ok": rsi_a_ok,
            "detail": f"rsi={rsi_a}, zone={_rsi_zone(ab)}",
        }
    )

    # 3. RSI на младшем ТФ
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

    # 4. MACD на младшем ТФ
    mac_t = _macd_sig(tb)
    mac_ok = mac_t != "SELL"
    checks.append(
        {
            "name": f"{timing_tf} MACD",
            "ok": mac_ok,
            "detail": mac_t or "N/A",
        }
    )

    # 5. Структура рынка на 4h — не должна быть глубоко медвежьей
    bear_pct = _market_structure_bearish_pct(tb)
    struct_ok = bear_pct is None or bear_pct < 0.80
    struct_detail = f"BEARISH {bear_pct:.0%}" if bear_pct and bear_pct > 0 else "OK"
    checks.append(
        {
            "name": f"{timing_tf} структура (не BEAR ≥80%)",
            "ok": struct_ok,
            "detail": struct_detail,
        }
    )

    # 6. MTF Confluence — направление не должно быть BEARISH
    conf_data = _confluence(results, asset)
    if conf_data:
        conf_dir = str(conf_data.get("direction") or "")
        conf_score = float(conf_data.get("score") or 0)
        mtf_ok = conf_dir != "BEARISH" or conf_score >= 0.40
        checks.append(
            {
                "name": "MTF Confluence (не BEARISH)",
                "ok": mtf_ok,
                "detail": f"{conf_dir} ({conf_score:.0%})",
            }
        )
    else:
        mtf_ok = True

    # 7. Fear & Greed — в Extreme Fear нужен BUY сигнал
    fg = _fear_greed(results)
    fg_ok = fg is None or fg > 20
    checks.append(
        {
            "name": "Fear&Greed > 20 (не паника)",
            "ok": fg_ok,
            "detail": f"F&G={fg}" if fg is not None else "N/A",
        }
    )

    structural = (
        anchor_trend_ok and rsi_a_ok and timing_rsi_ok
        and mac_ok and struct_ok and mtf_ok
    )

    # 8. BUY сигнал генератора — ОБЯЗАТЕЛЕН
    sig = tb.get("signal") if isinstance(tb.get("signal"), dict) else None
    sig_buy = sig and str(sig.get("signal_type") or "").upper() == "BUY"
    sig_conf = float(sig["confidence"]) if sig and sig.get("confidence") is not None else None

    if sig_buy and sig_conf is not None:
        gate = sig_conf >= min_confidence
        checks.append(
            {
                "name": f"BUY сигнал ≥ {min_confidence:.0%}",
                "ok": gate,
                "detail": f"{sig_conf:.1%}",
            }
        )
        active = structural and gate and fg_ok
        pre_frac = sum(1 for c in checks if c.get("ok")) / max(len(checks), 1)
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
    pre_frac = sum(1 for c in checks if c.get("ok")) / max(len(checks), 1)
    return {
        "asset": asset,
        "anchor_tf": anchor_tf,
        "timing_tf": timing_tf,
        "active": False,
        "confidence": round(pre_frac, 4),
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
