"""SharedSecretConfig + shared_secret_guard: generic 403-guard для входящих
вебхуков, сверяющий заголовок с общим секретом в constant-time. Aiogram-agnostic
(Telegram передаёт X-Telegram-Bot-Api-Secret-Token, но guard не знает про
Telegram) — поэтому живёт в httpkit, а не в боте. Fail-closed: пустой секрет или
несовпадение → 403.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .error import forbidden


class SharedSecretConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    header: str = Field(default="X-Shared-Secret")
    secret: str = Field(default="")


def shared_secret_guard(cfg: SharedSecretConfig) -> Callable[[Request], None]:
    """FastAPI-dependency: 403, если заголовок cfg.header не совпал с cfg.secret.

    Пустой cfg.secret трактуется как «не настроено» → всегда 403 (fail-closed):
    лучше отбить все вебхуки, чем принять неаутентифицированные.
    """
    expected = cfg.secret
    header = cfg.header

    def guard(request: Request) -> None:
        provided = request.headers.get(header, "")
        if not expected or not hmac.compare_digest(provided, expected):
            raise forbidden("FORBIDDEN", "invalid shared secret")

    return guard
