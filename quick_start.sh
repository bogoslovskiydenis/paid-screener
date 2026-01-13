#!/bin/bash
# Быстрый запуск Paid Screener с реальными данными Binance

echo "Paid Screener - Быстрый запуск"
echo "================================"
echo ""

# Проверка зависимостей
echo "Проверка зависимостей..."
python3 -c "import ccxt" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠ ccxt не установлен. Устанавливаю зависимости..."
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "✗ Ошибка установки зависимостей"
        echo "Установите вручную: pip install -r requirements.txt"
        exit 1
    fi
fi

echo "✓ Зависимости установлены"
echo ""

# Запуск анализа
echo "Запуск анализа ETH/USDT на таймфрейме 4h..."
python3 run_real.py --asset ETH --timeframes 4h --export-json --output signals_eth_4h.json

echo ""
echo "Готово! Результаты в signals_eth_4h.json"

