"""logkit — структурный logger поверх structlog с OTel-bridge'ом.

Каждый record уходит в OTel LoggerProvider (OTLP HTTP → corp Loki) и
автоматически получает trace_id/span_id из текущего span'а (ambient через
contextvars — поэтому, в отличие от Go-slog, ctx в сигнатуру не пробрасывается).
Поддерживает PII-redact (regex-фильтры через RedactConfig, LOG_REDACT_ENABLED).
Эмитит log_messages_total{level}.
"""

from typing import Any, Protocol


class Logger(Protocol):
    def debug(self, msg: str, **kv: Any) -> None: ...
    def info(self, msg: str, **kv: Any) -> None: ...
    def warn(self, msg: str, **kv: Any) -> None: ...
    def error(self, msg: str, **kv: Any) -> None: ...
    def with_(self, **kv: Any) -> "Logger": ...


def nop() -> Logger:
    return _Nop()


class _Nop:
    def debug(self, msg: str, **kv: Any) -> None: ...
    def info(self, msg: str, **kv: Any) -> None: ...
    def warn(self, msg: str, **kv: Any) -> None: ...
    def error(self, msg: str, **kv: Any) -> None: ...
    def with_(self, **kv: Any) -> Logger:
        return self
