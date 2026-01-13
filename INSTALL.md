# Установка зависимостей

Для работы с реальными данными Binance необходимо установить зависимости:

```bash
pip install -r requirements.txt
```

Или установить вручную:

```bash
pip install ccxt pyyaml pandas numpy scipy sqlalchemy pydantic pydantic-settings python-dotenv
```

## Проверка установки

После установки проверьте подключение к Binance:

```bash
python3 test_binance.py
```

## Запуск с реальными данными

```bash
python3 run_real.py --asset ETH --timeframes 4h,1d --export-json
```

