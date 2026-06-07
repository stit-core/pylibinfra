"""PII-redact: defence-in-depth — рекурсивный best-effort regex-фильтр,
заменяющий совпадения в значениях log-record'а на [REDACTED]. Проходит по
вложенным dict/list/tuple и приводит не-строковые скаляры к str для матчинга.
Встроенные паттерны (email, длинные цифровые последовательности) дополняются
пользовательскими из LOG_REDACT_PATTERNS.

Это страховка, а НЕ замена дисциплине «не логировать PII»: best-effort фильтр
ловит лишь то, что покрыто паттернами, и не даёт гарантий полноты.
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


def _redact_str(value: str, patterns: list[Pattern[str]]) -> str:
    for pat in patterns:
        value = pat.sub(REDACTED, value)
    return value


def _redact_value(value: Any, patterns: list[Pattern[str]]) -> Any:
    if isinstance(value, str):
        return _redact_str(value, patterns)
    if isinstance(value, MutableMapping):
        # ключи не трогаем — редактируем только значения
        for key in value:
            value[key] = _redact_value(value[key], patterns)
        return value
    if isinstance(value, (list, tuple)):
        redacted = [_redact_value(item, patterns) for item in value]
        return type(value)(redacted) if isinstance(value, tuple) else redacted
    # bool — подкласс int, но не PII-носитель: матчить его str-форму бессмысленно
    if isinstance(value, bool) or value is None:
        return value
    # прочий скаляр: матчим str-форму, но тип меняем лишь при реальном совпадении
    if isinstance(value, (int, float)) and _redact_str(str(value), patterns) != str(value):
        return REDACTED
    return value


def build_redactor(cfg: RedactConfig) -> Processor | None:
    if not cfg.enabled:
        return None
    patterns = [*_DEFAULT_PATTERNS, *_compile_custom(cfg)]

    def redactor(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        ed: MutableMapping[str, Any] = event_dict
        for key, value in ed.items():
            ed[key] = _redact_value(value, patterns)
        return event_dict

    return redactor
