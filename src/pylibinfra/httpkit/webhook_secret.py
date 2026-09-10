"""Generic 403-guard входящего вебхука по общему секрету в заголовке."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request
from pydantic import Field, SecretStr

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .error import forbidden


class SharedSecretConfig(KitSettings):
    """Заголовок и общий секрет вебхука; пустой секрет трактуется как «не настроено».

    Секрет — `SecretStr`: в `repr` конфига и в тексте ошибки валидации он не показывается.
    """

    model_config = configkit.settings_config()

    header: str = Field(default="X-Shared-Secret")
    secret: SecretStr = Field(default=SecretStr(""))


def shared_secret_guard(cfg: SharedSecretConfig) -> Callable[[Request], None]:
    """FastAPI-dependency: 403, если заголовок не совпал с секретом (сверка constant-time).

    Пустой `secret` — fail-closed: лучше отбить все вебхуки, чем принять неаутентифицированные.
    """
    expected = cfg.secret.get_secret_value().encode()
    header = cfg.header

    def guard(request: Request) -> None:
        # Starlette декодирует заголовок latin-1: сравниваем исходные байты, иначе
        # байт >0x7F в чужом заголовке даёт TypeError и 500 вместо 403.
        provided = request.headers.get(header, "").encode("latin-1", "replace")
        if not expected or not hmac.compare_digest(provided, expected):
            raise forbidden("FORBIDDEN", "invalid shared secret")

    return guard
