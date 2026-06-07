from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")
    pool_size: int = Field(default=10, validation_alias="REDIS_POOL_SIZE")
    # секунды (таймауты подключения и операций пула redis)
    dial_timeout: float = Field(default=5.0, validation_alias="REDIS_DIAL_TIMEOUT")
    read_timeout: float = Field(default=3.0, validation_alias="REDIS_READ_TIMEOUT")
    write_timeout: float = Field(default=3.0, validation_alias="REDIS_WRITE_TIMEOUT")
    # секунды; TTL ответа в IdempotencyStore (в Go задаётся на call-site, здесь —
    # Provider конструирует store, поэтому TTL приходит из конфига).
    idempotency_ttl: float = Field(default=86400.0, validation_alias="REDIS_IDEMPOTENCY_TTL")
