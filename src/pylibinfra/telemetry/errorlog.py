"""Диагностика OTel SDK в `logkit`: ошибки экспорта и внутренние сообщения с троттлингом."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pylibinfra.clockkit import Clock, Real
from pylibinfra.logkit import Logger

# SDK и экспортёры пишут диагностику в stdlib-логгеры этого пространства имён.
OTEL_LOGGER = "opentelemetry"

# Недоступный коллектор ошибается на каждом экспорте трёх сигналов.
ERROR_LOG_WINDOW = 60.0

# Тексты ошибок несут адреса и коды — множество ключей ничем не ограничено.
_MAX_KEYS = 256

_formatter = logging.Formatter()


def install_error_logging(
    log: Logger, *, window: float = ERROR_LOG_WINDOW, clock: Clock | None = None
) -> None:
    """Уводит диагностику OTel SDK в `log`: error → error, warning → warn, остальное → debug.

    `log` не должен нести OTel-sink: ошибка экспорта логов породила бы новую
    запись, новый экспорт и новую ошибку — по той же причине снимается
    `propagate` (корневой handler `logkit` этот sink несёт).
    """
    target = logging.getLogger(OTEL_LOGGER)
    for handler in list(target.handlers):
        if isinstance(handler, _Handler):
            target.removeHandler(handler)
    target.addHandler(_Handler(log, _Limiter(window, clock or Real())))
    target.propagate = False
    target.setLevel(logging.DEBUG)


class _Handler(logging.Handler):
    def __init__(self, log: Logger, limiter: _Limiter) -> None:
        super().__init__(level=logging.NOTSET)
        self._log = log
        self._limiter = limiter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if not self._limiter.allow(msg):
                return
            fields: dict[str, Any] = {"logger": record.name}
            if record.exc_info is not None:
                fields["exception"] = _formatter.formatException(record.exc_info)
            _method(self._log, record.levelno)(msg, **fields)
        except Exception:
            # Контракт logging.Handler: сбой доставки не поднимается в код SDK.
            self.handleError(record)


def _method(log: Logger, levelno: int) -> Callable[..., None]:
    if levelno >= logging.ERROR:
        return log.error
    if levelno >= logging.WARNING:
        return log.warn
    return log.debug


class _Limiter:
    """Пропускает ключ не чаще раза в `window` секунд."""

    def __init__(self, window: float, clock: Clock) -> None:
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, datetime] = {}

    def allow(self, key: str) -> bool:
        """`True` — ключ не встречался в текущем окне."""
        now = self._clock.now()
        with self._lock:
            last = self._seen.get(key)
            if last is not None and (now - last).total_seconds() < self._window:
                return False
            if len(self._seen) >= _MAX_KEYS:
                self._evict(now)
            self._seen[key] = now
            return True

    def _evict(self, now: datetime) -> None:
        for key, seen in list(self._seen.items()):
            if (now - seen).total_seconds() >= self._window:
                del self._seen[key]
        if len(self._seen) >= _MAX_KEYS:
            self._seen.clear()
