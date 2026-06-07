"""RequestIDMiddleware: читает X-Request-ID из запроса (или генерирует uuid4),
кладёт в contextvar + structlog-bind (для авто-полей в логах) и возвращает
заголовком в ответе.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra.httpkit.context import HEADER_REQUEST_ID, reset_request_id, set_request_id

_HEADER_LOWER = HEADER_REQUEST_ID.lower().encode("latin-1")


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        rid = _from_headers(scope) or uuid.uuid4().hex
        token = set_request_id(rid)
        # request_id в contextvars structlog → попадает в каждый ctx-scoped лог
        # без явного проброса (как RegisterAttr в golibinfra).
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            await self._app(scope, receive, self._wrap(send, rid))
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
            reset_request_id(token)

    def _wrap(self, send: Send, rid: str) -> Callable[[Message], Awaitable[None]]:
        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((_HEADER_LOWER, rid.encode("latin-1")))
            await send(message)

        return send_wrapper


def _from_headers(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == _HEADER_LOWER:
            return bytes(value).decode("latin-1")
    return ""
