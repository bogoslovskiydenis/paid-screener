"""Сводка по связанным инструментам: USDT + кроссы + BTC для XLM/SOL и полный BTC."""
from __future__ import annotations

from typing import Any, Dict, Optional


def _summarize_block(block: Dict[str, Any]) -> Dict[str, Any]:
    sig = block.get("signal") if isinstance(block.get("signal"), dict) else None
    rsi_o = block.get("rsi") or {}
    ema_o = block.get("ema") or {}
    macd_o = block.get("macd") or {}
    conf = None
    if sig and sig.get("confidence") is not None:
        try:
            conf = float(sig["confidence"])
        except (TypeError, ValueError):
            conf = None
    return {
        "price": block.get("current_price"),
        "signal_type": (sig or {}).get("signal_type"),
        "confidence": conf,
        "strength": (sig or {}).get("strength"),
        "rsi": rsi_o.get("rsi") if isinstance(rsi_o, dict) else None,
        "rsi_zone": rsi_o.get("rsi_zone") if isinstance(rsi_o, dict) else None,
        "ema_trend": ema_o.get("trend") if isinstance(ema_o, dict) else None,
        "macd_signal": macd_o.get("macd_signal") if isinstance(macd_o, dict) else None,
    }


def _per_timeframe(asset_node: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(asset_node, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for tf, block in asset_node.items():
        if not isinstance(tf, str) or tf.startswith("_"):
            continue
        if not isinstance(block, dict) or block.get("error"):
            continue
        out[tf] = _summarize_block(block)
    return out


def build_context_bundles(results: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "_context_XLM": {
            "label": "XLM/USDT + XLM/BTC + BTC/USDT",
            "XLM/USDT": _per_timeframe(results.get("XLM")),
            "XLM/BTC": _per_timeframe(results.get("XLM/BTC")),
            "BTC/USDT": _per_timeframe(results.get("BTC")),
        },
        "_context_SOL": {
            "label": "SOL/USDT + SOL/BTC + SOL/ETH + BTC/USDT",
            "SOL/USDT": _per_timeframe(results.get("SOL")),
            "SOL/BTC": _per_timeframe(results.get("SOL/BTC")),
            "SOL/ETH": _per_timeframe(results.get("SOL/ETH")),
            "BTC/USDT": _per_timeframe(results.get("BTC")),
        },
        "_context_BTC": {
            "label": "BTC/USDT (все таймфреймы из прогона)",
            "BTC/USDT": _per_timeframe(results.get("BTC")),
        },
    }


def merge_context_bundles_into_results(results: Dict[str, Any]) -> None:
    results.update(build_context_bundles(results))


def _fmt_tf_row(tf: str, s: Dict[str, Any]) -> str:
    st = s.get("signal_type") or "—"
    cf = s.get("confidence")
    cf_s = f"{cf:.2f}" if isinstance(cf, (int, float)) else "—"
    rsi = s.get("rsi")
    rsi_s = f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—"
    ema = s.get("ema_trend") or "—"
    mac = s.get("macd_signal") or "—"
    return f"      {tf}: sig={st} conf={cf_s} RSI={rsi_s} EMA={ema} MACD={mac}"


def format_context_bundles_console(results: Dict[str, Any]) -> str:
    lines = ["", "—— Полная картина (USDT + кроссы + BTC) ——"]
    for bkey, title in (
        ("_context_XLM", "XLM"),
        ("_context_SOL", "SOL"),
        ("_context_BTC", "BTC"),
    ):
        b = results.get(bkey)
        if not isinstance(b, dict):
            continue
        lines.append(f"[{title}] {b.get('label', '')}")
        for part_key in sorted(k for k in b if k != "label"):
            part = b.get(part_key)
            if not isinstance(part, dict) or not part:
                lines.append(f"  {part_key}: (нет данных в JSON)")
                continue
            lines.append(f"  {part_key}:")
            for tf in sorted(part.keys()):
                lines.append(_fmt_tf_row(tf, part[tf]))
        lines.append("")
    return "\n".join(lines).rstrip()
