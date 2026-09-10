"""logkit — структурный `Logger`: JSON/text в локальный sink, опциональный OTel-sink.

PII-redact на уровне полей, `request_id`/`trace_id`/`span_id` из контекста,
`register_attr` и метрика `log_messages_total{level}`.
"""

from .attrs import Attr, register_attr
from .build import Writer, new
from .config import (
    Config,
    ConfigError,
    RedactConfig,
    ServiceConfig,
    new_config,
    new_redact_config,
    new_service_config,
)
from .logger import Logger, nop
from .module import (
    LogkitProvider,
    OptionalLoggerProvider,
    OptionalRedactConfig,
)
from .redact import DEFAULT_PATTERNS
from .stdlib import capture_stdlib

__all__ = [
    "DEFAULT_PATTERNS",
    "Attr",
    "Config",
    "ConfigError",
    "Logger",
    "LogkitProvider",
    "OptionalLoggerProvider",
    "OptionalRedactConfig",
    "RedactConfig",
    "ServiceConfig",
    "Writer",
    "capture_stdlib",
    "new",
    "new_config",
    "new_redact_config",
    "new_service_config",
    "nop",
    "register_attr",
]
