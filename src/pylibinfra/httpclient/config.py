from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .breaker import BreakerConfig


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # секунды (httpx таймаут на запрос целиком)
    timeout: float = Field(default=10.0, validation_alias="HTTPCLIENT_TIMEOUT")
    max_retries: int = Field(default=3, validation_alias="HTTPCLIENT_MAX_RETRIES")
    # секунды (базовая пауза backoff; растёт экспоненциально по номеру попытки)
    retry_wait: float = Field(default=0.2, validation_alias="HTTPCLIENT_RETRY_WAIT")
    base_url: str = Field(default="", validation_alias="HTTPCLIENT_BASE_URL")

    breaker: BreakerConfig = Field(default_factory=lambda: BreakerConfig())
