"""ObservabilityInterceptor — серверный aio-интерсептор: метрики (requests_total/
duration по method+code), access-log через logkit и recovery (необработанное
исключение хендлера → log + grpc_server_panics_total + abort INTERNAL с
санитизированным сообщением, без утечки stack/деталей наружу). Намеренные
context.abort(...) хендлера проходят насквозь с их кодом, паникой не считаются.

OTel-трейс-спаны навешивает отдельный aio_server_interceptor из
opentelemetry-instrumentation-grpc (см. server.build_interceptors) — здесь только
метрики/логи/восстановление.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable

import grpc
from grpc.aio import ServerInterceptor, ServicerContext

from pylibinfra.logkit import Logger, nop

from .metrics import GRPCInstruments, new_instruments

# Что отдаём клиенту при необработанном исключении — без внутренних деталей.
_INTERNAL_MSG = "internal error"

_HandlerCont = Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]]


def _rpc_code(err: grpc.RpcError) -> grpc.StatusCode:
    code = err.code()
    return code if code is not None else grpc.StatusCode.UNKNOWN


class ObservabilityInterceptor(ServerInterceptor):  # type: ignore[misc]  # grpc.aio = Any
    def __init__(
        self,
        log: Logger | None = None,
        instruments: GRPCInstruments | None = None,
    ) -> None:
        self._log = log if log is not None else nop()
        self._m = instruments if instruments is not None else new_instruments()

    async def intercept_service(
        self,
        continuation: _HandlerCont,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        handler = await continuation(handler_call_details)
        if handler is None:
            return handler
        method = handler_call_details.method
        return self._wrap(handler, method)

    def _observe(self, method: str, code: grpc.StatusCode, start: float) -> None:
        dur = time.perf_counter() - start
        attrs = {"grpc_method": method, "grpc_code": code.name}
        self._m.req_total.add(1, attrs)
        self._m.req_duration.record(dur, attrs)
        self._log.info(
            "grpc request",
            grpc_method=method,
            grpc_code=code.name,
            duration_ms=round(dur * 1000, 2),
        )

    async def _panic(self, method: str, exc: Exception, context: ServicerContext) -> None:
        self._m.panics_total.add(1, {"grpc_method": method})
        self._log.error("grpc handler error", grpc_method=method, error=repr(exc))
        await context.abort(grpc.StatusCode.INTERNAL, _INTERNAL_MSG)

    def _wrap(self, handler: grpc.RpcMethodHandler, method: str) -> grpc.RpcMethodHandler:
        rs, ss = handler.request_streaming, handler.response_streaming

        if not ss:  # ответ унарный (unary_unary | stream_unary)
            inner_u = handler.stream_unary if rs else handler.unary_unary

            async def unary(request: object, context: ServicerContext) -> object:
                start, code = time.perf_counter(), grpc.StatusCode.OK
                try:
                    return await inner_u(request, context)
                except grpc.aio.AbortError:
                    code = context.code() or grpc.StatusCode.UNKNOWN
                    raise
                except grpc.RpcError as err:
                    code = _rpc_code(err)
                    raise
                except Exception as exc:
                    code = grpc.StatusCode.INTERNAL
                    await self._panic(method, exc, context)
                    raise  # недостижимо: abort() поднимает AbortError
                finally:
                    self._observe(method, code, start)

            return _rebuild(handler, rs, ss, unary)

        inner_s = handler.stream_stream if rs else handler.unary_stream

        async def stream(request: object, context: ServicerContext) -> AsyncIterator[object]:
            start, code = time.perf_counter(), grpc.StatusCode.OK
            try:
                async for resp in inner_s(request, context):
                    yield resp
            except grpc.aio.AbortError:
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            except grpc.RpcError as err:
                code = _rpc_code(err)
                raise
            except Exception as exc:
                code = grpc.StatusCode.INTERNAL
                await self._panic(method, exc, context)
                raise  # недостижимо
            finally:
                self._observe(method, code, start)

        return _rebuild(handler, rs, ss, stream)


def _rebuild(
    handler: grpc.RpcMethodHandler,
    rs: bool,
    ss: bool,
    behavior: Callable[..., object],
) -> grpc.RpcMethodHandler:
    de, se = handler.request_deserializer, handler.response_serializer
    if rs and ss:
        return grpc.stream_stream_rpc_method_handler(behavior, de, se)
    if rs and not ss:
        return grpc.stream_unary_rpc_method_handler(behavior, de, se)
    if not rs and ss:
        return grpc.unary_stream_rpc_method_handler(behavior, de, se)
    return grpc.unary_unary_rpc_method_handler(behavior, de, se)
