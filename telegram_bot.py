#!/usr/bin/env python3
"""Простой Telegram-бот для отправки сигналов BUY из JSON-файла."""

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

TELEGRAM_BOT_TOKEN = "8339654755:AAFa4GbSyOk5rvtlw4RZY3h7l2M_4pyKxns"


def load_signals(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Файл с сигналами не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def collect_buy_signals(
    data: Dict[str, Any],
    min_confidence: float,
    asset_filter: Optional[List[str]] = None,
    timeframes_filter: Optional[List[str]] = None,
    allowed_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for asset, tf_map in data.items():
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
            if confidence < min_confidence:
                continue

            if strength not in ("MEDIUM", "STRONG"):
                continue

            rsi_info = tf_data.get("rsi") or {}
            rsi_value = rsi_info.get("rsi")
            if isinstance(rsi_value, (int, float)):
                if signal_type == "SELL" and rsi_value < 30:
                    continue
                if signal_type == "BUY" and rsi_value > 70:
                    continue

            if timeframe == "1h" and signal_type == "SELL":
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
                }
            )

    return results


def build_message(signals: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("Торговые сигналы:")
    lines.append("")

    for s in signals:
        signal_type = str(s.get("signal_type") or "").upper()
        timeframe = str(s.get("timeframe") or "")

        if signal_type == "BUY":
            if timeframe in ("1h", "4h", "1d"):
                mode_prefix = "[СПОТ/ЛОНГ]"
            else:
                mode_prefix = "[ЛОНГ]"
        else:
            mode_prefix = "[ШОРТ]"

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

        primary_tp_level: float | None = None

        rr_values: List[float] = []
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
                rr_values.append(reward / risk)

        rr_text = ""
        if rr_values:
            best_rr = max(rr_values)
            rr_text = f"R/R: {best_rr:.2f}"
        tp_levels = ", ".join(
            f"{tp.get('level'):.4f} (p={tp.get('probability', 0):.2f})"
            for tp in s.get("take_profit", [])
            if isinstance(tp, dict) and "level" in tp
        )

        lines.append(
            f"{mode_prefix} {s['asset']} {s['timeframe']}: "
            f"{signal_type} ({strength_ru}) "
            f"(conf={s['confidence']:.2f})"
        )

        if signal_type == "BUY" and timeframe in ("1h", "4h", "1d"):
            if primary_tp_level is not None:
                lines.append(
                    f"СПОТ: цена входа {entry:.4f}, план закрытия {primary_tp_level:.4f}"
                )
            else:
                lines.append(f"СПОТ: цена входа {entry:.4f}")
        elif signal_type == "BUY":
            if primary_tp_level is not None:
                lines.append(
                    f"ФЬЮЧЕРС ЛОНГ: цена входа {entry:.4f}, цена выхода {primary_tp_level:.4f}"
                )
            else:
                lines.append(f"ФЬЮЧЕРС ЛОНГ: цена входа {entry:.4f}")
        else:
            if primary_tp_level is not None:
                lines.append(
                    f"ФЬЮЧЕРС ШОРТ: цена входа {entry:.4f}, цена выхода {primary_tp_level:.4f}"
                )
            else:
                lines.append(f"ФЬЮЧЕРС ШОРТ: цена входа {entry:.4f}")

        lines.append(
            f"Вход: {s['entry_price']:.4f}, SL: {s['stop_loss']:.4f}"
        )
        if rr_text:
            lines.append(rr_text)
        if tp_levels:
            lines.append(f"TP: {tp_levels}")
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

    data = load_signals(signals_file)

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

    message = build_message(filtered_signals)

    sent = 0
    for chat_id in subscribers:
        try:
            send_telegram_message(token=token, chat_id=chat_id, text=message)
            sent += 1
        except Exception as exc:
            print(f"Ошибка отправки подписчику {chat_id}: {exc}")

    print(f"Отправлено {len(filtered_signals)} сигналов {sent} подписчикам.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Отправка торговых сигналов из JSON в Telegram"
    )
    parser.add_argument(
        "--signals-file",
        type=str,
        default="signals.json",
        help="Путь к JSON файлу с сигналами (из run_real.py --export-json)",
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

