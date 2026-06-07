"""MaxBodyConfig + MaxBodyMiddleware: отклоняет запросы с телом больше
max_bytes ответом 413 (APIError) и инкрементит http_max_body_rejections_total.
Лимит 0 → проверка отключена. Проверяет Content-Length, а при его отсутствии
(chunked) — считает байты по мере чтения тела.
"""

from __future__ import annotations

import json

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .error import APIError, error
from .metrics import HTTPInstruments


class MaxBodyConfig(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    max_bytes: int = Field(default=1048576, validation_alias="HTTP_MAX_BODY_BYTES")


def _too_large() -> APIError:
    return error("PAYLOAD_TOO_LARGE", 413, "request body too large")


class MaxBodyMiddleware:
    def __init__(self, app: ASGIApp, cfg: MaxBodyConfig, instruments: HTTPInstruments) -> None:
        self._app = app
        self._max = cfg.max_bytes
        self._instr = instruments

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._max <= 0:
            await self._app(scope, receive, send)
            return

        if _declared_length(scope) > self._max:
            await self._reject(scope, send)
            return

        await self._app(scope, self._counting_receive(scope, receive, send), send)

    def _counting_receive(self, scope: Scope, receive: Receive, send: Send) -> Receive:
        seen = 0
        rejected = False

        async def wrapped() -> Message:
            nonlocal seen, rejected
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max and not rejected:
                    rejected = True
                    self._count(scope)
                    # сигналим handler'у разрыв тела; reject уже отправляется
                    # выше по Content-Length — для chunked это страховка.
                    return {"type": "http.disconnect"}
            return message

        return wrapped

    async def _reject(self, scope: Scope, send: Send) -> None:
        self._count(scope)
        body = json.dumps(_too_large().with_defaults().to_body()).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/problem+json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _count(self, scope: Scope) -> None:
        self._instr.max_body_rejections.add(1, {"path": scope.get("path", "")})


def _declared_length(scope: Scope) -> int:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0
