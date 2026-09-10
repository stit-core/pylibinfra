"""Config кита: bind-адрес, таймауты, OpenAPI, quiet-paths и вложенные конфиги middleware."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .cors import CORSConfig, csv_list
from .idempotency import IdempotencyConfig
from .maxbody import MaxBodyConfig
from .ratelimit import RateLimitConfig
from .security import SecurityHeadersConfig


class ConfigError(configkit.ConfigError):
    """Невалидный конфиг httpkit; валит старт приложения, а не первый запрос."""


class Config(KitSettings):
    """Настройки сервера и вложенные конфиги middleware; имена полей и ENV — public API."""

    model_config = configkit.settings_config()

    addr: str = Field(default=":8080", validation_alias="HTTP_ADDR")
    read_timeout: float = Field(default=15.0, validation_alias="HTTP_READ_TIMEOUT")
    idle_timeout: float = Field(default=60.0, validation_alias="HTTP_IDLE_TIMEOUT")
    shutdown_timeout: float = Field(default=15.0, validation_alias="HTTP_SHUTDOWN_TIMEOUT")
    api_title: str = Field(default="API", validation_alias="HTTP_API_TITLE")
    api_version: str = Field(default="0.1.0", validation_alias="HTTP_API_VERSION")
    docs_enabled: bool = Field(default=True, validation_alias="HTTP_DOCS_ENABLED")
    ready_probe_timeout: float = Field(default=5.0, validation_alias="HTTP_READY_PROBE_TIMEOUT")
    quiet_paths: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["/health", "/ready"], validation_alias="HTTP_QUIET_PATHS"
    )

    cors: CORSConfig = Field(default_factory=CORSConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    max_body: MaxBodyConfig = Field(default_factory=MaxBodyConfig)
    security: SecurityHeadersConfig = Field(default_factory=SecurityHeadersConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)

    @field_validator("quiet_paths", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return csv_list(value)

    @model_validator(mode="after")
    def _check(self) -> Config:
        if not self.addr:
            raise ValueError("addr must not be empty")
        if self.read_timeout < 0 or self.idle_timeout < 0:
            raise ValueError("read_timeout and idle_timeout must be >= 0")
        if self.shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be > 0")
        if self.ready_probe_timeout <= 0:
            raise ValueError("ready_probe_timeout must be > 0")
        if not self.api_title or not self.api_version:
            raise ValueError("api_title and api_version must not be empty")
        return self


def new_config() -> Config:
    """Читает `HTTP_*` из окружения; невалидное значение — `ConfigError`."""
    return configkit.build(Config, "httpkit", ConfigError)
