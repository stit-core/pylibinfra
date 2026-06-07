"""AuthExtractor Protocol + AuthMiddleware: опциональная аутентификация. Если
extractor предоставлен — на каждый запрос вызывает его; identity кладётся в
scope["state"]["auth"] (доступно роуту через request.state.auth), ошибка → 401.
Без extractor'а middleware не подключается (мягкая опциональность, как
optional-зависимость в golibinfra fx).
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .error import APIError, unauthorized


@runtime_checkable
class AuthExtractor(Protocol):
    async def __call__(self, request: Request) -> object: ...


class AuthMiddleware:
    def __init__(self, app: ASGIApp, extractor: AuthExtractor) -> None:
        self._app = app
        self._extractor = extractor

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        try:
            identity = await self._extractor(request)
        except APIError:
            raise
        except Exception:
            await _send_unauthorized(send)
            return

        scope.setdefault("state", {})["auth"] = identity
        await self._app(scope, receive, send)


async def _send_unauthorized(send: Send) -> None:
    body = json.dumps(
        unauthorized("UNAUTHORIZED", "unauthorized").with_defaults().to_body()
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/problem+json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
