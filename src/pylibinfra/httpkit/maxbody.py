"""Лимит тела запроса: конфиг, ошибка чтения и middleware."""

from __future__ import annotations

from http import HTTPStatus

from pydantic import Field, model_validator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from pylibinfra._internal import configkit
from pylibinfra._internal.configkit import KitSettings

from .error import APIError, HttpkitError, error, write_error
from .metrics import Instruments, instruments, route_label
from .response import BODY_OVERFLOW_KEY, header, tap


class MaxBodyError(HttpkitError):
    """Тело запроса превысило `HTTP_MAX_BODY_BYTES`; поднимается из `receive` при чтении."""


class MaxBodyConfig(KitSettings):
    """Потолок тела запроса; `max_bytes = 0` выключает лимит."""

    model_config = configkit.settings_config()

    max_bytes: int = Field(default=1048576, validation_alias="HTTP_MAX_BODY_BYTES")

    @model_validator(mode="after")
    def _check(self) -> MaxBodyConfig:
        if self.max_bytes < 0:
            raise ValueError("max_bytes must be >= 0")
        return self


def too_large() -> APIError:
    """Ответ 413 для запроса, тело которого превысило лимит."""
    return error("PAYLOAD_TOO_LARGE", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")


def _declared_length(scope: Scope) -> int:
    raw = header(scope, "Content-Length")
    try:
        return int(raw)
    except ValueError:
        return 0


class MaxBodyMiddleware:
    """Отвечает 413 на слишком большое тело и считает `http_max_body_rejections_total`.

    Объявленный `Content-Length` отбивается до чтения; тело без него считается по мере чтения и
    поднимает `MaxBodyError` из `receive`. Обработчик, не читающий тело, о превышении не узнаёт.
    """

    def __init__(self, app: ASGIApp, cfg: MaxBodyConfig, instr: Instruments | None = None) -> None:
        """`max_bytes = 0` делает middleware сквозным."""
        self._app = app
        self._max = cfg.max_bytes
        self._instr = instr if instr is not None else instruments()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Переполнение отмечается в scope: 413 отдаёт и обработчик ошибок, если ответ уже его."""
        if scope["type"] != "http" or self._max <= 0:
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        try:
            if _declared_length(scope) > self._max:
                scope[BODY_OVERFLOW_KEY] = True
                await write_error(observed.send, too_large())
                return
            try:
                await self._app(scope, self._counting(scope, receive), observed.send)
            except MaxBodyError:
                if observed.started:
                    raise
                await write_error(observed.send, too_large())
            except BaseExceptionGroup as group:
                # Обработчик, читающий тело внутри task-group, отдаёт ошибку завёрнутой.
                if observed.started or group.subgroup(MaxBodyError) is None:
                    raise
                await write_error(observed.send, too_large())
        finally:
            if scope.get(BODY_OVERFLOW_KEY):
                self._instr.max_body_rejections.add(1, {"path": route_label(scope)})

    def _counting(self, scope: Scope, receive: Receive) -> Receive:
        seen = 0

        async def wrapped() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b"") or b"")
                if seen > self._max:
                    scope[BODY_OVERFLOW_KEY] = True
                    raise MaxBodyError("httpkit: request body too large")
            return message

        return wrapped
