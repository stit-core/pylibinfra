"""AccessLogMiddleware: пишет одну строку на запрос через logkit.Logger
(method/path/status/dur). Уровень по статусу: 5xx→error, 4xx→warn, иначе info.
request_id не дублируем — RequestID кладёт его в structlog-contextvars.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra.logkit import Logger


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp, log: Logger) -> None:
        self._app = app
        self._log = log

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        start = time.perf_counter()
        status = 200

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        await self._app(scope, receive, send_wrapper)

        dur = time.perf_counter() - start
        kv = {
            "method": scope.get("method", ""),
            "path": scope.get("path", ""),
            "status": status,
            "dur": dur,
        }
        if status >= 500:
            self._log.error("http", **kv)
        elif status >= 400:
            self._log.warn("http", **kv)
        else:
            self._log.info("http", **kv)
