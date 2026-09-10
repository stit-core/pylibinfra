"""Конфиги logkit: уровень и формат записи, PII-redact, идентичность сервиса."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .redact import compile_rules

_KIT = "logkit"

DEBUG = 10
INFO = 20
WARN = 30
ERROR = 40

_LEVELS: dict[str, int] = {"debug": DEBUG, "info": INFO, "warn": WARN, "error": ERROR}


class ConfigError(configkit.ConfigError):
    """Невалидный уровень, формат, regex-паттерн redact'а или строка-замена."""


def parse_level(value: str) -> int:
    """Порог уровня по имени (регистр не важен); пустая строка — `info`, иное имя — `ValueError`."""
    name = value.strip().lower()
    if not name:
        return INFO
    level = _LEVELS.get(name)
    if level is None:
        raise ValueError(f"LOG_LEVEL {value!r}: want one of debug, info, warn, error")
    return level


def parse_format(value: str) -> str:
    """Формат локального sink'а: `json` (и пустая строка) либо `text`; иное — `ValueError`."""
    name = value.strip().lower()
    if name in ("", "json"):
        return "json"
    if name == "text":
        return "text"
    raise ValueError(f'LOG_FORMAT {value!r}: want "json" or "text"')


def split_patterns(value: str) -> tuple[str, ...]:
    """Режет `LOG_REDACT_PATTERNS` по неэкранированным `;` (литеральная `;` — как `\\;`)."""
    out: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            # «\;» остаётся в паттерне: re читает это как литеральную «;».
            cur.append(ch)
            i += 1
            cur.append(value[i])
        elif ch == ";":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return tuple(p.strip() for p in out if p.strip())


class Config(KitSettings):
    """Уровень, формат и назначение локального sink'а; конфиг по умолчанию валиден."""

    model_config = configkit.settings_config()

    level: str = Field(default="info", validation_alias="LOG_LEVEL")
    format: str = Field(default="json", validation_alias="LOG_FORMAT")
    source: bool = Field(default=False, validation_alias="LOG_SOURCE")
    silent: bool = Field(default=False, validation_alias="LOG_SILENT")

    @field_validator("level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        parse_level(value)
        return value.strip().lower() or "info"

    @field_validator("format")
    @classmethod
    def _valid_format(cls, value: str) -> str:
        return parse_format(value)


class RedactConfig(KitSettings):
    """Маскирование PII и секретов в `msg` и строковых значениях полей записи."""

    model_config = configkit.settings_config()

    enabled: bool = Field(default=False, validation_alias="LOG_REDACT_ENABLED")
    patterns: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(), validation_alias="LOG_REDACT_PATTERNS"
    )
    replace_with: str = Field(default="[REDACTED]", validation_alias="LOG_REDACT_REPLACE_WITH")

    @field_validator("patterns", mode="before")
    @classmethod
    def _split(cls, value: Any) -> Any:
        if isinstance(value, str):
            return split_patterns(value)
        return value

    @model_validator(mode="after")
    def _compilable(self) -> RedactConfig:
        compile_rules(self.patterns, self.replace_with)
        return self


class ServiceConfig(KitSettings):
    """Идентичность сервиса для sink'а без OTel-resource: поля `service`/`env`/`version`."""

    model_config = configkit.settings_config()

    service: str = Field(default="", validation_alias="OTEL_SERVICE_NAME")
    env: str = Field(default="dev", validation_alias="APP_ENV")
    version: str = Field(default="dev", validation_alias="OTEL_SERVICE_VERSION")


def new_config() -> Config:
    """Читает `LOG_*` из окружения; невалидные значения — `ConfigError`."""
    return configkit.build(Config, _KIT, ConfigError)


def new_redact_config() -> RedactConfig:
    """Читает `LOG_REDACT_*` из окружения; невалидный паттерн или замена — `ConfigError`."""
    return configkit.build(RedactConfig, _KIT, ConfigError)


def new_service_config() -> ServiceConfig:
    """Читает идентичность сервиса (`OTEL_SERVICE_NAME`/`APP_ENV`/`OTEL_SERVICE_VERSION`)."""
    return configkit.build(ServiceConfig, _KIT, ConfigError)
