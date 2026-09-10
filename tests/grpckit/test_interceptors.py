import asyncio
from collections.abc import Callable
from typing import Any

import grpc
import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from pylibinfra.grpckit import (
    INTERNAL_MESSAGE,
    REQUEST_ID_KEY,
    Config,
    HandlerInterceptor,
    RequestIDInterceptor,
    Server,
    request_id,
)
from pylibinfra.grpckit.interceptors import _error_text, _status_code, echo_request_id
from pylibinfra.idkit import is_v7
from pylibinfra.testkit.grpctest import running_server, target

from .conftest import CaptureLogger, echo_registrar, method


async def test_incoming_request_id_is_kept_and_echoed(log: CaptureLogger) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_unary(method("RequestID"))(b"", metadata=((REQUEST_ID_KEY, "req-1"),))

        assert await call == b"req-1"
        assert dict(await call.initial_metadata())[REQUEST_ID_KEY] == "req-1"


@pytest.mark.parametrize("incoming", ["", "with space", "x" * 129])
async def test_invalid_incoming_request_id_is_replaced(incoming: str) -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        metadata = ((REQUEST_ID_KEY, incoming),) if incoming else ()
        generated = await channel.unary_unary(method("RequestID"))(b"", metadata=metadata)

    assert generated != incoming.encode()
    assert is_v7(generated.decode())


async def test_request_id_is_unbound_after_the_call() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        await channel.unary_unary(method("RequestID"))(b"")

    assert request_id() == ""


async def test_handler_metadata_is_not_broken_by_the_echo() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_unary(method("OwnMetadata"))(b"ping")

        assert await call == b"ping"
        metadata = dict(await call.initial_metadata())
        assert metadata["x-own"] == "1"
        assert REQUEST_ID_KEY not in metadata


async def test_ok_call_is_logged_at_info(log: CaptureLogger) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        await channel.unary_unary(method("Unary"))(b"ping")

    record = log.find("grpc call")
    assert record is not None
    assert record.level == "info"
    assert record.fields["grpc_method"] == method("Unary")
    assert record.fields["grpc_code"] == "OK"
    assert record.fields["duration_ms"] >= 0
    assert "error" not in record.fields


async def test_client_error_is_logged_at_warn(log: CaptureLogger) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError):
            await channel.unary_unary(method("Fail"))(b"")

    record = log.find("grpc call failed")
    assert record is not None
    assert record.level == "warn"
    assert record.fields["grpc_code"] == "NOT_FOUND"
    assert record.fields["error"] == "nope"


async def test_server_fault_is_logged_at_error(log: CaptureLogger) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError):
            await channel.unary_unary(method("Down"))(b"")

    record = log.find("grpc call failed")
    assert record is not None
    assert record.level == "error"
    assert record.fields["grpc_code"] == "UNAVAILABLE"


async def test_metrics_carry_method_and_code(read_metric: Callable[..., float]) -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        await channel.unary_unary(method("Unary"))(b"ping")
        with pytest.raises(grpc.aio.AioRpcError):
            await channel.unary_unary(method("Fail"))(b"")

    ok_attrs = {"grpc_method": method("Unary"), "grpc_code": "OK"}
    assert read_metric("grpc_server_requests_total", ok_attrs) == 1
    assert read_metric("grpc_server_request_duration_seconds", ok_attrs) == 1
    assert (
        read_metric(
            "grpc_server_requests_total",
            {"grpc_method": method("Fail"), "grpc_code": "NOT_FOUND"},
        )
        == 1
    )
    assert read_metric("grpc_server_panics_total") == 0


