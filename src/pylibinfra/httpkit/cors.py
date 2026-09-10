"""CORS: политика в конфиге, preflight и заголовки ответа в middleware."""

from __future__ import annotations

from http import HTTPStatus
from typing import Annotated

from pydantic import Field, field_validator, model_validator
from pydantic_settings import NoDecode
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .response import header, tap

ANY_ORIGIN = "*"


def csv_list(value: object) -> object:
    """Разбирает запятую-разделённый список из ENV; не-строку отдаёт как есть."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class CORSConfig(KitSettings):
    """Политика CORS; пустой `origins` выключает middleware."""

    model_config = configkit.settings_config()

    origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list, validation_alias="HTTP_CORS_ORIGINS"
    )
    methods: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        validation_alias="HTTP_CORS_METHODS",
    )
    headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "X-Request-ID"],
        validation_alias="HTTP_CORS_HEADERS",
    )
    expose_headers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["X-Request-ID", "Location", "Retry-After", "Idempotent-Replayed"],
        validation_alias="HTTP_CORS_EXPOSE_HEADERS",
    )
    max_age: int = Field(default=600, validation_alias="HTTP_CORS_MAX_AGE")
    allow_credentials: bool = Field(default=False, validation_alias="HTTP_CORS_ALLOW_CREDENTIALS")

    @field_validator("origins", "methods", "headers", "expose_headers", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        return csv_list(value)

    @model_validator(mode="after")
    def _check(self) -> CORSConfig:
        if self.max_age < 0:
            raise ValueError("max_age must be >= 0")
        if self.allow_credentials and ANY_ORIGIN in self.origins:
            raise ValueError('allow_credentials is incompatible with origin "*"')
        return self


class CORSMiddleware:
    """Отвечает на preflight и регистрирует CORS-заголовки разрешённому origin.

    Origin, разрешённый только по `*`, всегда получает `*` и никогда — credentials: отражать
    произвольный origin вместе с credentials небезопасно.
    """

    def __init__(self, app: ASGIApp, cfg: CORSConfig) -> None:
        """Значения заголовков склеиваются один раз на приложение."""
        self._app = app
        self._any = ANY_ORIGIN in cfg.origins
        self._allowed = frozenset(o for o in cfg.origins if o != ANY_ORIGIN)
        self._methods = ", ".join(cfg.methods)
        self._headers = ", ".join(cfg.headers)
        self._expose = ", ".join(cfg.expose_headers)
        self._max_age = str(cfg.max_age)
        self._credentials = cfg.allow_credentials

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Preflight разрешённого origin завершается 204 до лимитера и чтения тела."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        # Ответ зависит от Origin: кэш не должен отдать чужой Access-Control-Allow-Origin.
        observed.add_header("Vary", "Origin")

        origin = header(scope, "Origin")
        listed = origin in self._allowed
        if origin and (listed or self._any):
            echo = origin if listed else ANY_ORIGIN
            observed.set_header("Access-Control-Allow-Origin", echo)
            observed.set_header("Access-Control-Allow-Methods", self._methods)
            observed.set_header("Access-Control-Allow-Headers", self._headers)
            observed.set_header("Access-Control-Max-Age", self._max_age)
            if self._expose:
                observed.set_header("Access-Control-Expose-Headers", self._expose)
            if self._credentials and echo != ANY_ORIGIN:
                observed.set_header("Access-Control-Allow-Credentials", "true")
            if scope.get("method") == "OPTIONS":
                await observed.send(
                    {"type": "http.response.start", "status": int(HTTPStatus.NO_CONTENT)}
                )
                await observed.send({"type": "http.response.body", "body": b""})
                return

        await self._app(scope, receive, observed.send)
