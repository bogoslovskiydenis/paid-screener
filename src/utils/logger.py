"""Утилиты для логирования."""
import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logger(name: str, log_file: str = "logs/screener.log", level: str = "INFO") -> logging.Logger:
    """Настраивает логгер с ротацией файлов."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))
    
    if logger.handlers:
        return logger
    
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    
    console_handler = logging.StreamHandler()
    
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

