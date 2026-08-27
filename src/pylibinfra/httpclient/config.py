from pydantic import BaseModel, ConfigDict, Field
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


class BackendBreakerConfig(BaseModel):
    """Пороги breaker'а именованного бэкенда. Имени здесь нет: его всегда задаёт
    ApiClientsProvider ключом бэкенда — per-backend ряды http_client_breaker_*
    без коллизий."""

    model_config = ConfigDict(extra="forbid")

    failure_threshold: int = 5
    # секунды (TTL открытого состояния до пробного half-open запроса)
    recovery_timeout: float = 30.0
    success_threshold: int = 3


class BackendConfig(BaseModel):
    """Декларация одного именованного бэкенда.

    Plain-модель, НЕ BaseSettings: значения берутся только из самой декларации
    (JSON в HTTPCLIENT_BACKENDS либо код) — глобальные HTTPCLIENT_*-env сюда не
    протекают. Неполная декларация (нет base_url) падает валидацией на старте,
    а не молча ходит на чужой хост.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)
    # секунды (httpx таймаут на запрос целиком)
    timeout: float = 10.0
    max_retries: int = 3
    # секунды (базовая пауза backoff; растёт экспоненциально по номеру попытки)
    retry_wait: float = 0.2
    # Статические заголовки бэкенда (например, Authorization: Bearer <token>).
    headers: dict[str, str] = Field(default_factory=dict)
    breaker: BackendBreakerConfig = Field(default_factory=BackendBreakerConfig)


class BackendsConfig(BaseSettings):
    """Именованные бэкенды: name → BackendConfig (свой base_url/timeout/breaker на каждый).

    Caller заполняет программно из своих env (asymmetry note) либо одной env-переменной
    HTTPCLIENT_BACKENDS='{"crm": {"base_url": "..."}}' (JSON).
    """

    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    backends: dict[str, BackendConfig] = Field(
        default_factory=dict, validation_alias="HTTPCLIENT_BACKENDS"
    )
