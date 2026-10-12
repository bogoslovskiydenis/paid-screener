# Paid Screener — что сделано в проекте

Крипто-скринер и торговый ассистент на Python. Парсит OHLCV с бирж (Binance/Bybit/OKX через ccxt), считает технический анализ, генерирует BUY/SELL-сигналы с entry/SL/TP, рассылает их в Telegram, отслеживает исход (TP/SL/трейлинг), ведёт журнал сделок в SQLite и умеет исполнять ордера на Binance Spot (paper и real режимы).

Актуально на: 2026-08-11.

---

## 1. Общая схема работы

```
config/config.yaml (активы, таймфреймы)
        │
run_real.py ──► ExchangeManager (ccxt) ──► свечи/стакан/funding/OI
        │
        ├─► Аналитика: индикаторы, уровни, паттерны, структура рынка
        ├─► SignalGenerator ──► BUY/SELL сигнал (entry, SL, TP[], confidence)
        ├─► data/signals.json + SQLite (data/screener.db)
        ├─► telegram_bot.py ──► рассылка подписчикам
        └─► binance_executor.py ──► ордер на Binance Spot (paper/real)
                                          │
track_signals.py ──► трекинг открытых сигналов (TP/SL/трейлинг) ──► журнал trades
```

## 2. Точки входа (корень проекта)

| Скрипт | Назначение |
|---|---|
| `run_real.py` | Главный движок: обход активов × таймфреймов, полный анализ, генерация сигналов, экспорт JSON, отправка в Telegram, опционально исполнение сделок. CLI: `--asset`, `--timeframes`, `--min-confidence` (0.7), `--export-json`, `--output`, `--limit` (500), `--loop`, `--loop-interval` (300), `--pump-scan` |
| `run.py` | Тонкая обёртка над `run_real.main` |
| `run_scanner.py` | Постоянный сканер: напрямую вызывает `run_iteration`/`broadcast_signals` (компоненты создаются один раз на весь процесс), пишет результат в `data/signals_spot.json`, рассылает в Telegram при изменении сигналов |
| `trade.py` | Ручная торговля на Binance Spot: `buy ASSET --usd --tp/--sl`, `sell`, `status`, `history`. Показывает сводку и просит подтверждение перед реальным ордером |
| `backtest.py` | Walk-forward бэктест SignalGenerator по истории Binance: симуляция SL/TP1/таймаут, таблицы калибровки confidence vs win rate, разрезы по ТФ и типам. Результат → `data/backtest_results.json` |
| `track_signals.py` | Трекер активных сигналов: проверка достижения TP/SL, трейлинг-стоп, закрытие, запись `Trade` в БД, отправка результата и дневной аналитики в Telegram |
| `telegram_bot.py` | Telegram-бот (подписки + все рассылки), см. раздел 6 |
| `liquidation_scan.py` | Скан уровней ликвидации фьючерсов: реальные ликвидации за 48ч + прогнозная тепловая карта каскада |
| `analytics_report.py` | Отчёт по закрытым сделкам из БД (win rate, PnL в разрезах) |
| `analyze_trades.py` | Сводка/экспорт журнала сделок из `data/screener.db` |
| `analyze_signals_stats.py` | Статистика по сохранённым сигналам (распределение confidence, R:R) |
| `record_trade.py` | Ручная запись сделки в журнал (сам считает `pnl_usd`, `pnl_rr`) |
| `reset_tracking.py` | Сброс: удаляет БД, `active_signals.json`, `trade_stats.json` |
| `test_binance.py` | Smoke-проверка соединения с Binance (публичный + приватный API) |

## 3. Аналитика (`src/analytics`)

### Генератор сигналов — `signals/generator.py`
`SignalGenerator.generate_signal(asset, timeframe, df)` — требует ≥100 свечей. Собирает: свечные паттерны, уровни S/R, «голову и плечи», RSI, пробои с volume confirmation, ATR, EMA, MACD, Volume Profile, Fibonacci, liquidity grab. Три стратегии входа по приоритету:
1. `_evaluate_signals()` — основная оценка по совокупности факторов;
2. `_check_oversold_bounce()` — контрарианский отскок от перепроданности;
3. `_check_pullback_entry()` — вход на откате в подтверждённом аптренде.

