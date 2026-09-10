"""RequestID, OTel, AccessLog и Recovery — верх цепочки кита."""

from __future__ import annotations

import time
import traceback
from collections.abc import Iterable
from http import HTTPStatus

from opentelemetry import propagate
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Receive, Scope, Send

from pylibinfra._internal import otelkit, requestid
from pylibinfra.logkit import Logger

from .error import internal, write_error
from .metrics import KIT, UNMATCHED, Instruments, instruments, method_label, route_label
from .response import header, tap


class RequestIDMiddleware:
    """Кладёт request-id в контекст и эхает его заголовком `X-Request-ID`.

    Входящий id принимается, только если проходит `requestid.valid`, иначе выдаётся UUID v7:
    id попадает в каждую строку лога. Поле `request_id` в записи добавляет logkit сам.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Оборачивает нижележащее ASGI-приложение."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Не-HTTP-скоупы (lifespan, websocket) пропускает без изменений."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        incoming = header(scope, requestid.HEADER)
        rid = incoming if requestid.valid(incoming) else requestid.new()
        observed = tap(scope, send)
        observed.set_header(requestid.HEADER, rid)
        token = requestid.bind(rid)
        try:
            await self._app(scope, receive, observed.send)
        finally:
            requestid.unbind(token)


class OTelMiddleware:
    """Открывает server-спан `METHOD /route/{pattern}` и подхватывает входящий traceparent.

    Пути из `quiet_paths` спана не открывают. Спан охватывает всю остальную цепочку, поэтому
    access-log и panic-log несут `trace_id`.
    """

    def __init__(self, app: ASGIApp, quiet_paths: Iterable[str] = ()) -> None:
        """`quiet_paths` — точные пути проб оркестратора, которым трейсы не нужны."""
        self._app = app
        self._quiet = frozenset(p for p in quiet_paths if p)
        self._tracer = otelkit.tracer(KIT)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Имя спана уточняется после ответа, когда шаблон маршрута уже известен."""
        if scope["type"] != "http" or scope.get("path", "") in self._quiet:
            await self._app(scope, receive, send)
            return

        method = method_label(str(scope.get("method", "")))
        carrier = {
            bytes(name).decode("latin-1"): bytes(value).decode("latin-1")
            for name, value in scope.get("headers", ())
        }
        observed = tap(scope, send)
        with self._tracer.start_as_current_span(
            method, context=propagate.extract(carrier), kind=SpanKind.SERVER
        ) as span:
            span.set_attribute("http.request.method", method)
            span.set_attribute("url.path", str(scope.get("path", "")))
            try:
                await self._app(scope, receive, observed.send)
            finally:
                route = route_label(scope)
                if route != UNMATCHED:
                    span.update_name(f"{method} {route}")
                span.set_attribute("http.response.status_code", observed.status)
                if observed.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    span.set_status(Status(StatusCode.ERROR))


class AccessLogMiddleware:
    """Пишет строку `http` на каждый запрос через logkit.

    Поля: `method`, `path`, `route`, `status`, `bytes`, `duration_ms` (float, мс). Уровень по
    статусу: `>=500` error, `>=400` warn, иначе info; успешный ответ на `quiet_paths` — debug.
    `request_id` и `trace_id` добавляет logkit из контекста.
    """

    def __init__(self, app: ASGIApp, log: Logger, quiet_paths: Iterable[str] = ()) -> None:
        """Строка запроса пишется только через logkit, поэтому `log` обязателен."""
        self._app = app
        self._log = log
        self._quiet = frozenset(p for p in quiet_paths if p)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Строка пишется и когда снизу прилетело исключение."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = time.perf_counter()
        path = str(scope.get("path", ""))
        observed = tap(scope, send)
        try:
            await self._app(scope, receive, observed.send)
        finally:
            fields: dict[str, object] = {
                "method": str(scope.get("method", "")),
                "path": path,
                "route": route_label(scope),
                "status": observed.status,
                "bytes": observed.body_bytes,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            }
            self._write(observed.status, path, fields)

    def _write(self, status: int, path: str, fields: dict[str, object]) -> None:
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            self._log.error("http", **fields)
        elif status >= HTTPStatus.BAD_REQUEST:
            self._log.warn("http", **fields)
        elif path in self._quiet:
            self._log.debug("http", **fields)
        else:
            self._log.info("http", **fields)


class RecoveryMiddleware:
    """Ловит незамапленное исключение: лог `panic recovered`, `http_panics_total` и 500 клиенту.

    Клиент получает нейтральный `{"code":"INTERNAL","status":500,"detail":"internal error"}`;
    если ответ уже начат — остаётся только лог, а исключение уходит наверх.
    """

    def __init__(self, app: ASGIApp, log: Logger, instr: Instruments | None = None) -> None:
        """`instr` по умолчанию — общий набор инструментов текущего Meter."""
        self._app = app
        self._log = log
        self._instr = instr if instr is not None else instruments()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """`asyncio.CancelledError` — не ошибка обработчика и наверх уходит как есть."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        observed = tap(scope, send)
        try:
            await self._app(scope, receive, observed.send)
        except Exception as exc:
            route = route_label(scope)
            self._log.error(
                "panic recovered",
                panic=repr(exc),
                path=str(scope.get("path", "")),
                route=route,
                stack="".join(traceback.format_exception(exc)),
            )
            self._instr.panics_total.add(1, {"path": route})
            if observed.started:
                raise
            await write_error(observed.send, internal("INTERNAL", "internal error"))
