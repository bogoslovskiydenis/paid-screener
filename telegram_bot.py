#!/usr/bin/env python3
"""Простой Telegram-бот для отправки сигналов BUY из JSON-файла."""

import argparse
import html
import json
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.utils.format_price import fmt_price
from src.utils.session import session_line, session_risk_note, get_session

load_dotenv()
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "Переменная TELEGRAM_BOT_TOKEN не задана. "
        "Добавьте её в файл .env или в переменные окружения."
    )


TELEGRAM_DAILY_SENDS_PATH = Path("data/telegram_daily_sends.json")

MAX_SIGNAL_SENDS_PER_DAY = 2
MIN_SIGNAL_CONFIDENCE_REPEAT = 0.7


def daily_quota_key(asset: str, timeframe: str, signal_type: str, day_iso: str) -> str:
    return f"{asset}|{timeframe}|{signal_type}|{day_iso}"


def load_daily_sends(path: Path) -> Dict[str, int]:
    today = date.today().isoformat()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError:
            return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, int):
            continue
        parts = k.split("|")
        if len(parts) >= 4 and parts[-1] == today:
            out[k] = v
    return out


def save_daily_sends(path: Path, data: Dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_signals(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Файл с сигналами не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _tf_block(data: Dict[str, Any], asset: str, tf: str) -> Optional[Dict[str, Any]]:
    a = data.get(asset)
    if not isinstance(a, dict):
        return None
    b = a.get(tf)
    return b if isinstance(b, dict) and not b.get("error") else None


def _btc_blocks_alt_spot_buy(data: Dict[str, Any], anchor_tf: str = "1d", timing_tf: str = "4h") -> bool:
    """Не шлём лонг по альтам, пока BTC на старшем ТФ в медвежьем тренде и 4h не подтверждает разворот."""
    d1 = _tf_block(data, "BTC", anchor_tf)
    if not d1:
        return False
    ema1 = d1.get("ema") if isinstance(d1.get("ema"), dict) else {}
    if str(ema1.get("trend") or "").upper() != "BEARISH":
        return False
    h4 = _tf_block(data, "BTC", timing_tf)
    if not h4:
        return True
    ema4 = h4.get("ema") if isinstance(h4.get("ema"), dict) else {}
    mac4 = h4.get("macd") if isinstance(h4.get("macd"), dict) else {}
    if str(ema4.get("trend") or "").upper() == "BULLISH":
        return False
    if str(ema4.get("ema_cross") or "").upper() == "BULLISH":
        return False
    if str(mac4.get("macd_signal") or "").upper() == "BUY":
        return False
    return True


def save_active_signals(path: Path, signals: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(signals, f, ensure_ascii=False, indent=2)


def collect_buy_signals(
    data: Dict[str, Any],
    min_confidence: float,
    asset_filter: Optional[List[str]] = None,
    timeframes_filter: Optional[List[str]] = None,
    allowed_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for asset, tf_map in data.items():
        if str(asset).startswith("_"):
            continue
        if asset_filter and asset not in asset_filter:
            continue

        if not isinstance(tf_map, dict):
            continue

        for timeframe, tf_data in tf_map.items():
            if timeframes_filter and timeframe not in timeframes_filter:
                continue

            if not isinstance(tf_data, dict):
                continue

            signal = tf_data.get("signal")
            if not signal:
                continue

            signal_type = signal.get("signal_type")
            if allowed_types and signal_type not in allowed_types:
                continue

            strength = str(signal.get("strength") or "").upper()
            confidence = float(signal.get("confidence", 0.0))

            # для SELL на коротких TF — повышенный порог confidence
            min_conf_effective = min_confidence
            if signal_type == "SELL" and timeframe in ("5m", "15m"):
                min_conf_effective = max(min_confidence, 0.75)

            if confidence < min_conf_effective:
                continue

            if strength not in ("MEDIUM", "STRONG"):
                continue

            rsi_info = tf_data.get("rsi") or {}
            rsi_value = rsi_info.get("rsi")
            if isinstance(rsi_value, (int, float)):
                # для SELL требуем RSI > 55: актив должен быть перекуплен, прежде чем шортить
                if signal_type == "SELL" and rsi_value < 55:
                    continue
                if signal_type == "BUY" and rsi_value > 70:
                    continue

            if (
                signal_type == "BUY"
                and asset != "BTC"
                and _btc_blocks_alt_spot_buy(data)
            ):
                continue

            if signal_type == "SELL":
                indicators = signal.get("indicators") or {}
                ema_trend = str(indicators.get("ema_trend") or "").upper()

                if timeframe in ("5m", "15m"):
                    # не шортим в бычьем тренде на коротких TF
                    if ema_trend == "BULLISH":
                        continue

                if timeframe == "15m":
                    sig_1h = (tf_map.get("1h") or {}).get("signal") or {}
                    if sig_1h.get("signal_type") == "BUY":
                        continue
                    sig_4h = (tf_map.get("4h") or {}).get("signal") or {}
                    if sig_4h.get("signal_type") == "BUY":
                        continue

                elif timeframe == "5m":
                    sig_15m = (tf_map.get("15m") or {}).get("signal") or {}
                    if sig_15m.get("signal_type") != "SELL":
                        continue

                elif timeframe == "1h":
                    tf_15m = tf_map.get("15m") or {}
                    tf_4h = tf_map.get("4h") or {}

                    sig_15m = tf_15m.get("signal") or {}
                    sig_15m_type = sig_15m.get("signal_type")
                    sig_15m_strength = str(sig_15m.get("strength") or "").upper()
                    sig_15m_conf = float(sig_15m.get("confidence", 0.0))

                    has_valid_15m_sell = (
                        sig_15m_type == "SELL"
                        and sig_15m_strength in ("MEDIUM", "STRONG")
                        and sig_15m_conf >= min_confidence
                    )

                    if not has_valid_15m_sell:
                        continue

                    tf_4h_patterns = tf_4h.get("chart_patterns") or []
                    has_bullish_4h_pattern = any(
                        isinstance(p, dict)
                        and str(p.get("pattern_direction") or "").upper() == "BULLISH"
                        for p in tf_4h_patterns
                    )

                    sig_4h = tf_4h.get("signal") or {}
                    has_bearish_4h_signal = sig_4h.get("signal_type") == "SELL"

                    if has_bullish_4h_pattern and not has_bearish_4h_signal and strength != "STRONG":
                        continue

            test_trade = signal.get("test_trade") or None
            indicators = signal.get("indicators") or {}
            atr = indicators.get("atr")

            results.append(
                {
                    "asset": asset,
                    "timeframe": timeframe,
                    "signal_type": signal_type,
                    "signal_label": signal.get("signal_label"),       # "OVERSOLD_BOUNCE" или None
                    "bounce_mode": bool(signal.get("bounce_mode")),
                    "warning": signal.get("warning"),
                    "bounce_confirmators": signal.get("bounce_confirmators") or [],
                    "strength": strength,
                    "entry_price": float(signal.get("entry_price")),
                    "stop_loss": float(signal.get("stop_loss")),
                    "take_profit": signal.get("take_profit", []),
                    "confidence": confidence,
                    "test_trade": test_trade,
                    "atr": float(atr) if atr is not None else None,
                    "rsi": indicators.get("rsi"),
                    "rsi_zone": indicators.get("rsi_zone"),
                    "ema_trend": indicators.get("ema_trend"),
                    "macd_signal": indicators.get("macd_signal"),
                    "support_level": indicators.get("support_level"),
                    "resistance_level": indicators.get("resistance_level"),
                    "volume_confirmation": indicators.get("volume_confirmation"),
                    "sentiment": signal.get("sentiment"),
                }
            )

    return results


_TF_RANK = {"5m": 1, "15m": 2, "1h": 3, "4h": 5, "1M": 6, "3d": 7, "1w": 8, "1d": 10}
_STRENGTH_RANK = {"STRONG": 2, "MEDIUM": 1}


def _signal_priority(s: Dict[str, Any]) -> Tuple[int, float, int]:
    return (
        _TF_RANK.get(str(s.get("timeframe") or ""), 0),
        float(s.get("confidence") or 0.0),
        _STRENGTH_RANK.get(str(s.get("strength") or "").upper(), 0),
    )


def dedupe_signals_by_asset(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Один сигнал на актив: приоритет старшему ТФ и большей уверенности."""
    best: Dict[str, Tuple[Tuple[int, float, int], Dict[str, Any]]] = {}
    for s in signals:
        asset = str(s.get("asset") or "")
        if not asset:
            continue
        pri = _signal_priority(s)
        if asset not in best or pri > best[asset][0]:
            best[asset] = (pri, s)
    return [v[1] for v in best.values()]


def build_message(signals: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("Торговые сигналы:")
    lines.append("")

    for s in signals:
        signal_type = str(s.get("signal_type") or "").upper()
        is_bounce = bool(s.get("bounce_mode"))

        if is_bounce:
            mode_prefix = "[⚡ РАЗВОРОТ / РИСК]"
        elif signal_type == "BUY":
            mode_prefix = "[СПОТ / ПОКУПКА]"
        else:
            mode_prefix = "[СПОТ / ПРОДАЖА]"

        strength = str(s.get("strength") or "").upper()
        if strength == "STRONG":
            strength_ru = "СИЛЬНЫЙ"
        elif strength == "MEDIUM":
            strength_ru = "СРЕДНИЙ"
        else:
            strength_ru = strength or ""

        entry = float(s.get("entry_price"))
        sl = float(s.get("stop_loss"))
        tp_list = s.get("take_profit", []) or []

        primary_tp_level = None
        primary_rr: Optional[float] = None

        for tp in tp_list:
            if not isinstance(tp, dict):
                continue
            level = tp.get("level")
            if not isinstance(level, (int, float)):
                continue
            if primary_tp_level is None:
                primary_tp_level = float(level)
                if s.get("signal_type") == "BUY":
                    reward = level - entry
                    risk = entry - sl
                else:
                    reward = entry - level
                    risk = sl - entry
                if risk > 0 and reward > 0:
                    primary_rr = reward / risk
                break

        rr_text = ""
        if primary_rr is not None:
            rr_text = f"R/R (к TP1): {primary_rr:.2f}"
        tp_levels = ", ".join(
            f"{fmt_price(tp.get('level'))} (p={tp.get('probability', 0):.2f})"
            for tp in s.get("take_profit", [])
            if isinstance(tp, dict) and "level" in tp
        )

        lines.append(
            f"{mode_prefix} {s['asset']} {s['timeframe']}: "
            f"{signal_type} ({strength_ru}) "
            f"(conf={s['confidence']:.2f})"
        )

        if primary_tp_level is not None:
            lines.append(
                f"СПОТ: цена входа {fmt_price(entry)}, план закрытия {fmt_price(primary_tp_level)}"
            )
        else:
            lines.append(f"СПОТ: цена входа {fmt_price(entry)}")

        lines.append(
            f"Вход: {fmt_price(s['entry_price'])}, SL: {fmt_price(s['stop_loss'])}"
        )
        if rr_text:
            lines.append(rr_text)
        if tp_levels:
            lines.append(f"TP: {tp_levels}")

        sr_bits: List[str] = []
        sup = s.get("support_level")
        res = s.get("resistance_level")
        if sup is not None:
            try:
                sr_bits.append(f"поддержка {fmt_price(float(sup))}")
            except (TypeError, ValueError):
                sr_bits.append(f"поддержка {sup}")
        if res is not None:
            try:
                sr_bits.append(f"сопротивление {fmt_price(float(res))}")
            except (TypeError, ValueError):
                sr_bits.append(f"сопротивление {res}")
        vc = s.get("volume_confirmation")
        if vc is True:
            sr_bits.append("объём выше среднего (подтверждение)")
        elif vc is False:
            sr_bits.append("объём без всплеска")
        if sr_bits:
            lines.append("📍 " + " | ".join(sr_bits))

        # контекст: RSI, EMA тренд, MACD
        ctx_parts = []
        rsi_val = s.get("rsi")
        rsi_zone = str(s.get("rsi_zone") or "").upper()
        if isinstance(rsi_val, (int, float)):
            rsi_zone_ru = {"OVERBOUGHT": "перекуплен", "OVERSOLD": "перепродан"}.get(rsi_zone, "нейтрален")
            ctx_parts.append(f"RSI {rsi_val:.1f} ({rsi_zone_ru})")

        ema_trend = str(s.get("ema_trend") or "").upper()
        if ema_trend in ("BULLISH", "BEARISH"):
            ema_ru = "тренд ▲" if ema_trend == "BULLISH" else "тренд ▼"
            ctx_parts.append(f"EMA {ema_ru}")

        macd_sig = str(s.get("macd_signal") or "").upper()
        if macd_sig in ("BUY", "SELL"):
            macd_ru = "бычий" if macd_sig == "BUY" else "медвежий"
            ctx_parts.append(f"MACD {macd_ru}")

        if ctx_parts:
            lines.append("📊 " + " | ".join(ctx_parts))

        # Блок OVERSOLD_BOUNCE: предупреждение и подтвердители
        if is_bounce:
            warning = s.get("warning") or "⚠️ Контрарианский сигнал: строгий стоп."
            lines.append(warning)
            confirmators = s.get("bounce_confirmators") or []
            if confirmators:
                lines.append("✅ Подтвердители: " + " | ".join(confirmators))

        # Блок рыночного настроения (L/S Ratio, OI, Funding Rate)
        sentiment = s.get("sentiment") or {}
        if sentiment:
            sent_label = str(sentiment.get("sentiment") or "NEUTRAL")
            long_ratio = sentiment.get("long_ratio")
            oi_change = sentiment.get("oi_change_pct")
            funding = sentiment.get("funding_rate")
            conf_delta = float(sentiment.get("confidence_delta") or 0.0)

            sent_emoji = {
                "CROWDED_LONG": "🔴",
                "LONG_HEAVY": "🟡",
                "NEUTRAL": "⚪",
                "SHORT_HEAVY": "🟡",
                "CROWDED_SHORT": "🟢",
            }.get(sent_label, "⚪")

            sent_parts: List[str] = [f"{sent_emoji} {sent_label}"]
            if long_ratio is not None:
                sent_parts.append(f"L/S {long_ratio * 100:.0f}% лонги")
            if oi_change is not None:
                sent_parts.append(f"OI {oi_change:+.1f}%")
            if funding is not None:
                sent_parts.append(f"FR {funding * 100:+.4f}%")
            if conf_delta != 0.0:
                sent_parts.append(f"Δconf {conf_delta:+.2f}")

            lines.append("🧭 " + " | ".join(sent_parts))

            # Заметки (одна-две строки объяснений)
            for note in (sentiment.get("notes") or [])[:3]:
                lines.append(f"   └─ {note}")

        lines.append("")

    return "\n".join(lines).strip()


def send_telegram_message(token: str, chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    resp = requests.post(url, json=payload, timeout=10)
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"Ошибка при отправке в Telegram: {exc}, ответ: {resp.text}")


def load_subscribers(path: Path) -> Set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return set()
    if not isinstance(data, list):
        return set()
    return {int(x) for x in data}


def save_subscribers(path: Path, chat_ids: Set[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(sorted(chat_ids), f, ensure_ascii=False, indent=2)


def poll_updates(token: str, offset: Optional[int] = None, timeout_s: int = 30) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params: Dict[str, Any] = {"timeout": timeout_s}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(url, params=params, timeout=timeout_s + 5)
    resp.raise_for_status()
    return resp.json()


def listen_for_subscribers(token: str, subscribers_path: Path) -> None:
    subscribers = load_subscribers(subscribers_path)
    print(f"Текущих подписчиков: {len(subscribers)}")

    last_update_id: Optional[int] = None

    while True:
        try:
            data = poll_updates(token, offset=(last_update_id + 1) if last_update_id is not None else None)
        except Exception as exc:
            print(f"Ошибка запроса getUpdates: {exc}")
            time.sleep(5)
            continue

        if not data.get("ok"):
            print(f"Ответ Telegram не ok: {data}")
            time.sleep(5)
            continue

        updates = data.get("result", [])
        if not updates:
            continue

        for update in updates:
            last_update_id = max(last_update_id or 0, update.get("update_id", 0))

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            text = (message.get("text") or "").strip()

            if not isinstance(chat_id, int) or not text:
                continue

            if text.startswith("/start"):
                if chat_id not in subscribers:
                    subscribers.add(chat_id)
                    save_subscribers(subscribers_path, subscribers)
                    print(f"Добавлен подписчик: {chat_id}")
                try:
                    send_telegram_message(token, chat_id, "Вы подписаны на сигналы. Как только появится новый сигнал BUY, он придёт сюда.")
                except Exception as exc:
                    print(f"Не удалось отправить приветственное сообщение: {exc}")
            elif text.startswith("/stop"):
                if chat_id in subscribers:
                    subscribers.remove(chat_id)
                    save_subscribers(subscribers_path, subscribers)
                    print(f"Удалён подписчик: {chat_id}")
                try:
                    send_telegram_message(token, chat_id, "Вы отписаны от сигналов.")
                except Exception as exc:
                    print(f"Не удалось отправить сообщение об отписке: {exc}")


def broadcast_signals(token: str, subscribers_path: Path, signals_file: Path, args: argparse.Namespace) -> None:
    subscribers = load_subscribers(subscribers_path)
    if not subscribers:
        print("Подписчиков нет, рассылать некому.")
        return

    # поддержка нескольких файлов через запятую
    signal_files = [Path(f.strip()) for f in str(signals_file).split(",") if f.strip()]
    data: Dict[str, Any] = {}
    for sf in signal_files:
        try:
            chunk = load_signals(sf)
            data.update(chunk)
        except FileNotFoundError:
            print(f"Файл не найден, пропускаем: {sf}")

    asset_filter = (
        [a.strip() for a in args.asset.split(",") if a.strip()]
        if args.asset
        else None
    )
    timeframes_filter = (
        [t.strip() for t in args.timeframes.split(",") if t.strip()]
        if args.timeframes
        else None
    )

    signal_types = ["BUY", "SELL"] if args.include_sell else ["BUY"]

    filtered_signals = dedupe_signals_by_asset(
        collect_buy_signals(
            data=data,
            min_confidence=args.min_confidence,
            asset_filter=asset_filter,
            timeframes_filter=timeframes_filter,
            allowed_types=signal_types,
        )
    )

    if not filtered_signals:
        print("Нет сигналов, удовлетворяющих фильтрам.")
        return

    today = date.today().isoformat()
    daily_sends = load_daily_sends(TELEGRAM_DAILY_SENDS_PATH)
    floor = max(float(args.min_confidence), MIN_SIGNAL_CONFIDENCE_REPEAT)
    new_signals: List[Dict[str, Any]] = []
    for s in filtered_signals:
        if float(s.get("confidence") or 0.0) < floor:
            continue
        k = daily_quota_key(
            str(s["asset"]), str(s["timeframe"]), str(s.get("signal_type") or ""), today
        )
        if daily_sends.get(k, 0) >= MAX_SIGNAL_SENDS_PER_DAY:
            continue
        new_signals.append(s)

    if not new_signals:
        print(
            "Нет сигналов для рассылки: дневной лимит "
            f"{MAX_SIGNAL_SENDS_PER_DAY} на актив/ТФ/тип или порог уверенности < {floor:.0%}."
        )
        return

    # сохраняем активные сигналы для последующего трекинга TP/SL
    active_signals_path = Path("data/active_signals.json")
    save_active_signals(active_signals_path, new_signals)

    message = build_message(new_signals)

    sent = 0
    for chat_id in subscribers:
        try:
            send_telegram_message(token=token, chat_id=chat_id, text=message)
            sent += 1
        except Exception as exc:
            print(f"Ошибка отправки подписчику {chat_id}: {exc}")

    if sent > 0:
        for s in new_signals:
            k = daily_quota_key(
                str(s["asset"]),
                str(s["timeframe"]),
                str(s.get("signal_type") or ""),
                today,
            )
            daily_sends[k] = daily_sends.get(k, 0) + 1
        save_daily_sends(TELEGRAM_DAILY_SENDS_PATH, daily_sends)

    print(f"Отправлено {len(new_signals)} сигналов {sent} подписчикам.")


def build_pump_message(pump_signals: List[Dict[str, Any]]) -> str:
    """Форматирует памп-сигналы альтов для Telegram."""
    lines: List[str] = [f"🎯 Памп-сигналы альтов — {len(pump_signals)} шт.", ""]

    for i, s in enumerate(pump_signals):
        asset = s.get("asset", "?")
        sig_type = s.get("signal_type", "PUMP")
        strength = str(s.get("strength") or "").upper()
        conf = float(s.get("confidence") or 0)
        price = s.get("current_price")
        vol_ratio = s.get("volume_ratio", 0)
        rsi = s.get("rsi")
        phase1: List[str] = s.get("phase1") or []
        phase2: List[str] = s.get("phase2") or []
        setup_signals: List[str] = s.get("signals") or []

        strength_emoji = {"STRONG": "🔥", "MEDIUM": "⚡", "WEAK": "📡"}.get(strength, "📡")
        strength_ru = {"STRONG": "СИЛЬНЫЙ", "MEDIUM": "СРЕДНИЙ", "WEAK": "СЛАБЫЙ"}.get(strength, strength)

        if i > 0:
            lines.append("──────────────────")

        if sig_type == "PRE_PUMP":
            lines.append(f"⚡ [PRE-PUMP] {asset} | {strength_ru} (conf={conf:.0%})")
            if price is not None:
                lines.append(f"💰 Цена: {fmt_price(price)}")
            if rsi is not None:
                lines.append(f"📊 RSI: {rsi:.1f}")
            if setup_signals:
                lines.append("🔍 Setup: " + " | ".join(setup_signals))
            lines.append("👁 Наблюдение — не торговый сигнал")
            risk_note = session_risk_note()
            if risk_note:
                lines.append(risk_note)
        else:
            lines.append(f"{strength_emoji} [PUMP] {asset} | {strength_ru} (conf={conf:.0%})")
            if price is not None:
                lines.append(f"💰 Цена: {fmt_price(price)}")
            vol_line = f"📊 Объём: {vol_ratio:.1f}x"
            if rsi is not None:
                vol_line += f" | RSI: {rsi:.1f}"
            lines.append(vol_line)
            if phase1:
                lines.append("📦 Накопление: " + " | ".join(phase1))
            if phase2:
                lines.append("🚀 Прорыв: " + " | ".join(phase2))

    return "\n".join(lines).strip()


def build_accumulation_message(signals: List[Dict[str, Any]]) -> str:
    """Форматирует сигналы накопления для Telegram."""
    lines: List[str] = [f"📦 Накопление 1d — {len(signals)} шт. (возможный памп позже)", ""]

    for i, s in enumerate(signals):
        asset = s.get("asset", "?")
        strength = str(s.get("strength") or "").upper()
        conf = float(s.get("confidence") or 0)
        price = s.get("current_price")
        rsi = s.get("rsi")
        zone_low = s.get("zone_low")
        zone_high = s.get("zone_high")
        sideways_pct = s.get("sideways_range_pct", 0)
        absorption = s.get("absorption_candles", 0)
        sig_list: List[str] = s.get("signals") or []

        strength_emoji = {"STRONG": "🔥", "MEDIUM": "⚡", "WEAK": "👀"}.get(strength, "👀")
        strength_ru = {"STRONG": "СИЛЬНОЕ", "MEDIUM": "СРЕДНЕЕ", "WEAK": "СЛАБОЕ"}.get(strength, strength)

        if i > 0:
            lines.append("──────────────────")
        lines.append(f"{strength_emoji} {asset} | {strength_ru} (conf={conf:.0%})")
        if price is not None:
            lines.append(f"💰 Цена: {fmt_price(price)}")
        if zone_low and zone_high:
            lines.append(f"📊 Зона: {fmt_price(zone_low)} – {fmt_price(zone_high)} (±{sideways_pct:.1f}%)")
        if rsi is not None:
            lines.append(f"📈 RSI: {rsi:.1f} | Абсорбция: {absorption} св.")
        if sig_list:
            for sig in sig_list:
                lines.append(f"  ✅ {html.escape(sig)}")
        lines.append("⏳ Вход — после пробоя зоны на 4h/1h")

    return "\n".join(lines).strip()


def broadcast_accumulation_signals(
    token: str,
    subscribers_path: Path,
    signals: List[Dict[str, Any]],
    min_confidence: float = 0.55,
) -> None:
    """Рассылает сигналы накопления подписчикам."""
    subscribers = load_subscribers(subscribers_path)
    if not subscribers:
        return

    filtered = [s for s in signals if float(s.get("confidence") or 0) >= min_confidence]
    if not filtered:
        print("Нет сигналов накопления с достаточной уверенностью.")
        return

    today = date.today().isoformat()
    daily_sends = load_daily_sends(TELEGRAM_DAILY_SENDS_PATH)
    new_signals: List[Dict[str, Any]] = []
    for s in filtered:
        k = daily_quota_key(str(s.get("asset", "")), "1d", "ACCUMULATION", today)
        if daily_sends.get(k, 0) < MAX_SIGNAL_SENDS_PER_DAY:
            new_signals.append(s)

    if not new_signals:
        print("Накопление: дневной лимит исчерпан.")
        return

    message = build_accumulation_message(new_signals)
    sent = 0
    for chat_id in subscribers:
        try:
            send_telegram_message(token=token, chat_id=chat_id, text=message)
            sent += 1
        except Exception as exc:
            print(f"Ошибка отправки накопления подписчику {chat_id}: {exc}")

    if sent > 0:
        for s in new_signals:
            k = daily_quota_key(str(s.get("asset", "")), "1d", "ACCUMULATION", today)
            daily_sends[k] = daily_sends.get(k, 0) + 1
        save_daily_sends(TELEGRAM_DAILY_SENDS_PATH, daily_sends)

    print(f"Отправлено {len(new_signals)} сигналов накопления {sent} подписчикам.")


def broadcast_pump_signals(
    token: str,
    subscribers_path: Path,
    pump_signals: List[Dict[str, Any]],
    min_confidence: float = 0.55,
) -> None:
    """Рассылает памп-сигналы альтов всем подписчикам."""
    subscribers = load_subscribers(subscribers_path)
    if not subscribers:
        return

    filtered = [s for s in pump_signals if float(s.get("confidence") or 0) >= min_confidence]
    if not filtered:
        print("Нет памп-сигналов с достаточной уверенностью.")
        return

    today = date.today().isoformat()
    daily_sends = load_daily_sends(TELEGRAM_DAILY_SENDS_PATH)
    new_signals: List[Dict[str, Any]] = []
    for s in filtered:
        sig_type = s.get("signal_type", "PUMP")
        k = daily_quota_key(str(s.get("asset", "")), "1h", sig_type, today)
        if daily_sends.get(k, 0) < MAX_SIGNAL_SENDS_PER_DAY:
            new_signals.append(s)

    if not new_signals:
        print("Памп-сигналы: дневной лимит исчерпан.")
        return

    message = build_pump_message(new_signals)
    sent = 0
    for chat_id in subscribers:
        try:
            send_telegram_message(token=token, chat_id=chat_id, text=message)
            sent += 1
        except Exception as exc:
            print(f"Ошибка отправки памп-сигнала подписчику {chat_id}: {exc}")

    if sent > 0:
        for s in new_signals:
            sig_type = s.get("signal_type", "PUMP")
            k = daily_quota_key(str(s.get("asset", "")), "1h", sig_type, today)
            daily_sends[k] = daily_sends.get(k, 0) + 1
        save_daily_sends(TELEGRAM_DAILY_SENDS_PATH, daily_sends)

    print(f"Отправлено {len(new_signals)} памп-сигналов {sent} подписчикам.")


def build_spot_overview_message(results: Dict[str, Any]) -> Optional[str]:
    """Формирует обзор рынка с лучшими спот-входами."""
    lines: List[str] = []

    # Рыночный контекст
    ctx = results.get("_market_context") or {}
    fg = ctx.get("fear_greed") or {}
    btc_dom = ctx.get("btc_dominance") or {}

    fg_val = fg.get("value")
    fg_zone = fg.get("zone", "")
    fg_emoji = {
        "EXTREME_FEAR": "😱", "FEAR": "😰", "GREED": "🤑", "EXTREME_GREED": "🔥",
    }.get(fg_zone, "😐")

    dom_val = btc_dom.get("btc_dominance")
    season = btc_dom.get("season", "")
    season_ru = {"BTC_SEASON": "BTC-сезон", "ALT_SEASON": "Альтсезон"}.get(season, "нейтрально")
    mcap_chg = btc_dom.get("market_cap_change_24h_pct", 0)

    if fg_val is not None or dom_val is not None:
        lines.append("📊 Обзор рынка")
        lines.append("")
        lines.append(session_line())
        if fg_val is not None:
            lines.append(f"{fg_emoji} Fear & Greed: {fg_val}/100 ({fg_zone.replace('_', ' ').title()})")
        if dom_val is not None:
            lines.append(f"₿ BTC Dominance: {dom_val:.1f}% — {season_ru}")
        if mcap_chg:
            lines.append(f"💰 Капитализация 24ч: {mcap_chg:+.1f}%")
        lines.append("")

    # Спот-лонг качество входа
    spot = results.get("_spot_long_entry") or {}
    scenarios = spot.get("scenarios") or []
    active_spots = [s for s in scenarios if s.get("active")]

    # Multi-TF Confluence
    confluence_data: List[Dict[str, Any]] = []
    for asset_name in ("ETH", "SOL", "BTC", "BNB", "XLM"):
        asset_data = results.get(asset_name)
        if isinstance(asset_data, dict):
            conf = asset_data.get("_confluence")
            if isinstance(conf, dict):
                confluence_data.append({"asset": asset_name, **conf})

    if active_spots:
        lines.append("✅ BUY — Сигналы на вход (спот)")
        lines.append("")
        for s in active_spots:
            asset = s.get("asset", "?")
            conf_val = s.get("confidence", 0)

            asset_data = results.get(asset, {})
            tf_4h = asset_data.get("4h", {}) if isinstance(asset_data, dict) else {}

            price = tf_4h.get("current_price") if isinstance(tf_4h, dict) else None

            sig = tf_4h.get("signal") if isinstance(tf_4h, dict) else None
            if isinstance(sig, dict) and sig.get("signal_type") == "BUY":
                entry = sig.get("entry_price")
                sl = sig.get("stop_loss")
                tp_list = sig.get("take_profit") or []
                tp1 = None
                for tp in tp_list:
                    if isinstance(tp, dict) and isinstance(tp.get("level"), (int, float)):
                        tp1 = float(tp["level"])
                        break

                lines.append(f"🟢 BUY {asset} (conf={conf_val:.0%})")
                if entry is not None:
                    lines.append(f"  Вход: {fmt_price(entry)}")
                if sl is not None:
                    lines.append(f"  Стоп: {fmt_price(sl)}")
                if tp1 is not None:
                    lines.append(f"  Цель: {fmt_price(tp1)}")
                    if entry and sl and entry != sl:
                        rr = abs(tp1 - entry) / abs(entry - sl)
                        lines.append(f"  R/R: {rr:.2f}")
            else:
                lines.append(f"🟢 BUY {asset} (conf={conf_val:.0%})")
                if price is not None:
                    lines.append(f"  💰 Цена: {fmt_price(price)}")

            cf = next((c for c in confluence_data if c["asset"] == asset), None)
            if cf:
                dir_ru = {"BULLISH": "бычий", "BEARISH": "медвежий", "MIXED": "смешанный"}.get(cf["direction"], "?")
                lines.append(f"  MTF: {dir_ru} ({cf['score']:.0%})")

            lines.append("")
    else:
        lines.append("⏸ Нет сигналов на вход")
        lines.append("")

    if not lines:
        return None

    return "\n".join(lines).strip()


def broadcast_spot_overview(
    token: str,
    subscribers_path: Path,
    results: Dict[str, Any],
    max_per_day: int = 2,
) -> None:
    """Рассылает обзор рынка подписчикам, не чаще max_per_day раз в день."""
    subscribers = load_subscribers(subscribers_path)
    if not subscribers:
        return

    today = date.today().isoformat()
    quota_key = f"_market_overview|{today}"
    daily_sends = load_daily_sends(TELEGRAM_DAILY_SENDS_PATH)
    if daily_sends.get(quota_key, 0) >= max_per_day:
        print(f"Обзор рынка уже отправлен {max_per_day} раз сегодня, пропускаем.")
        return

    message = build_spot_overview_message(results)
    if not message:
        print("Нет данных для обзора рынка.")
        return

    sent = 0
    for chat_id in subscribers:
        try:
            send_telegram_message(token=token, chat_id=chat_id, text=message)
            sent += 1
        except Exception as exc:
            print(f"Ошибка отправки обзора подписчику {chat_id}: {exc}")

    if sent:
        daily_sends[quota_key] = daily_sends.get(quota_key, 0) + 1
        save_daily_sends(TELEGRAM_DAILY_SENDS_PATH, daily_sends)
        print(f"Отправлен обзор рынка {sent} подписчикам ({daily_sends[quota_key]}/{max_per_day} сегодня).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Отправка торговых сигналов из JSON в Telegram"
    )
    parser.add_argument(
        "--signals-file",
        type=str,
        default="data/signals.json",
        help="Путь к JSON файлу(ам) с сигналами, через запятую",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.7,
        help="Минимальная уверенность сигнала",
    )
    parser.add_argument(
        "--asset",
        type=str,
        default="",
        help="Фильтр по активу (например, ETH,XRP). Пусто = все",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default="",
        help="Фильтр по таймфреймам (например, 15m,4h). Пусто = все",
    )
    parser.add_argument(
        "--include-sell",
        action="store_true",
        help="Также отправлять SELL сигналы (по умолчанию только BUY)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["listen", "send"],
        default="send",
        help="Режим работы: listen — принимать /start и /stop, send — разослать текущие сигналы подписчикам",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    token = TELEGRAM_BOT_TOKEN

    subscribers_path = Path("data/telegram_subscribers.json")
    signals_path = Path(args.signals_file)

    if args.mode == "listen":
        listen_for_subscribers(token=token, subscribers_path=subscribers_path)
    else:
        broadcast_signals(
            token=token,
            subscribers_path=subscribers_path,
            signals_file=signals_path,
            args=args,
        )


if __name__ == "__main__":
    main()

