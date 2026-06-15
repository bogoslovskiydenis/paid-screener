"""Анализ рыночных настроений: Open Interest, Long/Short Ratio, Funding Rate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SentimentResult:
    """Результат анализа рыночного настроения для одного актива."""

    long_ratio: Optional[float] = None      # доля лонг-аккаунтов, 0–1 (0.65 = 65% в лонге)
    open_interest: Optional[float] = None   # OI в контрактах (текущее значение)
    oi_change_pct: Optional[float] = None   # % изменение OI к предыдущему замеру
    funding_rate: Optional[float] = None    # текущий funding rate
    sentiment: str = "NEUTRAL"             # CROWDED_LONG | LONG_HEAVY | NEUTRAL | SHORT_HEAVY | CROWDED_SHORT
    confidence_delta: float = 0.0          # корректировка уверенности: от −0.10 до +0.10
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "long_ratio": self.long_ratio,
            "open_interest": self.open_interest,
            "oi_change_pct": self.oi_change_pct,
            "funding_rate": self.funding_rate,
            "sentiment": self.sentiment,
            "confidence_delta": self.confidence_delta,
            "notes": self.notes,
        }


def analyze_market_sentiment(
    signal_type: str,
    long_ratio: Optional[float] = None,
    open_interest: Optional[float] = None,
    oi_prev: Optional[float] = None,
    funding_rate: Optional[float] = None,
) -> SentimentResult:
    """
    Оценивает рыночное настроение и возвращает корректировку confidence.

    Аргументы:
        signal_type:   «BUY» или «SELL»
        long_ratio:    доля лонг-аккаунтов 0–1 (0.65 = 65% в лонге)
        open_interest: текущий OI (в контрактах, единицы не важны — нужна динамика)
        oi_prev:       OI предыдущей итерации (та же единица)
        funding_rate:  текущий funding rate (0.0001 = 0.01%)

    Возвращает:
        SentimentResult с полем confidence_delta ∈ [−0.10, +0.10]
    """
    result = SentimentResult(
        long_ratio=long_ratio,
        open_interest=open_interest,
        funding_rate=funding_rate,
    )

    side = (signal_type or "BUY").strip().upper()
    delta = 0.0
    notes: List[str] = []

    # ─── Open Interest: динамика ───────────────────────────────────────────────
    if open_interest is not None and oi_prev is not None and oi_prev > 0:
        oi_change = (open_interest - oi_prev) / oi_prev * 100.0
        result.oi_change_pct = round(oi_change, 2)

        if oi_change > 5:
            delta += 0.03
            if side == "BUY":
                notes.append(f"OI {oi_change:+.1f}% — новые деньги входят, тренд подтверждён ▲")
            else:
                notes.append(f"OI {oi_change:+.1f}% — шорты наращивают, давление усиливается ▼")
        elif oi_change < -5:
            delta -= 0.03
            if side == "BUY":
                notes.append(f"OI {oi_change:.1f}% — позиции закрываются, тренд слабеет")
            else:
                notes.append(f"OI {oi_change:.1f}% — шорты закрываются, давление ослабевает")

    # ─── Funding Rate ─────────────────────────────────────────────────────────
    if funding_rate is not None:
        fr_pct = funding_rate * 100.0
        if funding_rate > 0.0005:      # > +0.05%: лонги переплачивают
            if side == "BUY":
                delta -= 0.05
                notes.append(f"Funding {fr_pct:+.4f}% — лонги переплачивают, рынок перегрет ▼")
            else:
                delta += 0.03
                notes.append(f"Funding {fr_pct:+.4f}% — шорты получают выплату, давление на шорт")
        elif funding_rate < -0.0003:   # < −0.03%: шорты переплачивают
            if side == "BUY":
                delta += 0.03
                notes.append(f"Funding {fr_pct:+.4f}% — шорты переплачивают, разворот вверх вероятен ▲")
            else:
                delta -= 0.05
                notes.append(f"Funding {fr_pct:+.4f}% — шорты перегреты, риск шорт-сквиза ▼")
        else:
            notes.append(f"Funding {fr_pct:+.4f}% — нейтрально")

    # ─── Long/Short Ratio ─────────────────────────────────────────────────────
    if long_ratio is not None:
        long_pct = long_ratio * 100.0

        if long_ratio >= 0.70:          # ≥70% в лонге: «толпа» зашла лонг
            result.sentiment = "CROWDED_LONG"
            if side == "BUY":
                delta -= 0.05
                notes.append(f"L/S {long_pct:.0f}% лонги — рынок перекуплен, contrarian ▼")
            else:
                delta += 0.05
                notes.append(f"L/S {long_pct:.0f}% лонги — шорт против перекупленной толпы ▼")

        elif long_ratio <= 0.30:        # ≤30% в лонге (≥70% шортов): «толпа» зашла шорт
            result.sentiment = "CROWDED_SHORT"
            if side == "BUY":
                delta += 0.05
                notes.append(f"L/S {long_pct:.0f}% лонги — рынок перепродан, шорт-сквиз вероятен ▲")
            else:
                delta -= 0.05
                notes.append(f"L/S {long_pct:.0f}% лонги — шортов слишком много, contrarian ▼")

        elif long_ratio >= 0.62:        # умеренно перегружен лонгами
            result.sentiment = "LONG_HEAVY"
            if side == "BUY":
                delta -= 0.02
                notes.append(f"L/S {long_pct:.0f}% лонги — лёгкий перевес лонгов, осторожно")
            else:
                notes.append(f"L/S {long_pct:.0f}% лонги — немного перегружен лонгами")

        elif long_ratio <= 0.38:        # умеренно перегружен шортами
            result.sentiment = "SHORT_HEAVY"
            if side == "SELL":
                delta -= 0.02
                notes.append(f"L/S {long_pct:.0f}% лонги — много шортов, осторожно")
            else:
                notes.append(f"L/S {long_pct:.0f}% лонги — лёгкий перевес шортов")
        else:
            notes.append(f"L/S {long_pct:.0f}% лонги — нейтрально")

    # ─── Итог ─────────────────────────────────────────────────────────────────
    result.confidence_delta = round(max(-0.10, min(0.10, delta)), 3)
    result.notes = notes
    return result
