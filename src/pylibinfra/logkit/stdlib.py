"""Мост `logging` → `Logger`: записи сторонних библиотек в том же формате и уровне."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .config import Config, parse_level
from .logger import Logger

_formatter = logging.Formatter()


def capture_stdlib(logger: Logger, cfg: Config) -> None:
    """Уводит корневой `logging` в `logger`: uvicorn/grpc/aiogram пишут тем же форматом.

    Меняет глобальное состояние процесса: снимает уже установленные root-handler'ы
    и выставляет уровень корневого логгера по `LOG_LEVEL`. Зовётся один раз на старте.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(_Handler(logger))
    root.setLevel(parse_level(cfg.level))


class _Handler(logging.Handler):
    def __init__(self, logger: Logger) -> None:
        super().__init__(level=logging.NOTSET)
        self._logger = logger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            fields: dict[str, Any] = {"logger": record.name}
            if record.exc_info is not None:
                fields["exception"] = _formatter.formatException(record.exc_info)
            _method(self._logger, record.levelno)(record.getMessage(), **fields)
        except Exception:
            # Контракт logging.Handler: сбой доставки не поднимается в чужой код.
            self.handleError(record)


def _method(logger: Logger, levelno: int) -> Callable[..., None]:
    if levelno >= logging.ERROR:
        return logger.error
    if levelno >= logging.WARNING:
        return logger.warn
    if levelno >= logging.INFO:
        return logger.info
    return logger.debug
