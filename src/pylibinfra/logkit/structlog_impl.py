"""Конфигурация structlog → stdlib logging: структурированный JSON-вывод в
stdout (при LOG_STDOUT). Trace-context (trace_id/span_id из ambient-span'а) и
счётчик log_messages_total — processor'ы в общей цепочке; оба используют
vendor-neutral OTel-API и без выставленного provider'а — no-op.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter
from structlog.types import EventDict, Processor, WrappedLogger

from .config import Config
from .logger import Logger
from .redact import RedactConfig, build_redactor

_log_counter: Counter | None = None

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

# Стандартные поля logging.LogRecord — всё остальное в __dict__ это наши extras.
_STD_RECORD_ATTRS = frozenset(
    logging.makeLogRecord({}).__dict__.keys() | {"message", "asctime", "taskName"}
)


def _inject_trace(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def _count(_: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    if _log_counter is not None:
        level = str(event_dict.get("level", method_name))
        _log_counter.add(1, {"level": level})
    return event_dict


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STD_RECORD_ATTRS:
                out[key] = value
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def configure(
    cfg: Config,
    *,
    redact_cfg: RedactConfig | None = None,
) -> None:
    global _log_counter
    _log_counter = metrics.get_meter("app").create_counter("log_messages_total")

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _inject_trace,
    ]
    if redact_cfg is not None:
        redactor = build_redactor(redact_cfg)
        if redactor is not None:
            processors.append(redactor)
    processors.append(_count)
    processors.append(structlog.stdlib.render_to_log_kwargs)

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    if cfg.stdout:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(_JsonFormatter())
        root.addHandler(stream)
    root.setLevel(_LEVELS.get(cfg.level.lower(), logging.INFO))


class _StructLogger:
    def __init__(self, log: Any) -> None:
        self._log = log

    def debug(self, msg: str, **kv: Any) -> None:
        self._log.debug(msg, **kv)

    def info(self, msg: str, **kv: Any) -> None:
        self._log.info(msg, **kv)

    def warn(self, msg: str, **kv: Any) -> None:
        self._log.warning(msg, **kv)

    def error(self, msg: str, **kv: Any) -> None:
        self._log.error(msg, **kv)

    def with_(self, **kv: Any) -> Logger:
        return _StructLogger(self._log.bind(**kv))


def new_logger() -> Logger:
    return _StructLogger(structlog.get_logger())
