"""Серверные интерсепторы кита: request-id, access-log с метриками, recovery.

Порядок в цепочке — снаружи внутрь: OTel-спан, request-id, access-log, recovery,
auth, интерсепторы консьюмера (собирается в `server.build_interceptors`).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import grpc
from grpc.aio import ServerInterceptor

from pylibinfra._internal import requestid
from pylibinfra.logkit import Logger

from .metrics import Instruments
from .status import is_server_fault

# Что уходит клиенту вместо необработанного исключения — без внутренних деталей.
INTERNAL_MESSAGE = "internal error"
# gRPC приводит ключи metadata к нижнему регистру.
REQUEST_ID_KEY = requestid.HEADER.lower()

# Статус вызова handler задаёт через `context.abort` (он поднимает AbortError, не
# наследника RpcError). Всё остальное — авария: `grpc.RpcError` от исходящего
# вызова тоже, иначе grpc отдал бы клиенту его текст под UNKNOWN.
_INTENTIONAL = (grpc.aio.AbortError, asyncio.CancelledError)

_Continuation = Callable[[Any], Awaitable[Any]]


class HandlerInterceptor(ServerInterceptor):  # type: ignore[misc]  # grpc.aio = Any
    """База интерсептора: подменяет поведение handler'а, сохраняя его форму (unary/stream).

    Подкласс переопределяет `call_unary`/`call_stream` — они выполняются уже внутри
    RPC, поэтому видят request-id и identity вызова (в отличие от
    `intercept_service`, который отрабатывает на разборе handler'а).
    """

    async def intercept_service(self, continuation: _Continuation, details: Any) -> Any:
        """Оборачивает handler метода; отсутствующий handler пропускается как есть."""
        handler = await continuation(details)
        if handler is None:
            return handler
        method = str(details.method)
        if handler.response_streaming:
            inner_stream = (
                handler.stream_stream if handler.request_streaming else handler.unary_stream
            )
            if inspect.iscoroutinefunction(inner_stream):
                # Handler в reader/writer-форме (так написан aio-health): ответы он шлёт
                # через context.write, а grpc отличает форму по самому поведению —
                # обёртка обязана остаться корутиной, иначе RPC падает.
                async def writer(request: Any, context: Any) -> None:
                    written = self.call_stream(_as_stream(inner_stream), method, request, context)
                    async for _ in written:
                        pass

                return _rebuild(handler, writer)

            async def stream(request: Any, context: Any) -> AsyncIterator[Any]:
                async for response in self.call_stream(inner_stream, method, request, context):
                    yield response

            return _rebuild(handler, stream)

        inner_unary = handler.stream_unary if handler.request_streaming else handler.unary_unary

        async def unary(request: Any, context: Any) -> Any:
            return await self.call_unary(inner_unary, method, request, context)

        return _rebuild(handler, unary)

    async def call_unary(self, inner: Any, method: str, request: Any, context: Any) -> Any:
        """Вызов с унарным ответом; подкласс добавляет своё поведение вокруг `inner`."""
        return await inner(request, context)

    async def call_stream(
        self, inner: Any, method: str, request: Any, context: Any
    ) -> AsyncIterator[Any]:
        """Вызов с потоковым ответом; подкласс добавляет своё поведение вокруг `inner`."""
        async for response in inner(request, context):
            yield response


class RequestIDInterceptor(HandlerInterceptor):
    """Кладёт request-id вызова в контекст и отдаёт его клиенту в initial metadata."""

    async def call_unary(self, inner: Any, method: str, request: Any, context: Any) -> Any:
        """Привязывает id на время вызова; эхо — после handler'а, чтобы не занять его metadata."""
        request_id = _incoming_request_id(context)
        token = requestid.bind(request_id)
        try:
            return await inner(request, context)
        finally:
            # Снимаем контекст до эха: отмена вызова прервала бы отправку metadata,
            # и id остался бы привязанным к задаче.
            requestid.unbind(token)
            await echo_request_id(context, request_id)

    async def call_stream(
        self, inner: Any, method: str, request: Any, context: Any
    ) -> AsyncIterator[Any]:
        """То же для потока: эхо уходит перед первым сообщением."""
        request_id = _incoming_request_id(context)
        token = requestid.bind(request_id)
        echoed = False
        try:
            async for response in inner(request, context):
                if not echoed:
                    await echo_request_id(context, request_id)
                    echoed = True
                yield response
            if not echoed:
                await echo_request_id(context, request_id)
        finally:
            requestid.unbind(token)


class AccessLogInterceptor(HandlerInterceptor):
    """Одна запись и одна точка метрик на вызов; уровень записи — по gRPC-коду."""

    def __init__(self, log: Logger, instruments: Instruments) -> None:
        self._log = log
        self._m = instruments

    async def call_unary(self, inner: Any, method: str, request: Any, context: Any) -> Any:
        """Меряет вызов и логирует его исход, каким бы ни был выход из handler'а."""
        start = time.perf_counter()
        try:
            response = await inner(request, context)
        except BaseException as exc:
            self._observe(method, context, start, exc)
            raise
        self._observe(method, context, start, None)
        return response

    async def call_stream(
        self, inner: Any, method: str, request: Any, context: Any
    ) -> AsyncIterator[Any]:
        """То же для потока: длительность считается до закрытия стрима."""
        start = time.perf_counter()
        try:
            async for response in inner(request, context):
                yield response
        except BaseException as exc:
            self._observe(method, context, start, exc)
            raise
        self._observe(method, context, start, None)

    def _observe(self, method: str, context: Any, start: float, exc: BaseException | None) -> None:
        seconds = time.perf_counter() - start
        code = _status_code(context, exc)
        attrs = {"grpc_method": method, "grpc_code": code.name}
        self._m.requests.add(1, attrs)
        self._m.duration.record(seconds, attrs)

        fields: dict[str, Any] = {
            "grpc_method": method,
            "grpc_code": code.name,
            "duration_ms": round(seconds * 1000, 2),
        }
        if code is grpc.StatusCode.OK:
            self._log.info("grpc call", **fields)
            return
        fields["error"] = _error_text(context, exc)
        if is_server_fault(code):
            self._log.error("grpc call failed", **fields)
        else:
            self._log.warn("grpc call failed", **fields)


class RecoveryInterceptor(HandlerInterceptor):
    """Необработанное исключение handler'а → метрика, лог со стеком и `INTERNAL` наружу."""

    def __init__(self, log: Logger, instruments: Instruments) -> None:
        self._log = log
        self._m = instruments

    async def call_unary(self, inner: Any, method: str, request: Any, context: Any) -> Any:
        """Пропускает намеренные статусы и отмену, гасит всё остальное."""
        try:
            return await inner(request, context)
        except _INTENTIONAL:
            raise
        except Exception as exc:
            await self._recover(method, exc, context)
            raise  # недостижимо: abort поднимает AbortError

    async def call_stream(
        self, inner: Any, method: str, request: Any, context: Any
    ) -> AsyncIterator[Any]:
        """То же для потока: исключение на любом сообщении завершает стрим `INTERNAL`."""
        try:
            async for response in inner(request, context):
                yield response
        except _INTENTIONAL:
            raise
        except Exception as exc:
            await self._recover(method, exc, context)
            raise  # недостижимо

    async def _recover(self, method: str, exc: Exception, context: Any) -> None:
        self._m.panics.add(1, {"grpc_method": method})
        self._log.error(
            "grpc panic recovered",
            grpc_method=method,
            error=repr(exc),
            stack=traceback.format_exc(),
        )
        await echo_request_id(context)
        await context.abort(grpc.StatusCode.INTERNAL, INTERNAL_MESSAGE)


async def echo_request_id(context: Any, request_id: str = "") -> None:
    """Отдаёт клиенту `x-request-id`, если initial metadata ещё не ушла."""
    request_id = request_id or requestid.current()
    if not request_id:
        return
    # Свою initial metadata мог отправить handler (или вызов уже прерван abort'ом):
    # эхо необязательно, ломать из-за него вызов нельзя.
    with contextlib.suppress(Exception):
        await context.send_initial_metadata(((REQUEST_ID_KEY, request_id),))


def request_id() -> str:
    """Request-id текущего вызова; пустая строка вне обработчика."""
    return requestid.current()


def metadata_value(context: Any, key: str) -> str:
    """Первое значение metadata по ключу (ключи gRPC — в нижнем регистре); нет — пустая строка."""
    for name, value in context.invocation_metadata() or ():
        if name == key and isinstance(value, str):
            return value
    return ""


def _incoming_request_id(context: Any) -> str:
    incoming = metadata_value(context, REQUEST_ID_KEY)
    return incoming if requestid.valid(incoming) else requestid.new()


def _status_code(context: Any, exc: BaseException | None) -> grpc.StatusCode:
    code = context.code()
    if code is not None and code is not grpc.StatusCode.OK:
        return code
    if exc is None:
        return grpc.StatusCode.OK
    if isinstance(exc, asyncio.CancelledError):
        return grpc.StatusCode.CANCELLED
    # Отказ без выставленного статуса: у ошибки вызова код читается её методом,
    # у остальных исключений его нет — они остаются UNKNOWN.
    code_of = getattr(exc, "code", None)
    resolved = code_of() if callable(code_of) else None
    return resolved if isinstance(resolved, grpc.StatusCode) else grpc.StatusCode.UNKNOWN


def _error_text(context: Any, exc: BaseException | None) -> str:
    details = context.details()
    if details:
        return str(details)
    if exc is None:
        return ""
    return str(exc) or type(exc).__name__


def _as_stream(inner: Any) -> Callable[..., AsyncIterator[Any]]:
    """Показывает reader/writer-handler как пустой async-генератор для общей обёртки."""

    async def stream(request: Any, context: Any) -> AsyncIterator[Any]:
        await inner(request, context)
        return
        yield  # генератор без элементов: сообщения handler пишет сам

    return stream


def _rebuild(handler: Any, behavior: Callable[..., Any]) -> Any:
    request_streaming, response_streaming = handler.request_streaming, handler.response_streaming
    deserializer, serializer = handler.request_deserializer, handler.response_serializer
    if request_streaming and response_streaming:
        return grpc.stream_stream_rpc_method_handler(behavior, deserializer, serializer)
    if request_streaming:
        return grpc.stream_unary_rpc_method_handler(behavior, deserializer, serializer)
    if response_streaming:
        return grpc.unary_stream_rpc_method_handler(behavior, deserializer, serializer)
    return grpc.unary_unary_rpc_method_handler(behavior, deserializer, serializer)