TP фильтруются по R:R (не ниже 2) и капятся по таймфрейму (`_MAX_TP_PCT`: 15m 2.5%, 1h 5%, 4h 10%, 1d 20%, 1w 30%, 1M 40%).

### Индикаторы (`indicators/`)
RSI (14), EMA (9/21/50 + кроссы), MACD (12/26/9 + дивергенции), ATR (14), VWAP, Volume Profile (POC/VAH/VAL), Fibonacci (ретрейсменты 0.236–0.786, «золотая зона» 0.618–0.786).

### Паттерны и уровни
- `candlestick/patterns.py` — hammer, engulfing, doji, shooting star, morning/evening star.
- `levels/support_resistance.py` — уровни S/R (мин. 2 касания), детект пробоев с подтверждением объёмом.
- `patterns/head_shoulders.py` — «голова и плечи» (bearish/bullish), neckline, % завершения.
- `patterns/chart_patterns.py` — double top/bottom, triangle, flag, pennant, wedge, rectangle.
- `market_structure.py` — swing points, BOS/CHoCH, order blocks, liquidity grab.
- `ict_detector.py` — FVG (Fair Value Gap) и liquidity sweep (ICT-концепции).

### Специализированные детекторы и фильтры
- `pump_detector.py` — `AltPumpDetector`: двухфазный детектор пампа альтов (накопление → прорыв EMA9), OBV-дивергенция, BB-squeeze, «высыхание» объёма.
- `accumulation_detector.py` — Wyckoff-подобный детектор накопления на дневном ТФ.
- `orderbook_analyzer.py` — анализ стакана: давление, Order Flow Imbalance, дисбаланс сделок, кандидаты в спуфинг.
- `liquidation_analyzer.py` — история OI, Long/Short ratio, детект каскадов ликвидаций, кластеры и прогнозная тепловая карта.
- `market_sentiment.py` — сентимент по OI / L-S ratio / funding rate.
- `multi_tf_confluence.py` — согласованность направления между таймфреймами (score, bullish/bearish TFs).
- `rotation_filters.py` — сценарии «перелива» капитала BTC→ETH→SOL.
- `entry_spot_filters.py` — скоринг качества входа в спот-лонг (якорный ТФ 1d, тайминг 4h).
- `context_bundles.py` — сводка по связанным инструментам (USDT-пара + кроссы + BTC).

## 4. Инфраструктура

### Парсеры (`src/parsers`)
`ExchangeManager` над парсерами бирж (Binance spot/futures реализован полностью): OHLCV, объёмы, funding rate, long/short ratio, open interest, стакан, последние сделки, топ USDT-пар, отсев стейблкоинов.

### Хранилище (`src/storage/database.py`)
SQLAlchemy + SQLite (`data/screener.db`). Таблицы: `candles`, `support_resistance_levels`, `patterns`, `signals`, `trades` (журнал сделок с PnL в USD и R), `breakouts`. Методы записи/чтения + `get_trade_analytics()`.

### Исполнение сделок (`src/trading/binance_executor.py`)
- **Paper-режим** (по умолчанию, `BINANCE_PAPER_TRADING=true`) — сделки только логируются в `data/paper_trades.json`.
- **Real-режим** — маркет-покупка с риск-сайзингом от дистанции до SL, затем OCO-ордер (лимитный TP + стоп-лосс). При отказе OCO — fallback на лимитный TP с WARNING.
- Учитывает биржевые фильтры (stepSize, minQty, minNotional, tickSize), округление через Decimal.
- Риск-параметры через env: `BINANCE_RISK_PCT` (1%), `BINANCE_MAX_POSITION_PCT` (20%), `BINANCE_TP_PCT`, `BINANCE_TRADE_TIMEFRAMES`.

