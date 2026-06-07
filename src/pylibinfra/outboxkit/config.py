from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    batch_size: int = Field(default=100, validation_alias="OUTBOX_BATCH_SIZE")
    # секунды (Go-аналог OUTBOX_INTERVAL: time.Duration 1s)
    interval: float = Field(default=1.0, validation_alias="OUTBOX_INTERVAL")