async def test_server_span_carries_the_method(span_exporter: InMemorySpanExporter) -> None:
    async with (
        running_server(echo_registrar()) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        await channel.unary_unary(method("Unary"))(b"ping")

    span = next(s for s in span_exporter.get_finished_spans() if s.name == method("Unary"))
    assert span.kind is SpanKind.SERVER
    assert span.attributes is not None
    assert span.attributes["rpc.system"] == "grpc"
    assert span.attributes["rpc.service"] == "test.Echo"
    assert span.attributes["rpc.method"] == "Unary"
    assert span.attributes["rpc.grpc.status_code"] == grpc.StatusCode.OK.value[0]


async def test_otel_adds_no_second_duration_metric(metric_reader: InMemoryMetricReader) -> None:
    async with (
        running_server(echo_registrar()) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        await channel.unary_unary(method("Unary"))(b"ping")

    data = metric_reader.get_metrics_data()
    assert data is not None
    emitted = {
        metric.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }
    # RED считает сам кит: otel-grpc серверных метрик не эмитит, дублей `rpc.server.*` нет.
    assert "grpc_server_requests_total" in emitted
    assert "grpc_server_request_duration_seconds" in emitted
    assert not {name for name in emitted if name.startswith("rpc.")}


async def test_trace_filter_skips_the_span_but_keeps_the_metric(
    span_exporter: InMemorySpanExporter, read_metric: Callable[..., float]
) -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(
        cfg,
        services=(echo_registrar(),),
        trace_filter=lambda details: not str(details.method).endswith("/Unary"),
    )
    await server.serve()
    try:
        async with grpc.aio.insecure_channel(target(cfg, server)) as channel:
            await channel.unary_unary(method("Unary"))(b"ping")
            stream = channel.unary_stream(method("Stream"))(b"")
            assert [message async for message in stream] == [b"1", b"2"]
    finally:
        await server.stop()

    traced = {span.name for span in span_exporter.get_finished_spans()}
    assert method("Unary") not in traced
    assert method("Stream") in traced
    # Фильтр снимает только трассировку — access-log и RED-метрики остаются.
    assert read_metric("grpc_server_requests_total", {"grpc_method": method("Unary")}) == 1


async def test_unhandled_exception_is_sanitized(
    read_metric: Callable[..., float], log: CaptureLogger
) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_unary(method("Boom"))(b"")
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await call
        echoed = dict(await call.initial_metadata())

    error = exc_info.value
    assert error.code() == grpc.StatusCode.INTERNAL
    assert error.details() == INTERNAL_MESSAGE
    assert "internal detail" not in error.details()
    # Клиент получает request-id даже у сорвавшегося вызова.
    assert REQUEST_ID_KEY in echoed

    assert read_metric("grpc_server_panics_total", {"grpc_method": method("Boom")}) == 1
    recovered = log.find("grpc panic recovered")
    assert recovered is not None
    assert recovered.level == "error"
    assert "internal detail leaks nowhere" in recovered.fields["error"]
    assert "Traceback" in recovered.fields["stack"]
    # Тот же вызов попадает и в access-log — как INTERNAL.
    call_log = log.find("grpc call failed")
    assert call_log is not None
    assert call_log.fields["grpc_code"] == "INTERNAL"


async def test_downstream_rpc_error_never_reaches_the_client(
    read_metric: Callable[..., float], log: CaptureLogger
) -> None:
    async with (
        running_server(echo_registrar(), log=log) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Downstream"))(b"")

    # Ошибка исходящего вызова — такая же авария, как ValueError: её текст несёт
    # внутренние детали чужого сервиса и наружу не уходит.
    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    assert exc_info.value.details() == INTERNAL_MESSAGE
    assert "db-7" not in str(exc_info.value.details())

    assert read_metric("grpc_server_panics_total", {"grpc_method": method("Downstream")}) == 1
    recovered = log.find("grpc panic recovered")
    assert recovered is not None
    assert "db-7" in recovered.fields["error"]
    # Access-log показывает код, который получил клиент, а не код чужого сервиса.
    call_log = log.find("grpc call failed")
    assert call_log is not None
    assert call_log.fields["grpc_code"] == "INTERNAL"


async def test_intentional_abort_is_not_a_panic(read_metric: Callable[..., float]) -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Fail"))(b"")

    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    assert exc_info.value.details() == "nope"
    assert read_metric("grpc_server_panics_total") == 0


async def test_stream_is_observed_and_echoed(
    read_metric: Callable[..., float], log: CaptureLogger
) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_stream(method("Stream"))(b"")
        received = [message async for message in call]
        echoed = dict(await call.initial_metadata())

    assert received == [b"1", b"2"]
    assert REQUEST_ID_KEY in echoed
    assert (
        read_metric(
            "grpc_server_requests_total", {"grpc_method": method("Stream"), "grpc_code": "OK"}
        )
        == 1
    )
    record = log.find("grpc call")
    assert record is not None
    assert record.fields["grpc_method"] == method("Stream")


async def test_stream_exception_is_sanitized(read_metric: Callable[..., float]) -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_stream(method("StreamBoom"))(b"")
        received: list[bytes] = []

        async def drain() -> None:
            async for message in call:
                received.append(message)

        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await drain()

    assert received == [b"1"]
    assert exc_info.value.code() == grpc.StatusCode.INTERNAL
    assert exc_info.value.details() == INTERNAL_MESSAGE
    assert read_metric("grpc_server_panics_total", {"grpc_method": method("StreamBoom")}) == 1


async def test_stream_abort_passes_through(read_metric: Callable[..., float]) -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_stream(method("StreamFail"))(b"")

        async def drain() -> None:
            async for _ in call:
                pass

        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await drain()

    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    assert read_metric("grpc_server_panics_total") == 0


async def test_consumer_interceptor_runs_innermost() -> None:
    seen: list[str] = []

    class Recorder(grpc.aio.ServerInterceptor):  # type: ignore[misc]
        async def intercept_service(self, continuation: Any, details: Any) -> Any:
            handler = await continuation(details)
            inner = handler.unary_unary

            async def behavior(request: bytes, context: Any) -> bytes:
                seen.append(request_id())
                return await inner(request, context)

            return grpc.unary_unary_rpc_method_handler(
                behavior, handler.request_deserializer, handler.response_serializer
            )

    async with (
        running_server(echo_registrar(), interceptors=(Recorder(),)) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        await channel.unary_unary(method("Unary"))(b"ping", metadata=((REQUEST_ID_KEY, "req-9"),))

    # Интерсептор консьюмера стоит после request-id: id вызова ему уже виден.
    assert seen == ["req-9"]


async def test_client_streaming_and_bidi_keep_their_shape(
    read_metric: Callable[..., float],
) -> None:
    async def messages() -> Any:
        yield b"a"
        yield b"b"

    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.stream_unary(method("Collect"))(messages()) == b"ab"

        piped = channel.stream_stream(method("Pipe"))(messages())
        assert [message async for message in piped] == [b"a", b"b"]

    assert (
        read_metric(
            "grpc_server_requests_total", {"grpc_method": method("Collect"), "grpc_code": "OK"}
        )
        == 1
    )
    assert (
        read_metric(
            "grpc_server_requests_total", {"grpc_method": method("Pipe"), "grpc_code": "OK"}
        )
        == 1
    )


async def test_empty_stream_still_echoes_the_request_id() -> None:
    async with (
        running_server(echo_registrar()) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_stream(method("StreamEmpty"))(b"")
        assert [message async for message in call] == []

        assert REQUEST_ID_KEY in dict(await call.initial_metadata())


async def test_unknown_method_passes_through_the_chain(log: CaptureLogger) -> None:
    async with (
        running_server(echo_registrar(), log=log) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary("/test.Echo/Missing")(b"")

    assert exc_info.value.code() == grpc.StatusCode.UNIMPLEMENTED
    # Метода нет — интерсепторы кита не оборачивают ничего и не пишут access-log.
    assert log.calls() == []


async def test_handler_interceptor_defaults_pass_calls_through() -> None:
    class PassThrough(HandlerInterceptor):  # type: ignore[misc]
        """Интерсептор консьюмера без переопределений — обе формы вызова проходят как есть."""

    async with (
        running_server(echo_registrar(), interceptors=(PassThrough(),)) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
        stream = channel.unary_stream(method("Stream"))(b"")
        assert [message async for message in stream] == [b"1", b"2"]


async def test_echo_is_skipped_without_a_bound_request_id() -> None:
    sent: list[Any] = []

    class FakeContext:
        async def send_initial_metadata(self, metadata: Any) -> None:
            sent.append(metadata)

    await echo_request_id(FakeContext())

    assert sent == []


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (None, grpc.StatusCode.OK),
        (asyncio.CancelledError(), grpc.StatusCode.CANCELLED),
        (RuntimeError("boom"), grpc.StatusCode.UNKNOWN),
        (grpc.RpcError(), grpc.StatusCode.UNKNOWN),
    ],
)
def test_status_code_of_a_call_without_explicit_code(
    exc: BaseException | None, expected: grpc.StatusCode
) -> None:
    class FakeContext:
        def code(self) -> None:
            return None

        def details(self) -> str:
            return ""

    assert _status_code(FakeContext(), exc) is expected


def test_status_code_reads_the_code_of_a_call_error() -> None:
    class CallError(Exception):
        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.DEADLINE_EXCEEDED

    class FakeContext:
        def code(self) -> None:
            return None

    assert _status_code(FakeContext(), CallError()) is grpc.StatusCode.DEADLINE_EXCEEDED


def test_error_text_prefers_details_then_exception() -> None:
    class WithDetails:
        def details(self) -> str:
            return "from context"

    class WithoutDetails:
        def details(self) -> str:
            return ""

    assert _error_text(WithDetails(), None) == "from context"
    assert _error_text(WithoutDetails(), None) == ""
    assert _error_text(WithoutDetails(), RuntimeError("boom")) == "boom"
    assert _error_text(WithoutDetails(), TimeoutError()) == "TimeoutError"


async def test_handler_interceptor_supports_writer_style_handlers() -> None:
    # Форма reader/writer: ответы пишет сам handler. grpc её различает по поведению,
    # поэтому обёртка обязана остаться корутиной.
    async def writer(request: bytes, context: Any) -> None:
        await context.write(b"1")
        await context.write(b"2")

    server = grpc.aio.server(interceptors=[RequestIDInterceptor()])
    handlers = {"Writer": grpc.unary_stream_rpc_method_handler(writer)}
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Echo", handlers),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            call = channel.unary_stream(method("Writer"))(b"")
            assert [message async for message in call] == [b"1", b"2"]
    finally:
        await server.stop(1)
