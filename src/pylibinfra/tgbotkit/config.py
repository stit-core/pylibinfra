"""Config — public API tgbotkit. Env-имена по конвенции BOT_* (таймауты —
секунды). Redis приходит из cachekit (REDIS_URL) — здесь НЕ дублируется. Поля и
их validation_alias — часть стабильности API: менять = breaking.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class I18nConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    default_locale: str = Field(default="en", validation_alias="BOT_I18N_DEFAULT_LOCALE")
    # корень с .ftl-каталогами; пустой путь = i18n не настроен (install_i18n no-op).
    # Имя поля НЕ "path" умышленно: с populate_by_name=True оно подхватило бы $PATH.
    locales_dir: str = Field(default="", validation_alias="BOT_I18N_PATH")
    # имена .ftl-файлов внутри каждой локали (resource_ids у fluent)
    resources: list[str] = Field(
        default_factory=lambda: ["main.ftl"], validation_alias="BOT_I18N_RESOURCES"
    )


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    # SecretStr: токен не утекает в repr/model_dump/трейсбек ValidationError
    token: SecretStr = Field(default=SecretStr(""), validation_alias="BOT_TOKEN")
    mode: Literal["polling", "webhook"] = Field(default="polling", validation_alias="BOT_MODE")
    parse_mode: str = Field(default="HTML", validation_alias="BOT_PARSE_MODE")

    # dedup маркера update_id: Telegram ретраит апдейты, при ≥2 воркерах нужен общий guard
    dedup_enabled: bool = Field(default=True, validation_alias="BOT_DEDUP_ENABLED")
    dedup_ttl: float = Field(default=600.0, validation_alias="BOT_DEDUP_TTL")
    # per-user троттлинг апдейтов; 0 = off (как rate-limit у httpkit)
    throttle_rps: int = Field(default=0, validation_alias="BOT_THROTTLE_RPS")
    # TTL FSM-стейта/dialog_data в redis (секунды); 0 = вечно (дефолт aiogram). >0 даёт
    # дочистку висящих диалогов и deprecated State, иначе ключи копятся в redis навсегда.
    state_ttl: int = Field(default=0, validation_alias="BOT_STATE_TTL")

    # исходящий rate-limit/ordering/429-retry (OutgoingThrottleMiddleware на bot.session)
    flood_enabled: bool = Field(default=True, validation_alias="BOT_FLOOD_ENABLED")
    rate_global: float = Field(default=30.0, validation_alias="BOT_RATE_GLOBAL")  # msg/s
    rate_per_chat: float = Field(default=1.0, validation_alias="BOT_RATE_PER_CHAT")  # msg/s/chat
    retry_after_attempts: int = Field(default=3, validation_alias="BOT_RETRY_AFTER_ATTEMPTS")

    # webhook-режим (в polling игнорируются)
    webhook_path: str = Field(default="/tg/webhook", validation_alias="BOT_WEBHOOK_PATH")
    webhook_base_url: str = Field(default="", validation_alias="BOT_WEBHOOK_BASE_URL")
    webhook_secret: SecretStr = Field(default=SecretStr(""), validation_alias="BOT_WEBHOOK_SECRET")
    drop_pending_updates: bool = Field(default=False, validation_alias="BOT_DROP_PENDING")

    i18n: I18nConfig = Field(default_factory=I18nConfig)
