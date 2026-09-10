"""webapp-подсистема: проверка Telegram WebApp `initData`.

Канал доставки проверенных данных на бэкенд и связка с identity — в сервисе;
кит ничего не эмитит.
"""

from .init_data import DEFAULT_MAX_AGE, validate_init_data

__all__ = ["DEFAULT_MAX_AGE", "validate_init_data"]
