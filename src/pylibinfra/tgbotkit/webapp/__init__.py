"""webapp-подсистема: валидатор Telegram WebApp initData (HMAC-SHA256). Канал
доставки проверенных данных на бэкенд и identity-link — продуктовые, в шаблоне.
"""

from .init_data import InitDataError, validate_init_data

__all__ = ["InitDataError", "validate_init_data"]
