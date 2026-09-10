"""Config кита: адрес Redis, префикс ключей, пул, ретраи, таймауты, стартовые проверки."""

from __future__ import annotations

import re

from pydantic import Field, SecretStr, field_validator
from redis.asyncio import ConnectionPool

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError

_KIT = "cachekit"

# userinfo адреса: пароль не должен попасть ни в текст ошибки, ни в лог.
_USERINFO_RE = re.compile(r"//[^/@]*:([^/@]+)@")


class Config(KitSettings):
    """Параметры клиента Redis; имена полей и env-alias'ы — public API кита.

    Каждое нарушение инварианта называет свою ENV-переменную; читать конфиг —
    через `new_config`.
    """

    model_config = configkit.settings_config()

    url: SecretStr = Field(
        default=SecretStr("redis://localhost:6379/0"), validation_alias="REDIS_URL"
    )
    key_prefix: str = Field(default="", validation_alias="REDIS_KEY_PREFIX")
    pool_size: int = Field(default=10, validation_alias="REDIS_POOL_SIZE")
    max_retries: int = Field(default=3, validation_alias="REDIS_MAX_RETRIES")
    dial_timeout: float = Field(default=5.0, validation_alias="REDIS_DIAL_TIMEOUT")
    read_timeout: float = Field(default=3.0, validation_alias="REDIS_READ_TIMEOUT")
    write_timeout: float = Field(default=3.0, validation_alias="REDIS_WRITE_TIMEOUT")
    ping_on_start: bool = Field(default=True, validation_alias="REDIS_PING_ON_START")
    skip_version_check: bool = Field(default=False, validation_alias="REDIS_SKIP_VERSION_CHECK")

    @field_validator("url", mode="after")
    @classmethod
    def _parsable_url(cls, value: SecretStr) -> SecretStr:
        """Адрес обязан разбираться `redis.asyncio`: иначе падает первая команда, а не старт."""
        url = value.get_secret_value()
        try:
            ConnectionPool.from_url(url)
        except (ValueError, TypeError) as exc:
            raise ValueError(redact(str(exc), url)) from None
        return value

    @field_validator("pool_size", mode="after")
    @classmethod
    def _positive_pool(cls, value: int) -> int:
        """Пул на ноль соединений не отдаст ни одной команды."""
        if value < 1:
            raise ValueError(f"must be >= 1, got {value}")
        return value

    @field_validator("max_retries", mode="after")
    @classmethod
    def _retries(cls, value: int) -> int:
        """`-1` выключает повторы; меньшее — опечатка, а не более сильное выключение."""
        if value < -1:
            raise ValueError(f"must be >= -1 (-1 disables retries), got {value}")
        return value

    @field_validator("dial_timeout", "read_timeout", "write_timeout", mode="after")
    @classmethod
    def _timeout(cls, value: float) -> float:
        """Отрицательного таймаута нет; `0` — ждать без ограничения."""
        if value < 0:
            raise ValueError(f"must be >= 0, got {value:g}")
        return value


def redact(text: str, url: str) -> str:
    """Прячет пароль из `url` в `text`; без userinfo текст возвращается как есть."""
    match = _USERINFO_RE.search(url)
    if match is None:
        return text
    return text.replace(match.group(1), "***")


def new_config() -> Config:
    """Читает `REDIS_*` из окружения; невалидное значение — `cachekit.ConfigError`."""
    return configkit.build(Config, _KIT, ConfigError)
