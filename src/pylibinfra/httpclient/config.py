"""Конфиг httpclient: политика попыток, редиректов, SSE и цепи; именованные бэкенды."""

from __future__ import annotations

import os
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError

KIT = "httpclient"

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_WAIT = 0.2
DEFAULT_RETRY_MAX_WAIT = 5.0
DEFAULT_MAX_REDIRECTS = 10
DEFAULT_SSE_MAX_EVENT_BYTES = 1 << 20
DEFAULT_FAILURE_THRESHOLD = 5
DEFAULT_RECOVERY_TIMEOUT = 30.0

_DURATION_DEFAULTS = {
    "timeout": DEFAULT_TIMEOUT,
    "retry_wait": DEFAULT_RETRY_WAIT,
    "retry_max_wait": DEFAULT_RETRY_MAX_WAIT,
    "recovery_timeout": DEFAULT_RECOVERY_TIMEOUT,
}
_SIZE_DEFAULTS = {
    "max_redirects": DEFAULT_MAX_REDIRECTS,
    "sse_max_event_bytes": DEFAULT_SSE_MAX_EVENT_BYTES,
}


def _duration_default(value: float, info: ValidationInfo) -> float:
    return _DURATION_DEFAULTS[str(info.field_name)] if value == 0 else value


def _size_default(value: int, info: ValidationInfo) -> int:
    return _SIZE_DEFAULTS[str(info.field_name)] if value == 0 else value


class BreakerConfig(KitSettings):
    """Политика circuit breaker'а; порог `0` выключает цепь целиком."""

    model_config = configkit.settings_config()

    failure_threshold: int = Field(
        default=DEFAULT_FAILURE_THRESHOLD,
        ge=0,
        validation_alias="HTTPCLIENT_BREAKER_FAILURE_THRESHOLD",
    )
    recovery_timeout: float = Field(
        default=DEFAULT_RECOVERY_TIMEOUT,
        ge=0,
        validation_alias="HTTPCLIENT_BREAKER_RECOVERY_TIMEOUT",
    )

    @field_validator("recovery_timeout")
    @classmethod
    def _recovery_default(cls, value: float, info: ValidationInfo) -> float:
        return _duration_default(value, info)


class Config(KitSettings):
    """Параметры клиента; нули заменяются дефолтами — клиент без таймаута собрать нельзя."""

    model_config = configkit.settings_config()

    timeout: float = Field(default=DEFAULT_TIMEOUT, ge=0, validation_alias="HTTPCLIENT_TIMEOUT")
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES, ge=0, validation_alias="HTTPCLIENT_MAX_RETRIES"
    )
    retry_wait: float = Field(
        default=DEFAULT_RETRY_WAIT, ge=0, validation_alias="HTTPCLIENT_RETRY_WAIT"
    )
    retry_max_wait: float = Field(
        default=DEFAULT_RETRY_MAX_WAIT, ge=0, validation_alias="HTTPCLIENT_RETRY_MAX_WAIT"
    )
    retry_non_idempotent: bool = Field(
        default=False, validation_alias="HTTPCLIENT_RETRY_NON_IDEMPOTENT"
    )
    max_redirects: int = Field(
        default=DEFAULT_MAX_REDIRECTS, ge=0, validation_alias="HTTPCLIENT_MAX_REDIRECTS"
    )
    allow_cross_host_redirects: bool = Field(
        default=False, validation_alias="HTTPCLIENT_ALLOW_CROSS_HOST_REDIRECTS"
    )
    sse_max_event_bytes: int = Field(
        default=DEFAULT_SSE_MAX_EVENT_BYTES, ge=0, validation_alias="HTTPCLIENT_SSE_MAX_EVENT_BYTES"
    )
    base_url: str = Field(default="", validation_alias="HTTPCLIENT_BASE_URL")

    breaker: BreakerConfig = Field(default_factory=BreakerConfig)

    @field_validator("timeout", "retry_wait", "retry_max_wait")
    @classmethod
    def _duration_defaults(cls, value: float, info: ValidationInfo) -> float:
        return _duration_default(value, info)

    @field_validator("max_redirects", "sse_max_event_bytes")
    @classmethod
    def _size_defaults(cls, value: int, info: ValidationInfo) -> int:
        return _size_default(value, info)

    @model_validator(mode="after")
    def _check_backoff_bounds(self) -> Self:
        if self.retry_max_wait < self.retry_wait:
            raise ValueError("retry_max_wait < retry_wait")
        return self


