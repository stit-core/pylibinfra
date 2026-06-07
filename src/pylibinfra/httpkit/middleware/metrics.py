"""MetricsMiddleware: эмитит http_requests_total + http_request_duration_seconds
с атрибутами method/path/status. path берём из роут-шаблона (route.path), а не
из конкретного URL — иначе кардинальность метрик взрывается на path-параметрах.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra.httpkit.metrics import HTTPInstruments


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, instruments: HTTPInstruments) -> None:
        self._app = app
        self._instr = instruments

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

        attrs = {
            "method": scope.get("method", ""),
            "path": _route_path(scope),
            "status": str(status),
        }
        self._instr.req_total.add(1, attrs)
        self._instr.req_duration.record(time.perf_counter() - start, attrs)


def _route_path(scope: Scope) -> str:
    route = scope.get("route")
    path_format = getattr(route, "path_format", None) or getattr(route, "path", None)
    if isinstance(path_format, str) and path_format:
        return path_format
    return str(scope.get("path", ""))
