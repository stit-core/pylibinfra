"""RecoveryMiddleware: ловит необработанные исключения из нижележащих
handler'ов → логирует через logkit.Logger, инкрементит http_panics_total и
отдаёт 500 в форме APIError. APIError пробрасывается дальше (его рендерит
exception-handler с корректным статусом).
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra.httpkit.error import APIError, internal
from pylibinfra.httpkit.metrics import HTTPInstruments
from pylibinfra.logkit import Logger


class RecoveryMiddleware:
    def __init__(self, app: ASGIApp, log: Logger, instruments: HTTPInstruments) -> None:
        self._app = app
        self._log = log
        self._instr = instruments

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except APIError:
            raise
        except Exception as exc:
            path = scope.get("path", "")
            self._log.error("panic recovered", panic=repr(exc), path=path)
            self._instr.panics_total.add(1, {"path": path})
            if started:
                # response уже начат — заголовки/часть тела ушли; повторно
                # ответить нельзя, остаётся пробросить и оборвать соединение.
                raise
            await _send_error(send, internal("INTERNAL", "internal server error"))


async def _send_error(send: Send, err: APIError) -> None:
    import json

    body = json.dumps(err.with_defaults().to_body()).encode()
    await send(
        {
            "type": "http.response.start",
            "status": err.status,
            "headers": [(b"content-type", b"application/problem+json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
