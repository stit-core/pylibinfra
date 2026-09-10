"""Config и I18nConfig — публичный env-контракт бота и его проверка."""

from __future__ import annotations

import os
import re
from typing import Annotated, Literal

from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import NoDecode

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .errors import ConfigError

Mode = Literal["polling", "webhook"]
ParseMode = Literal["HTML", "MarkdownV2", "Markdown", ""]

DEFAULT_WEBHOOK_PATH = "/tg/webhook"
DEFAULT_RESOURCE = "main.ftl"

# Telegram принимает secret_token вебхука только из этого набора длиной 1..256.
_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def _csv_list(value: object) -> object:
    """ENV-список пишется через запятую: JSON-декодер pydantic-settings здесь не к месту."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class I18nConfig(KitSettings):
    """Каталог локалей fluent; пустой `BOT_I18N_PATH` — i18n не настроен, ключи как есть."""

    default_locale: str = Field(default="en", validation_alias="BOT_I18N_DEFAULT_LOCALE")
    # Имя поля не `path`: с populate_by_name оно подхватило бы $PATH, будь источник нестрогим.
    locales_dir: str = Field(default="", validation_alias="BOT_I18N_PATH")
    resources: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [DEFAULT_RESOURCE], validation_alias="BOT_I18N_RESOURCES"
    )

    @field_validator("resources", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return _csv_list(value)

    @model_validator(mode="after")
    def _check(self) -> I18nConfig:
        if not self.default_locale:
            raise ValueError("BOT_I18N_DEFAULT_LOCALE must not be empty")
        if not self.resources:
            raise ValueError("BOT_I18N_RESOURCES must not be empty")
        if not self.locales_dir:
            return self
        if not os.path.isdir(self.locales_dir):
            raise ValueError("BOT_I18N_PATH is not a directory")
        # Каталога дефолтной локали нет — фолбэк цепочки fluent упирается в пустоту и
        # каждый ключ молча рендерится сам собой; это ловится только глазами в проде.
        if not os.path.isdir(os.path.join(self.locales_dir, self.default_locale)):
            raise ValueError("BOT_I18N_PATH has no catalog for BOT_I18N_DEFAULT_LOCALE")
        return self


class Config(KitSettings):
    """Публичный env-контракт tgbotkit; `BOT_TOKEN` обязателен, остальное имеет дефолты."""

    token: SecretStr = Field(validation_alias="BOT_TOKEN")
    mode: Mode = Field(default="polling", validation_alias="BOT_MODE")
    parse_mode: ParseMode = Field(default="HTML", validation_alias="BOT_PARSE_MODE")
    api_timeout: float = Field(default=60.0, validation_alias="BOT_API_TIMEOUT")
    shutdown_timeout: float = Field(default=10.0, validation_alias="BOT_SHUTDOWN_TIMEOUT")

    dedup_enabled: bool = Field(default=True, validation_alias="BOT_DEDUP_ENABLED")
    dedup_ttl: float = Field(default=600.0, validation_alias="BOT_DEDUP_TTL")
    throttle_rps: int = Field(default=0, validation_alias="BOT_THROTTLE_RPS")
    state_ttl: int = Field(default=0, validation_alias="BOT_STATE_TTL")

    flood_enabled: bool = Field(default=True, validation_alias="BOT_FLOOD_ENABLED")
    rate_global: float = Field(default=30.0, validation_alias="BOT_RATE_GLOBAL")
    rate_per_chat: float = Field(default=1.0, validation_alias="BOT_RATE_PER_CHAT")
    rate_max_wait: float = Field(default=60.0, validation_alias="BOT_RATE_MAX_WAIT")
    retry_after_attempts: int = Field(default=3, validation_alias="BOT_RETRY_AFTER_ATTEMPTS")

    webhook_path: str = Field(default=DEFAULT_WEBHOOK_PATH, validation_alias="BOT_WEBHOOK_PATH")
    webhook_base_url: str = Field(default="", validation_alias="BOT_WEBHOOK_BASE_URL")
    webhook_secret: SecretStr = Field(default=SecretStr(""), validation_alias="BOT_WEBHOOK_SECRET")
    drop_pending_updates: bool = Field(default=False, validation_alias="BOT_DROP_PENDING")

    i18n: I18nConfig = Field(default_factory=I18nConfig)

    @field_validator("token")
    @classmethod
    def _check_token(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            raise ValueError("BOT_TOKEN must not be empty")
        try:
            validate_token(raw)
        except TokenValidationError:
            # Текст aiogram не подставляем: сообщение об ошибке конфига видят логи.
            raise ValueError("BOT_TOKEN is malformed: expected <bot_id>:<secret>") from None
        return value

    @model_validator(mode="after")
    def _check(self) -> Config:
        for env, value in (
            ("BOT_API_TIMEOUT", self.api_timeout),
            ("BOT_DEDUP_TTL", self.dedup_ttl),
            ("BOT_RATE_MAX_WAIT", self.rate_max_wait),
        ):
            if value <= 0:
                raise ValueError(f"{env} must be > 0")
        for env, value in (
            ("BOT_SHUTDOWN_TIMEOUT", self.shutdown_timeout),
            ("BOT_THROTTLE_RPS", float(self.throttle_rps)),
            ("BOT_STATE_TTL", float(self.state_ttl)),
            ("BOT_RATE_GLOBAL", self.rate_global),
            ("BOT_RATE_PER_CHAT", self.rate_per_chat),
        ):
            if value < 0:
                raise ValueError(f"{env} must be >= 0")
        if self.retry_after_attempts < 1:
            raise ValueError("BOT_RETRY_AFTER_ATTEMPTS must be >= 1")
        if not self.webhook_path.startswith("/"):
            raise ValueError("BOT_WEBHOOK_PATH must start with '/'")
        self._check_webhook()
        return self

    def _check_webhook(self) -> None:
        secret = self.webhook_secret.get_secret_value()
        if secret and not _SECRET_RE.match(secret):
            raise ValueError("BOT_WEBHOOK_SECRET must be 1..256 chars of A-Z a-z 0-9 _ -")
        if self.mode != "webhook":
            return
        if not self.webhook_base_url.startswith("https://"):
            raise ValueError("BOT_WEBHOOK_BASE_URL must be an https:// URL in webhook mode")
        # Гейт вебхука fail-closed: с пустым секретом он отбивает все апдейты, и бот
        # выглядит живым, но немым.
        if not secret:
            raise ValueError("BOT_WEBHOOK_SECRET is required in webhook mode")

    def webhook_url(self) -> str:
        """Полный URL вебхука для `set_webhook`; пуст вне webhook-режима."""
        if not self.webhook_base_url:
            return ""
        return self.webhook_base_url.rstrip("/") + self.webhook_path


def new_config() -> Config:
    """Читает `BOT_*` из окружения; невалидное значение — `ConfigError` на старте."""
    return configkit.build(Config, "tgbotkit", ConfigError)
