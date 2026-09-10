"""Env-контракт orchestration-слоя: батч, режим публикации, backoff и дедлайн тика."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError

_KIT = "outboxkit"

DEFAULT_INSTANCE = "outbox"
"""Имя single-instance outbox'а: label `instance` в метриках и таблица `pgstore.DEFAULT_TABLE`."""

DEFAULT_BATCH_SIZE = 100
DEFAULT_TICK_TIMEOUT = 30.0
DEFAULT_RETRY_BACKOFF = 1.0
DEFAULT_RETRY_MAX_BACKOFF = 300.0


class PublishMode(StrEnum):
    """Компромисс «порядок vs пропускная способность» на хопе worker→брокер.

    `batch` — async-батч (один round-trip на тик), порядок персиста best-effort.
    `ordered` — последовательная публикация со стопом на первой ошибке.
    """

    BATCH = "batch"
    ORDERED = "ordered"


class Config(KitSettings):
    """Конфиг `Bus` + `Worker`; невалидное значение валит старт (`ConfigError`)."""

    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, ge=1, validation_alias="OUTBOX_BATCH_SIZE")
    interval: float = Field(default=1.0, gt=0, validation_alias="OUTBOX_INTERVAL")
    publish_mode: PublishMode = Field(
        default=PublishMode.BATCH, validation_alias="OUTBOX_PUBLISH_MODE"
    )
    max_attempts: int = Field(default=0, ge=0, validation_alias="OUTBOX_MAX_ATTEMPTS")
    tick_timeout: float = Field(
        default=DEFAULT_TICK_TIMEOUT, gt=0, validation_alias="OUTBOX_TICK_TIMEOUT"
    )
    retry_backoff: float = Field(
        default=DEFAULT_RETRY_BACKOFF, ge=0, validation_alias="OUTBOX_RETRY_BACKOFF"
    )
    retry_max_backoff: float = Field(
        default=DEFAULT_RETRY_MAX_BACKOFF, ge=0, validation_alias="OUTBOX_RETRY_MAX_BACKOFF"
    )

    @model_validator(mode="after")
    def _backoff_ceiling(self) -> Config:
        if 0 < self.retry_max_backoff < self.retry_backoff:
            raise ValueError(
                f"OUTBOX_RETRY_MAX_BACKOFF ({self.retry_max_backoff:g}s) must be >= "
                f"OUTBOX_RETRY_BACKOFF ({self.retry_backoff:g}s)"
            )
        return self


def new_config() -> Config:
    """Читает `OUTBOX_*` из окружения; невалидные значения — `ConfigError`."""
    return configkit.build(Config, _KIT, ConfigError)
