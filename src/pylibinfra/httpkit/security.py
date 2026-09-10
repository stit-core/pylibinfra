"""Security-заголовки ответа: конфиг и middleware."""

from __future__ import annotations

from pydantic import Field
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .response import tap


class SecurityHeadersConfig(KitSettings):
    """Значения security-заголовков; пустое поле — заголовок не ставится."""

    model_config = configkit.settings_config()

    hsts: str = Field(default="max-age=31536000; includeSubDomains", validation_alias="HTTP_HSTS")
    content_type_options: str = Field(
        default="nosniff", validation_alias="HTTP_CONTENT_TYPE_OPTIONS"
    )
    frame_options: str = Field(default="DENY", validation_alias="HTTP_FRAME_OPTIONS")
    referrer_policy: str = Field(
        default="strict-origin-when-cross-origin", validation_alias="HTTP_REFERRER_POLICY"
    )
    csp: str = Field(default="", validation_alias="HTTP_CSP")


class SecurityHeadersMiddleware:
    """Регистрирует security-заголовки на любой ответ, включая 429, 413 и нейтральный 500."""

    def __init__(self, app: ASGIApp, cfg: SecurityHeadersConfig) -> None:
        """Список заголовков собирается один раз на приложение."""
        self._app = app
        self._headers = [
            (name, value)
            for name, value in (
                ("Strict-Transport-Security", cfg.hsts),
                ("X-Content-Type-Options", cfg.content_type_options),
                ("X-Frame-Options", cfg.frame_options),
                ("Referrer-Policy", cfg.referrer_policy),
                ("Content-Security-Policy", cfg.csp),
            )
            if value
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Заголовки регистрируются на пути запроса и применяются при старте ответа."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        for name, value in self._headers:
            observed.set_header(name, value)
        await self._app(scope, receive, observed.send)
