"""Определение текущей торговой сессии по времени Киева."""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Сессии в часах Киева (EEST UTC+3 / EET UTC+2 — zoneinfo учитывает автоматически)
# Азиатская:    03:00–12:00
# Европейская:  10:00–19:00
# Американская: 16:00–01:00
_SESSIONS = [
    ("🌏 Азиатская",    3,  12),
    ("🌍 Европейская", 10,  19),
    ("🌎 Американская", 16, 25),  # 25 = следующего дня 01:00
]

_SESSION_RISK = {
    "🌏 Азиатская": (
        "⚠️ Азиатская сессия — низкий объём, риск ложных пробоев выше"
    ),
    "🌍 Европейская": None,
    "🌎 Американская": None,
}


def now_kyiv() -> datetime:
    return datetime.now(KYIV_TZ)


def get_session(dt: datetime | None = None) -> str:
    """Возвращает название текущей торговой сессии."""
    if dt is None:
        dt = now_kyiv()
    hour = dt.hour + (dt.minute / 60)

    active = []
    for name, start, end in _SESSIONS:
        h = hour if end <= 24 else (hour if hour >= start else hour + 24)
        if start <= h < end:
            active.append(name)

    if len(active) >= 2:
        # Перекрытие — называем старшую (американская > европейская > азиатская)
        for priority in ("🌎 Американская", "🌍 Европейская", "🌏 Азиатская"):
            if priority in active:
                return priority
    if active:
        return active[0]

    return "🌙 Межсессионная"


def session_risk_note(session: str | None = None) -> str | None:
    """Возвращает предупреждение о риске для данной сессии, или None."""
    if session is None:
        session = get_session()
    return _SESSION_RISK.get(session)


def session_line() -> str:
    """Готовая строка для Telegram: '🕐 21:41 Киев | 🌎 Американская сессия'"""
    dt = now_kyiv()
    session = get_session(dt)
    return f"🕐 {dt.strftime('%H:%M')} Киев | {session} сессия"
