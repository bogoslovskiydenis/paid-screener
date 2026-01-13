# Запуск с реальными данными Binance

## Установка зависимостей

```bash
pip install -r requirements.txt
```

## Проверка подключения к Binance

```bash
python3 test_binance.py
```

## Запуск анализа

### Базовый запуск (ETH, 4h)

```bash
python3 run_real.py --asset ETH --timeframes 4h
```

### Анализ нескольких таймфреймов

```bash
python3 run_real.py --asset ETH --timeframes 15m,4h,1d
```

### Анализ SOL

```bash
python3 run_real.py --asset SOL --timeframes 4h,1d
```

### С экспортом в JSON

```bash
python3 run_real.py --asset ETH --timeframes 4h,1d --export-json --output signals.json
```

### С настройкой минимальной уверенности

```bash
python3 run_real.py --asset ETH --timeframes 4h --min-confidence 0.7
```

## Параметры

- `--asset` - актив (ETH, SOL)
- `--timeframes` - таймфреймы через запятую (15m, 4h, 1d, 1M)
- `--min-confidence` - минимальная уверенность сигнала (0.0-1.0)
- `--export-json` - экспортировать результаты в JSON
- `--output` - путь к файлу вывода
- `--limit` - количество свечей для загрузки (по умолчанию 500)
- `--config` - путь к конфигурационному файлу

## Примеры использования

### Полный анализ ETH на всех таймфреймах

```bash
python3 run_real.py --asset ETH --timeframes 15m,4h,1d,1M --export-json
```

### Анализ с высокой уверенностью

```bash
python3 run_real.py --asset ETH --timeframes 4h --min-confidence 0.8 --export-json
```

### Загрузка большего количества данных

```bash
python3 run_real.py --asset ETH --timeframes 1d --limit 1000
```

