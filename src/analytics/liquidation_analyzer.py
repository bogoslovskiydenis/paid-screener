"""
Анализ уровней ликвидации фьючерсных позиций.

Вариант A — детекция реальных ликвидаций через резкие падения OI (15m):
  OI_drop + price_drop  → принудительное закрытие лонгов
  OI_drop + price_rise  → принудительное закрытие шортов

Вариант B — прогнозная тепловая карта: где открытые лонги будут
  ликвидированы при продолжении падения (OI history 1h + L/S ratio).
"""

import time
import logging
import requests
import pandas as pd
from collections import defaultdict
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

_MAINT_MARGIN = 0.005          # maintenance margin Binance ETH tier-1
_LEVERAGE_DIST = {5: 0.15, 10: 0.40, 20: 0.35, 50: 0.10}

BUCKET_SIZES: Dict[str, float] = {
    "ETHUSDT":  15.0,
    "BTCUSDT": 250.0,
    "SOLUSDT":   0.5,
    "BNBUSDT":   3.0,
    "SUIUSDT":  0.02,
    "XLMUSDT": 0.005,
}


def _liq_long(entry: float, leverage: int) -> float:
    return entry * (1.0 - 1.0 / leverage + _MAINT_MARGIN)


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


class LiquidationAnalyzer:

    DATA = "https://fapi.binance.com/futures/data"
    FAPI = "https://fapi.binance.com"

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers["User-Agent"] = "PaidScreener/1.0"

    def _get(self, url: str, params: dict = None) -> Optional[Any]:
        for attempt in range(3):
            try:
                r = self._s.get(url, params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"LiqAnalyzer {url}: {e}")
                    return None
                time.sleep(1.5)
        return None

    # ─────────────────────────────────────────────────────────────
    # Общие данные
    # ─────────────────────────────────────────────────────────────

    def fetch_oi_history(
        self, symbol: str, period: str = "15m", limit: int = 200
    ) -> pd.DataFrame:
        """OI history с заданным периодом."""
        data = self._get(
            f"{self.DATA}/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df["oi_usd"] = df["sumOpenInterestValue"].astype(float)
        df["oi_qty"] = df["sumOpenInterest"].astype(float)
        return df.sort_values("timestamp").reset_index(drop=True)

    def fetch_ls_ratio(
        self, symbol: str, period: str = "1h", limit: int = 168
    ) -> pd.DataFrame:
        """Long/Short account ratio."""
        data = self._get(
            f"{self.DATA}/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": limit},
        )
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["timestamp"]   = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df["longAccount"] = df["longAccount"].astype(float)
        return df.sort_values("timestamp").reset_index(drop=True)

    # ─────────────────────────────────────────────────────────────
    # ВАРИАНТ A — Детекция реальных ликвидаций через OI drops
    # ─────────────────────────────────────────────────────────────

    def detect_liquidation_events(
        self,
        ohlcv_15m: pd.DataFrame,
        oi_15m: pd.DataFrame,
        oi_drop_threshold: float = 0.003,  # 0.3% падение OI за свечу
    ) -> pd.DataFrame:
        """
        Обнаруживает события ликвидации по корреляции OI_drop + price_move.

        OI_drop + price_drop → лонги ликвидированы
        OI_drop + price_rise → шорты ликвидированы
        """
        if ohlcv_15m.empty or oi_15m.empty:
            return pd.DataFrame()

        price_df = ohlcv_15m.copy()
        if not pd.api.types.is_datetime64_any_dtype(price_df["timestamp"]):
            price_df["timestamp"] = pd.to_datetime(price_df["timestamp"], unit="ms")

        merged = pd.merge_asof(
            oi_15m.sort_values("timestamp"),
            price_df[["timestamp", "open", "high", "low", "close", "volume"]]
            .sort_values("timestamp"),
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta("20m"),
        )

        merged["oi_pct_chg"] = merged["oi_usd"].pct_change(fill_method=None)
        merged["price_chg"]  = merged["close"].pct_change(fill_method=None)

        # Только значимые падения OI
        drops = merged[merged["oi_pct_chg"] < -oi_drop_threshold].copy()
        if drops.empty:
            return pd.DataFrame()

        drops["oi_dropped_usd"] = (-drops["oi_pct_chg"]) * drops["oi_usd"]
        drops["side"] = drops["price_chg"].apply(
            lambda x: "LONG_LIQ" if x <= 0 else "SHORT_LIQ"
        )

        # Цена события: low свечи при лонг-ликвидации, high при шорт
        drops["event_price"] = drops.apply(
            lambda r: float(r["low"]) if r["side"] == "LONG_LIQ" else float(r["high"]),
            axis=1,
        )

        return drops[
            ["timestamp", "event_price", "oi_dropped_usd", "side", "price_chg"]
        ].reset_index(drop=True)

    def build_hist_clusters(
        self,
        events: pd.DataFrame,
        bucket_size: float,
        current_price: float,
    ) -> pd.DataFrame:
        """Группирует события ликвидации по ценовым бакетам."""
        if events.empty:
            return pd.DataFrame()

        buckets: Dict[float, Dict] = defaultdict(
            lambda: {"long_usd": 0.0, "short_usd": 0.0, "long_cnt": 0, "short_cnt": 0}
        )

        for _, row in events.iterrows():
            ep = row.get("event_price")
            if ep is None or (isinstance(ep, float) and ep != ep):  # NaN check
                continue
            key = round(float(ep) / bucket_size) * bucket_size
            usd = float(row["oi_dropped_usd"])
            if row["side"] == "LONG_LIQ":
                buckets[key]["long_usd"]  += usd
                buckets[key]["long_cnt"]  += 1
            else:
                buckets[key]["short_usd"] += usd
                buckets[key]["short_cnt"] += 1

        rows = []
        for level, d in sorted(buckets.items()):
            rows.append({
                "price":     level,
                "long_usd":  d["long_usd"],
                "short_usd": d["short_usd"],
                "total_usd": d["long_usd"] + d["short_usd"],
                "long_cnt":  d["long_cnt"],
                "short_cnt": d["short_cnt"],
                "dist_pct":  (level - current_price) / current_price * 100,
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ─────────────────────────────────────────────────────────────
    # ВАРИАНТ B — Прогнозная тепловая карта
    # ─────────────────────────────────────────────────────────────

    def build_pred_heatmap(
        self,
        ohlcv_1h: pd.DataFrame,
        oi_1h: pd.DataFrame,
        ls_df: pd.DataFrame,
        current_price: float,
        bucket_size: float,
    ) -> pd.DataFrame:
        """
        Оценивает где открытые лонги будут ликвидированы.

        1. Находим часы, когда OI рос (новые позиции открывались по цене close).
        2. L/S ratio даёт долю лонгов среди открытых позиций.
        3. Для каждого уровня входа считаем цену ликвидации при 5x/10x/20x/50x.
        4. Аккумулируем USD под риском ниже текущей цены.
        """
        if oi_1h.empty or ohlcv_1h.empty:
            return pd.DataFrame()

        price_df = ohlcv_1h.copy()
        if not pd.api.types.is_datetime64_any_dtype(price_df["timestamp"]):
            price_df["timestamp"] = pd.to_datetime(price_df["timestamp"], unit="ms")

        merged = pd.merge_asof(
            oi_1h.sort_values("timestamp"),
            price_df[["timestamp", "close"]].sort_values("timestamp"),
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta("2h"),
        )

        if not ls_df.empty:
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                ls_df[["timestamp", "longAccount"]].sort_values("timestamp"),
                on="timestamp",
                direction="nearest",
                tolerance=pd.Timedelta("2h"),
            )
        else:
            merged["longAccount"] = 0.55

        merged["oi_delta"] = merged["oi_usd"].diff()
        additions = merged[
            (merged["oi_delta"] > 0) & merged["close"].notna()
        ].copy()

        if additions.empty:
            return pd.DataFrame()

        liq_map: Dict[float, float] = defaultdict(float)

        for _, row in additions.iterrows():
            entry     = float(row["close"])
            oi_added  = float(row["oi_delta"])
            long_frac = float(row.get("longAccount", 0.55))

            if entry > current_price * 1.55:
                continue  # позиции далеко выше — скорее всего уже закрыты

            long_oi = oi_added * long_frac

            for lev, weight in _LEVERAGE_DIST.items():
                liq = _liq_long(entry, lev)
                if liq >= current_price:
                    continue
                key = round(liq / bucket_size) * bucket_size
                liq_map[key] += long_oi * weight

        rows = [
            {
                "price":    level,
                "est_usd":  usd,
                "dist_pct": (level - current_price) / current_price * 100,
            }
            for level, usd in sorted(liq_map.items(), key=lambda x: -x[1])
            if usd > 0 and level < current_price
        ]
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    # ─────────────────────────────────────────────────────────────
    # Полный анализ (A + B)
    # ─────────────────────────────────────────────────────────────

    def analyze(
        self,
        symbol: str,
        current_price: float,
        ohlcv_15m: pd.DataFrame = None,
        ohlcv_1h:  pd.DataFrame = None,
        hours: int = 48,
    ) -> Dict[str, Any]:
        bucket = BUCKET_SIZES.get(symbol, max(1.0, round(current_price * 0.01, 2)))

        # ── A ──
        logger.info(f"[Liq/{symbol}] Variant A: загружаем OI 15m ({hours}ч)...")
        limit_15m = min(500, hours * 4)  # 4 свечи/час
        oi_15m = self.fetch_oi_history(symbol, period="15m", limit=limit_15m)

        events   = pd.DataFrame()
        hist     = pd.DataFrame()
        if not oi_15m.empty and ohlcv_15m is not None and not ohlcv_15m.empty:
            events = self.detect_liquidation_events(ohlcv_15m, oi_15m)
            hist   = self.build_hist_clusters(events, bucket, current_price)

        # ── B ──
        pred = pd.DataFrame()
        if ohlcv_1h is not None and not ohlcv_1h.empty:
            logger.info(f"[Liq/{symbol}] Variant B: прогнозная карта...")
            oi_1h = self.fetch_oi_history(symbol, period="1h", limit=168)
            ls_df = self.fetch_ls_ratio(symbol, period="1h", limit=168)
            pred  = self.build_pred_heatmap(
                ohlcv_1h, oi_1h, ls_df, current_price, bucket
            )

        # Пересечение A ∩ B
        overlap: List[float] = []
        if not hist.empty and not pred.empty:
            overlap = sorted(
                set(hist["price"].values) & set(pred["price"].values),
                reverse=True,
            )

        return {
            "symbol":        symbol,
            "current_price": current_price,
            "bucket":        bucket,
            "events_count":  len(events),
            "hist":          hist,
            "pred":          pred,
            "overlap":       overlap,
        }


# ─────────────────────────────────────────────────────────────────
# Консольный форматтер
# ─────────────────────────────────────────────────────────────────

def format_liq_console(result: Dict[str, Any], top_n: int = 8) -> str:
    sym          = result["symbol"]
    price        = result["current_price"]
    hist         = result.get("hist", pd.DataFrame())
    pred         = result.get("pred", pd.DataFrame())
    overlap_set  = set(result.get("overlap", []))

    lines = [
        "",
        f"══ Ликвидационные уровни {sym} | цена ${price:,.4f} ══",
    ]

    # ── Вариант A ──────────────────────────────────────────────
    ev_cnt = result.get("events_count", 0)
    total_a = hist["total_usd"].sum() if not hist.empty else 0.0
    lines.append(
        f"\n▸ A — Реальные ликвидации (OI-drop детекция, 48ч) "
        f"[{ev_cnt} событий | {_fmt_usd(total_a)}]"
    )

    if hist.empty:
        lines.append("   нет значимых событий ликвидации")
    else:
        above = hist[hist["price"] > price].nlargest(3, "short_usd")
        below = hist[hist["price"] < price].nlargest(top_n, "long_usd")

        hdr = f"  {'Уровень':>10}  {'Лонги ликв':>11}  {'Шорты ликв':>11}  {'Δ%':>6}"
        lines += [hdr, "  " + "─" * 48]

        for _, r in above.sort_values("price", ascending=False).iterrows():
            tag = "▲ short liq" if r["short_usd"] > r["long_usd"] else ""
            lines.append(
                f"  ${r['price']:>9,.2f}  "
                f"{_fmt_usd(r['long_usd']):>11}  "
                f"{_fmt_usd(r['short_usd']):>11}  "
                f"{r['dist_pct']:>+5.1f}%  {tag}"
            )

        lines.append(f"  ──── текущая ${price:,.2f} ────")

        for _, r in below.iterrows():
            if r["long_usd"] >= 50_000_000:
                tag = "◄◄◄ ОГРОМНЫЙ"
            elif r["long_usd"] >= 10_000_000:
                tag = "◄◄◄ КРУПНЫЙ"
            elif r["long_usd"] >= 3_000_000:
                tag = "◄◄"
            elif r["long_usd"] >= 1_000_000:
                tag = "◄"
            else:
                tag = ""
            spark = "⚡" if r["price"] in overlap_set else ""
            lines.append(
                f"  ${r['price']:>9,.2f}  "
                f"{_fmt_usd(r['long_usd']):>11}  "
                f"{_fmt_usd(r['short_usd']):>11}  "
                f"{r['dist_pct']:>+5.1f}%  {tag} {spark}"
            )

    # ── Вариант B ──────────────────────────────────────────────
    lines.append("\n▸ B — Прогноз каскадных ликвидаций (оценка по OI 7д)")

    if pred.empty:
        lines.append("   нет данных")
    else:
        below_pred = pred[pred["price"] < price].head(top_n)
        max_usd = below_pred["est_usd"].max() if not below_pred.empty else 1.0

        lines.append(f"  {'Уровень':>10}  {'USD под риском':>14}  {'Δ%':>6}")
        lines.append("  " + "─" * 44)

        for _, r in below_pred.iterrows():
            bar_n = max(1, int(r["est_usd"] / max_usd * 12))
            bar   = "█" * bar_n + "░" * (12 - bar_n)
            spark = "⚡" if r["price"] in overlap_set else ""
            lines.append(
                f"  ${r['price']:>9,.2f}  "
                f"  [{bar}] {_fmt_usd(r['est_usd']):>8}  "
                f"{r['dist_pct']:>+5.1f}%  {spark}"
            )

    # ── Пересечения ────────────────────────────────────────────
    danger = [lv for lv in result.get("overlap", []) if lv < price]
    if danger:
        lines.append("\n▸ ⚡ Зоны повышенного риска (A ∩ B):")
        for lv in danger[:5]:
            lines.append(f"   ${lv:,.2f}  ({(lv-price)/price*100:+.1f}% от цены)")

    return "\n".join(lines)


# Typing hint needed after class definition
from typing import List  # noqa: E402
