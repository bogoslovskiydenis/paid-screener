#!/usr/bin/env python3
"""Точка входа — делегирует в run_real.main().

run_real.py — актуальная точка запуска скринера.
src/main.py (устаревший PaidScreener) удалён.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_real import main  # noqa: E402

if __name__ == "__main__":
    main()
