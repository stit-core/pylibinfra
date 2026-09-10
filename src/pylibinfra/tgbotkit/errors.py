"""Ошибки tgbotkit: невалидный конфиг, проводка runner'а, initData, состав платежа."""

from __future__ import annotations

from pylibinfra._internal import configkit


class TgbotkitError(Exception):
    """База ошибок кита (кроме `ConfigError` — та наследует `ValueError`)."""


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг бота: валит старт сервиса, а не первый апдейт."""


class RunnerError(TgbotkitError):
    """Проводка runner'а несобираема: сервер уже обслуживается чужим lifecycle."""


class InitDataError(TgbotkitError):
    """WebApp `initData` не прошла проверку подписи или свежести."""


class PaymentError(TgbotkitError):
    """Некорректный запрос платежа: состав чека или период подписки."""
