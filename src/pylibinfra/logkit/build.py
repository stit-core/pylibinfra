"""Сборка логгера: цепочка redact → метрика → ctx-поля → fan-out в оба sink'а."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from opentelemetry import trace
from opentelemetry._logs import LoggerProvider, LogRecord, NoOpLoggerProvider, SeverityNumber
from opentelemetry.metrics import Counter

from pylibinfra._internal import otelkit, requestid

from .attrs import registered
from .config import (
    DEBUG,
    ERROR,
    INFO,
    WARN,
    Config,
    ConfigError,
    RedactConfig,
    ServiceConfig,
    parse_format,
    parse_level,
)
from .logger import Logger
from .redact import Redactor, compile_rules

_KIT = "logkit"

_LEVEL_META: dict[int, tuple[str, str, SeverityNumber]] = {
    DEBUG: ("debug", "DEBUG", SeverityNumber.DEBUG),
    INFO: ("info", "INFO", SeverityNumber.INFO),
    WARN: ("warn", "WARN", SeverityNumber.WARN),
    ERROR: ("error", "ERROR", SeverityNumber.ERROR),
}


class Writer(Protocol):
    """Куда пишет локальный sink: `sys.stdout`, файл, `io.StringIO`."""

    def write(self, text: str, /) -> object:
        """Пишет готовую строку записи."""
        ...

    def flush(self) -> None:
        """Сбрасывает буфер: запись должна быть видна сразу."""
        ...


def new(
    cfg: Config,
    *,
    writer: Writer | None = None,
    redact: RedactConfig | None = None,
    otel: LoggerProvider | None = None,
    service: ServiceConfig | None = None,
) -> Logger:
    """Собирает независимый `Logger` по конфигу и опциям; невалидный конфиг — `ConfigError`.

    `writer` приоритетнее `LOG_SILENT`; `otel` добавляет второй sink в OTel Logs;
    `service` добавляет статические поля `service`/`env`/`version` в каждую запись.
    """
    try:
        level = parse_level(cfg.level)
        text_format = parse_format(cfg.format) == "text"
        redactor = (
            compile_rules(redact.patterns, redact.replace_with)
            if redact is not None and redact.enabled
            else None
        )
    except ValueError as exc:
        raise ConfigError(f"{_KIT}: invalid config: {exc}") from None

    target = writer if writer is not None else (None if cfg.silent else sys.stdout)
    local = _LocalSink(target, text=text_format) if target is not None else None
    remote = (
        _OTelSink(otel) if otel is not None and not isinstance(otel, NoOpLoggerProvider) else None
    )

    fields: dict[str, Any] = {}
    if service is not None:
        for key, value in (
            ("service", service.service),
            ("env", service.env),
            ("version", service.version),
        ):
            if value:
                fields[key] = value

    core = _Core(
        level=level,
        source=cfg.source,
        redactor=redactor,
        local=local,
        remote=remote,
    )
    return _Logger(core, fields)


class _Core:
    """Общая часть логгера и всех его `with_`-производных."""

    __slots__ = ("counter", "level", "local", "redactor", "remote", "source")

    def __init__(
        self,
        *,
        level: int,
        source: bool,
        redactor: Redactor | None,
        local: _LocalSink | None,
        remote: _OTelSink | None,
    ) -> None:
        self.level = level
        self.source = source
        self.redactor = redactor
        self.local = local
        self.remote = remote
        self.counter: Counter = otelkit.meter(_KIT).create_counter(
            "log_messages_total", description="Emitted log records by level"
        )


class _Logger:
    __slots__ = ("_core", "_fields")

    def __init__(self, core: _Core, fields: Mapping[str, Any]) -> None:
        self._core = core
        self._fields = fields

    def debug(self, msg: str, /, **kv: Any) -> None:
        self._log(DEBUG, msg, kv)

    def info(self, msg: str, /, **kv: Any) -> None:
        self._log(INFO, msg, kv)

    def warn(self, msg: str, /, **kv: Any) -> None:
        self._log(WARN, msg, kv)

    def error(self, msg: str, /, **kv: Any) -> None:
        self._log(ERROR, msg, kv)

    def with_(self, **kv: Any) -> Logger:
        core = self._core
        fields = dict(self._fields)
        fields.update(core.redactor.fields(kv) if core.redactor is not None else kv)
        return _Logger(core, fields)

    def enabled(self, level: str) -> bool:
        return parse_level(level) >= self._core.level

    def _log(self, level: int, msg: str, kv: Mapping[str, Any]) -> None:
        core = self._core
        if level < core.level:
            return
        name, text, severity = _LEVEL_META[level]
        core.counter.add(1, {"level": name})

        if core.redactor is not None:
            msg = core.redactor.text(msg)
            kv = core.redactor.fields(kv)
        fields = {**self._fields, **kv, **_ctx_attrs()}
        stamp = time.time_ns()

        if core.remote is not None:
            core.remote.emit(stamp, severity, text, msg, fields)
        if core.local is not None:
            record: dict[str, Any] = {"time": _stamp(stamp), "level": text, "msg": msg}
            if core.source:
                frame = sys._getframe(2)
                record["source"] = {
                    "function": frame.f_code.co_qualname,
                    "file": frame.f_code.co_filename,
                    "line": frame.f_lineno,
                }
            span = trace.get_current_span().get_span_context()
            # Собственные имена записи полем вызова не перекрываются: иначе
            # log.info("x", level="oops") подменил бы уровень для сборщика логов.
            taken = set(record) | ({"trace_id", "span_id"} if span.is_valid else set())
            record.update({key: value for key, value in fields.items() if key not in taken})
            if span.is_valid:
                record["trace_id"] = format(span.trace_id, "032x")
                record["span_id"] = format(span.span_id, "016x")
            core.local.emit(record)


class _LocalSink:
    __slots__ = ("_lock", "_text", "_writer")

    def __init__(self, writer: Writer, *, text: bool) -> None:
        self._writer = writer
        self._text = text
        self._lock = threading.Lock()

    def emit(self, record: Mapping[str, Any]) -> None:
        line = _render_text(record) if self._text else _render_json(record)
        with self._lock:
            self._writer.write(line + "\n")
            self._writer.flush()


class _OTelSink:
    __slots__ = ("_logger",)

    def __init__(self, provider: LoggerProvider) -> None:
        self._logger = provider.get_logger(otelkit.scope(_KIT), otelkit.version())

    def emit(
        self,
        stamp: int,
        severity: SeverityNumber,
        severity_text: str,
        msg: str,
        fields: Mapping[str, Any],
    ) -> None:
        self._logger.emit(
            LogRecord(
                timestamp=stamp,
                observed_timestamp=stamp,
                severity_number=severity,
                severity_text=severity_text,
                body=msg,
                attributes={k: v for k, v in fields.items() if v is not None},
            )
        )


def _ctx_attrs() -> dict[str, Any]:
    out: dict[str, Any] = {}
    request_id = requestid.current()
    if request_id:
        out["request_id"] = request_id
    for key, fn in registered():
        try:
            value = fn()
        except Exception:
            # Сломанный экстрактор не должен ронять вызов лога на горячем пути.
            value = None
        if value is not None:
            out[key] = value
    return out


def _stamp(stamp: int) -> str:
    return datetime.fromtimestamp(stamp / 1_000_000_000, tz=UTC).astimezone().isoformat()


def _render_json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _render_text(record: Mapping[str, Any]) -> str:
    return " ".join(f"{key}={_text_value(value)}" for key, value in record.items())


def _text_value(value: Any) -> str:
    if isinstance(value, Mapping | list | tuple):
        return _render_json(value)
    text = value if isinstance(value, str) else str(value)
    if not text or any(char in text for char in ' "='):
        return json.dumps(text, ensure_ascii=False)
    return text
