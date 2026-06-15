"""Multi-Timeframe Confluence — оценка совпадения сигналов по таймфреймам."""
from typing import Dict, Any, List, Optional

try:
    from ..utils.logger import setup_logger
except ImportError:
    import logging
    def setup_logger(name: str):
        return logging.getLogger(name)

logger = setup_logger(__name__)

TF_WEIGHTS: Dict[str, float] = {
    "1M": 1.0,
    "1w": 0.85,
    "3d": 0.70,
    "1d": 0.60,
    "4h": 0.40,
    "1h": 0.25,
}


def compute_confluence(
    results: Dict[str, Any],
    asset: str,
) -> Dict[str, Any]:
    """
    Оценивает совпадение направления по всем таймфреймам для актива.

    Считает взвешенный score по RSI, EMA, MACD для каждого TF.
    Если 3+ TF указывают в одну сторону → сильная конфлюенция.

    Returns:
        {
            "direction": "BULLISH" / "BEARISH" / "MIXED",
            "score": float [0-1],
            "bullish_tfs": ["1d", "4h", ...],
            "bearish_tfs": ["1w", "3d", ...],
            "details": [...]
        }
    """
    asset_data = results.get(asset, {})
    if not asset_data:
        return {"direction": "MIXED", "score": 0.0, "bullish_tfs": [], "bearish_tfs": [], "details": []}

    bull_score = 0.0
    bear_score = 0.0
    total_weight = 0.0
    bullish_tfs: List[str] = []
    bearish_tfs: List[str] = []
    details: List[str] = []

    for tf, tf_data in asset_data.items():
        if tf.startswith("_") or not isinstance(tf_data, dict):
            continue

        weight = TF_WEIGHTS.get(tf, 0.3)
        total_weight += weight

        tf_bull = 0.0
        tf_bear = 0.0

        rsi_data = tf_data.get("rsi", {})
        rsi_signal = rsi_data.get("rsi_signal", "NEUTRAL")
        if rsi_signal == "BUY":
            tf_bull += 0.33
        elif rsi_signal == "SELL":
            tf_bear += 0.33

        ema_data = tf_data.get("ema", {})
        ema_trend = ema_data.get("trend", "NEUTRAL")
        if ema_trend == "BULLISH":
            tf_bull += 0.33
        elif ema_trend == "BEARISH":
            tf_bear += 0.33

        macd_data = tf_data.get("macd", {})
        macd_signal = macd_data.get("macd_signal", "NEUTRAL")
        if macd_signal == "BUY":
            tf_bull += 0.17
        elif macd_signal == "SELL":
            tf_bear += 0.17

        macd_div = macd_data.get("divergence")
        if macd_div == "BULLISH":
            tf_bull += 0.17
        elif macd_div == "BEARISH":
            tf_bear += 0.17

        if tf_bull > tf_bear:
            bull_score += weight * tf_bull
            bullish_tfs.append(tf)
            details.append(f"{tf}: BULL ({tf_bull:.0%})")
        elif tf_bear > tf_bull:
            bear_score += weight * tf_bear
            bearish_tfs.append(tf)
            details.append(f"{tf}: BEAR ({tf_bear:.0%})")
        else:
            details.append(f"{tf}: MIXED")

    if total_weight == 0:
        return {"direction": "MIXED", "score": 0.0, "bullish_tfs": [], "bearish_tfs": [], "details": []}

    norm_bull = bull_score / total_weight
    norm_bear = bear_score / total_weight

    if norm_bull > norm_bear and len(bullish_tfs) >= 3:
        direction = "BULLISH"
        score = norm_bull
    elif norm_bear > norm_bull and len(bearish_tfs) >= 3:
        direction = "BEARISH"
        score = norm_bear
    else:
        direction = "MIXED"
        score = max(norm_bull, norm_bear)

    logger.info(
        "MTF Confluence [%s]: %s (%.0f%%) | bull=%s | bear=%s",
        asset, direction, score * 100,
        ",".join(bullish_tfs) or "—",
        ",".join(bearish_tfs) or "—",
    )

    return {
        "direction": direction,
        "score": round(score, 3),
        "bullish_tfs": bullish_tfs,
        "bearish_tfs": bearish_tfs,
        "details": details,
    }
