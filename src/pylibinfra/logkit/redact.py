"""PII-redact: regex-фильтры, заменяющие совпадения в строковых значениях
log-record'а на [REDACTED]. Встроенные паттерны (email, длинные цифровые
последовательности) дополняются пользовательскими из LOG_REDACT_PATTERNS.
"""

import re
from collections.abc import MutableMapping
from re import Pattern
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.types import EventDict, Processor, WrappedLogger

REDACTED = "[REDACTED]"

_DEFAULT_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email
    re.compile(r"\b\d{13,19}\b"),  # card-like long digit runs
)


class RedactConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    enabled: bool = Field(default=False, validation_alias="LOG_REDACT_ENABLED")
    # запятая-разделённые regex-паттерны
    patterns: str = Field(default="", validation_alias="LOG_REDACT_PATTERNS")


def _compile_custom(cfg: RedactConfig) -> list[Pattern[str]]:
    return [re.compile(p.strip()) for p in cfg.patterns.split(",") if p.strip()]


def build_redactor(cfg: RedactConfig) -> Processor | None:
    if not cfg.enabled:
        return None
    patterns = [*_DEFAULT_PATTERNS, *_compile_custom(cfg)]

    def redactor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        ed: MutableMapping[str, Any] = event_dict
        for key, value in ed.items():
            if isinstance(value, str):
                for pat in patterns:
                    value = pat.sub(REDACTED, value)
                ed[key] = value
        return event_dict

    return redactor