### Утилиты (`src/utils`)
Загрузка конфига и .env, логгер с ротацией (`logs/screener.log`), retry с backoff, Fear & Greed Index, BTC Dominance, market cap через CoinGecko, адаптивное форматирование цен, торговые сессии по Киеву (азиатская/европейская/американская) с риск-заметками.

## 5. Конфигурация

- `config/config.yaml`:
  - **assets** — 20 USDT-пар (ETH, SOL, BTC, BNB, XLM, XRP, SUI, LTC, HBAR, ADA, LINK, AVAX, DOGE, DOT, ARB, NEAR, ATOM, TRX, GRAM, HYPE) + кроссы ETH/BTC, SOL/ETH, SOL/BTC, XLM/BTC;
  - **timeframes** — 1d, 3d, 1w, 1M;
  - **exchanges** — binance, bybit, okx;
  - **analysis** — min_candles 100, min_confidence 0.7, test_risk_usd 10;
  - **pump_scan / accumulation_scan** — параметры сканов (top_n, мин. объём $5M, мин. капитализация $50M, пороги RSI и score).
- `.env` (см. `.env.example`): `TELEGRAM_BOT_TOKEN`, `BINANCE_API_KEY`, `BINANCE_API_SECRET`; дополнительно код читает `DATABASE_URL`, `LOG_FILE`, `LOG_LEVEL`, `BINANCE_PAPER_TRADING` и риск-параметры из раздела 4.

## 6. Telegram-бот

Команды пользователя: `/start` (подписка), `/stop` (отписка). Подписчики — `data/telegram_subscribers.json`.

Рассылки:
- **Торговые сигналы** (BUY, опционально SELL) — с дедупликацией по активу (приоритет старших ТФ и силы сигнала) и антиспамом: максимум 2 отправки в день на ключ актив/ТФ/тип, повтор только при confidence ≥ 0.7. Есть фильтр: блокировка BUY по альтам при медвежьем BTC.
- **Памп-сигналы** и **сигналы накопления** (min_confidence 0.55).
- **Обзор спот-рынка** — раз в час.
- **Уровни ликвидации** ETH и BTC — раз в 4 часа.
- **Уведомление об исполненном ордере** (из executor'а).
- **Закрытие сигнала** по TP/SL/трейлингу и **дневная аналитика** (из трекера).

В сообщения подмешивается контекст торговой сессии.

## 7. Тесты

Pytest в `tests/` (синтетические датафреймы в `conftest.py`):
- `test_indicators.py` — RSI, EMA, MACD, ATR (значения, диапазоны, сигналы, короткие данные);
- `test_signal_generator.py` — структура сигнала, SL/TP по правильную сторону от entry, сортировка TP, фильтр min_confidence, R:R ≥ 2;
- `test_filters.py` — скоринг спот-лонга (блокировки по EMA/RSI/MACD/confidence) и хелперы.

## 8. Данные

- `data/screener.db` — SQLite со всеми таблицами.
- `data/signals.json` — последний результат анализа; `active_signals.json`, `trade_stats.json` — состояние трекера; `paper_trades.json` — бумажные сделки; `telegram_subscribers.json`, `telegram_daily_sends.json` — состояние бота; `backtest_*.json` — результаты бэктестов.
- `data/signals_spot.json` — вывод постоянного сканера `run_scanner.py`.
- `logs/screener.log` — лог с ротацией.
- `requirements.lock` — зафиксированные версии зависимостей (`pip freeze`).

## 9. История улучшений

Журнал ведётся в `IMPROVEMENTS.md`: исправлены баги (двойной check_breakout, отрицательная вероятность TP, несортированные TP, некорректный ранг ТФ и др.), проведена декомпозиция `main()` на компоненты, удалён устаревший `src/main.py`, токен Telegram вынесен в `.env`. Открытые пункты: параллельная загрузка данных, вынос ключей бирж только в env, MTF-подтверждение на уровне генератора.
