#!/usr/bin/env python3
"""Простой Telegram-бот для отправки сигналов BUY из JSON-файла."""

import argparse
import json
import math
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

TELEGRAM_BOT_TOKEN = "8339654755:AAFa4GbSyOk5rvtlw4RZY3h7l2M_4pyKxns"


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
                }
            )

    return results


def _fmt_price(x: Any) -> str:
    """Цены для USDT и для малых кросс-пар (например XLM/BTC — не обрезать до 0.0000)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    av = abs(v)
    if av == 0:
        return "0"
    if av >= 100:
        return f"{v:.2f}"
    if av >= 1:
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"
    if av >= 0.01:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"
    nd = min(12, max(6, int(math.ceil(-math.log10(av))) + 3))
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return s if s else "0"


def build_message(signals: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("Торговые сигналы:")
    lines.append("")

    for s in signals:
        signal_type = str(s.get("signal_type") or "").upper()

        if signal_type == "BUY":
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
            f"{_fmt_price(tp.get('level'))} (p={tp.get('probability', 0):.2f})"
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
                f"СПОТ: цена входа {_fmt_price(entry)}, план закрытия {_fmt_price(primary_tp_level)}"
            )
        else:
            lines.append(f"СПОТ: цена входа {_fmt_price(entry)}")

        lines.append(
            f"Вход: {_fmt_price(s['entry_price'])}, SL: {_fmt_price(s['stop_loss'])}"
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
                sr_bits.append(f"поддержка {_fmt_price(float(sup))}")
            except (TypeError, ValueError):
                sr_bits.append(f"поддержка {sup}")
        if res is not None:
            try:
                sr_bits.append(f"сопротивление {_fmt_price(float(res))}")
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

    filtered_signals = collect_buy_signals(
        data=data,
        min_confidence=args.min_confidence,
        asset_filter=asset_filter,
        timeframes_filter=timeframes_filter,
        allowed_types=signal_types,
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Отправка торговых сигналов из JSON в Telegram"
    )
    parser.add_argument(
        "--signals-file",
        type=str,
        default="signals.json",
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

