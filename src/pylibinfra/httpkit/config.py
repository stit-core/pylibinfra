"""Config — public API httpkit. Env-имена точно как в golibinfra (HTTP_ADDR,
HTTP_READ_TIMEOUT, …; таймауты — секунды). Вложенные CORS/RateLimit/MaxBody/
Security/Idempotency читают свои env через собственные validation_alias'ы.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .cors import CORSConfig
from .idempotency import IdempotencyConfig
from .maxbody import MaxBodyConfig
from .ratelimit import RateLimitConfig
from .security import SecurityHeadersConfig


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    addr: str = Field(default=":8080", validation_alias="HTTP_ADDR")
    # секунды
    read_timeout: float = Field(default=15.0, validation_alias="HTTP_READ_TIMEOUT")
    idle_timeout: float = Field(default=60.0, validation_alias="HTTP_IDLE_TIMEOUT")
    shutdown_timeout: float = Field(default=15.0, validation_alias="HTTP_SHUTDOWN_TIMEOUT")
    api_title: str = Field(default="API", validation_alias="HTTP_API_TITLE")
    api_version: str = Field(default="0.1.0", validation_alias="HTTP_API_VERSION")

    cors: CORSConfig = Field(default_factory=CORSConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    max_body: MaxBodyConfig = Field(default_factory=MaxBodyConfig)
    security: SecurityHeadersConfig = Field(default_factory=SecurityHeadersConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)


def new_config() -> Config:
    return Config()
