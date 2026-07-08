from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PublishMode(StrEnum):
    """Компромисс «порядок vs пропускная способность» на хопе worker→брокер.

    batch   — async-батч (один round-trip на тик); порядок персиста best-effort.
    ordered — последовательная публикация со стопом на первой ошибке; строгий
              порядок per-subject ценой round-trip на сообщение.
    """

    BATCH = "batch"
    ORDERED = "ordered"


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    batch_size: int = Field(default=100, validation_alias="OUTBOX_BATCH_SIZE")
    # секунды (Go-аналог OUTBOX_INTERVAL: time.Duration 1s)
    interval: float = Field(default=1.0, validation_alias="OUTBOX_INTERVAL")
    publish_mode: PublishMode = Field(
        default=PublishMode.BATCH, validation_alias="OUTBOX_PUBLISH_MODE"
    )
    # <=0 — без ограничения (ретрай вечно); иначе после N неудач строка dead.
    max_attempts: int = Field(default=0, validation_alias="OUTBOX_MAX_ATTEMPTS")