class BackendBreakerConfig(BaseModel):
    """Пороги цепи именованного бэкенда: значения только из декларации, не из ENV."""

    model_config = ConfigDict(extra="forbid")

    failure_threshold: int = Field(default=DEFAULT_FAILURE_THRESHOLD, ge=0)
    recovery_timeout: float = Field(default=DEFAULT_RECOVERY_TIMEOUT, gt=0)


class BackendConfig(BaseModel):
    """Декларация одного именованного бэкенда.

    Plain-модель, НЕ BaseSettings: значения берутся только из самой декларации
    (JSON в `HTTPCLIENT_BACKENDS` либо код) — глобальные `HTTPCLIENT_*` сюда не
    протекают. Неполная декларация (нет `base_url`) падает валидацией на старте,
    а не молча ходит на чужой хост.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0)
    retry_wait: float = Field(default=DEFAULT_RETRY_WAIT, gt=0)
    retry_max_wait: float = Field(default=DEFAULT_RETRY_MAX_WAIT, gt=0)
    retry_non_idempotent: bool = False
    max_redirects: int = Field(default=DEFAULT_MAX_REDIRECTS, gt=0)
    allow_cross_host_redirects: bool = False
    sse_max_event_bytes: int = Field(default=DEFAULT_SSE_MAX_EVENT_BYTES, gt=0)
    breaker: BackendBreakerConfig = Field(default_factory=BackendBreakerConfig)

    def client_config(self) -> Config:
        """Runtime-`Config` бэкенда: каждое поле явное, ENV в него не подмешивается."""
        breaker = configkit.build(
            BreakerConfig,
            KIT,
            ConfigError,
            failure_threshold=self.breaker.failure_threshold,
            recovery_timeout=self.breaker.recovery_timeout,
        )
        return configkit.build(
            Config,
            KIT,
            ConfigError,
            timeout=self.timeout,
            max_retries=self.max_retries,
            retry_wait=self.retry_wait,
            retry_max_wait=self.retry_max_wait,
            retry_non_idempotent=self.retry_non_idempotent,
            max_redirects=self.max_redirects,
            allow_cross_host_redirects=self.allow_cross_host_redirects,
            sse_max_event_bytes=self.sse_max_event_bytes,
            base_url=self.base_url,
            breaker=breaker,
        )


class BackendsConfig(KitSettings):
    """Именованные бэкенды: имя → `BackendConfig` (свой base_url/таймауты/цепь у каждого).

    Caller заполняет программно из своих env (asymmetry note) либо одной переменной
    `HTTPCLIENT_BACKENDS='{"crm": {"base_url": "…"}}'` (JSON).
    """

    model_config = configkit.settings_config()

    backends: dict[str, BackendConfig] = Field(
        default_factory=dict, validation_alias="HTTPCLIENT_BACKENDS"
    )


def new_config() -> Config:
    """Конфиг из `HTTPCLIENT_*`; невалидный — `ConfigError`."""
    return new_config_prefixed("")


def new_config_prefixed(prefix: str) -> Config:
    """Конфиг из `<PREFIX>_HTTPCLIENT_*` с фолбэком на голые `HTTPCLIENT_*`.

    Базовая политика на сервис плюс точечные переопределения на upstream —
    несколько клиентов с разной политикой в одном процессе.
    """
    breaker = configkit.build(BreakerConfig, KIT, ConfigError, **_prefixed(BreakerConfig, prefix))
    return configkit.build(Config, KIT, ConfigError, breaker=breaker, **_prefixed(Config, prefix))


def new_breaker_config() -> BreakerConfig:
    """Политика цепи из `HTTPCLIENT_BREAKER_*`; невалидная — `ConfigError`."""
    return configkit.build(BreakerConfig, KIT, ConfigError)


def new_backends_config() -> BackendsConfig:
    """Декларации именованных бэкендов из `HTTPCLIENT_BACKENDS`; невалидные — `ConfigError`."""
    return configkit.build(BackendsConfig, KIT, ConfigError)


def _prefixed(model: type[BaseSettings], prefix: str) -> dict[str, Any]:
    # Пустой префикс оставляем BaseSettings: она сама прочитает голые HTTPCLIENT_*.
    if not prefix:
        return {}
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        alias = field.validation_alias
        if not isinstance(alias, str):
            continue
        raw = os.environ.get(f"{prefix}_{alias}", "")
        if raw != "":
            values[name] = raw
    return values
