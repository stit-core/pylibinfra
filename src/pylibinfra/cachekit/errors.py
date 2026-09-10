"""Исключения cachekit: конфиг, версия сервера, нечитаемое значение."""

from __future__ import annotations

from pylibinfra._internal import configkit


class CacheError(Exception):
    """База ошибок кита.

    Отказы драйвера (`redis.exceptions.ConnectionError`, `TimeoutError`,
    `ResponseError`) кит не оборачивает — они поднимаются как есть, чтобы
    консьюмер различал их `except`'ом.
    """


class ConfigError(configkit.ConfigError, CacheError):
    """Невалидный `Config`: адрес не парсится, размер пула/ретраи/таймаут вне диапазона."""


class ServerVersionError(CacheError):
    """Сервер старше Redis 7.0 либо не отдаёт `redis_version` в `INFO server`."""


class CacheDecodeError(CacheError):
    """Значение по ключу не разбирается как JSON — это отказ, а не промах."""
