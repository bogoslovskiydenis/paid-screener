#!/usr/bin/env python3
"""Проверка импортов и структуры проекта."""
import sys
from pathlib import Path

print("Проверка структуры проекта...")
print(f"Python версия: {sys.version}\n")

# Проверка структуры директорий
required_dirs = [
    "src",
    "src/parsers",
    "src/analytics",
    "src/analytics/candlestick",
    "src/analytics/patterns",
    "src/analytics/levels",
    "src/analytics/signals",
    "src/storage",
    "src/utils",
    "config"
]

print("Директории:")
for dir_path in required_dirs:
    path = Path(dir_path)
    exists = path.exists() and path.is_dir()
    status = "✓" if exists else "✗"
    print(f"  {status} {dir_path}")

# Проверка ключевых файлов
required_files = [
    "src/main.py",
    "src/parsers/base.py",
    "src/parsers/binance_parser.py",
    "src/parsers/exchange_manager.py",
    "src/storage/database.py",
    "src/analytics/candlestick/patterns.py",
    "src/analytics/levels/support_resistance.py",
    "src/analytics/patterns/head_shoulders.py",
    "src/analytics/signals/generator.py",
    "config/config.yaml",
    "requirements.txt"
]

print("\nФайлы:")
for file_path in required_files:
    path = Path(file_path)
    exists = path.exists() and path.is_file()
    status = "✓" if exists else "✗"
    print(f"  {status} {file_path}")

# Проверка зависимостей
print("\nПроверка зависимостей:")
dependencies = [
    "yaml",
    "pandas",
    "numpy",
    "scipy",
    "sqlalchemy",
    "ccxt",
    "pydantic",
    "pydantic_settings"
]

missing = []
for dep in dependencies:
    try:
        __import__(dep)
        print(f"  ✓ {dep}")
    except ImportError:
        print(f"  ✗ {dep} - не установлен")
        missing.append(dep)

if missing:
    print(f"\n⚠ Необходимо установить: {', '.join(missing)}")
    print("Выполните: pip install -r requirements.txt")
else:
    print("\n✓ Все зависимости установлены!")

# Проверка синтаксиса основных файлов
print("\nПроверка синтаксиса:")
python_files = [
    "src/main.py",
    "src/parsers/base.py",
    "src/storage/database.py"
]

for file_path in python_files:
    path = Path(file_path)
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                code = f.read()
            compile(code, str(path), 'exec')
            print(f"  ✓ {file_path}")
        except SyntaxError as e:
            print(f"  ✗ {file_path} - синтаксическая ошибка: {e}")

print("\nПроверка завершена!")

