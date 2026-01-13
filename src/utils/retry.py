"""Утилиты для повторных попыток."""
import time
import logging
from functools import wraps
from typing import Callable, Any


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """Декоратор для повторных попыток с экспоненциальной задержкой."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            delay = initial_delay
            last_exception = None
            
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time.sleep(delay)
                        delay *= backoff_factor
                    else:
                        logging.error(
                            f"All {max_attempts} attempts failed for {func.__name__}"
                        )
            
            raise last_exception
        
        return wrapper
    return decorator

