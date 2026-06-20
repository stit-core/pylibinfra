from collections.abc import Callable

import grpc
import pytest

from pylibinfra.testkit.grpctest import running_server


def faulty_registrar(server: grpc.aio.Server) -> None:
    async def boom(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
        raise ValueError("internal detail leaks nowhere")

    async def deny(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "nope")
        return b""  # недостижимо

    async def ok(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
        return request

    handlers = {
        "Boom": grpc.unary_unary_rpc_method_handler(boom),
        "Deny": grpc.unary_unary_rpc_method_handler(deny),
        "Ok": grpc.unary_unary_rpc_method_handler(ok),
    }
    generic = grpc.method_handlers_generic_handler("test.Faulty", handlers)
    server.add_generic_rpc_handlers((generic,))


async def test_recovery_sanitizes_unhandled_exception(read_metric: Callable[[str], float]) -> None:
    async with (
        running_server(faulty_registrar) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as ei:
            await channel.unary_unary("/test.Faulty/Boom")(b"x")
    err = ei.value
    assert err.code() == grpc.StatusCode.INTERNAL
    assert err.details() == "internal error"  # деталь исключения наружу не утекла
    assert read_metric("grpc_server_panics_total") >= 1


async def test_intentional_abort_passes_through(read_metric: Callable[[str], float]) -> None:
    async with (
        running_server(faulty_registrar) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        with pytest.raises(grpc.aio.AioRpcError) as ei:
            await channel.unary_unary("/test.Faulty/Deny")(b"x")
    assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert ei.value.details() == "nope"
    # намеренный abort паникой не считается
    assert read_metric("grpc_server_panics_total") == 0


async def test_success_records_request_metrics(read_metric: Callable[[str], float]) -> None:
    async with (
        running_server(faulty_registrar) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        assert await channel.unary_unary("/test.Faulty/Ok")(b"ping") == b"ping"
    assert read_metric("grpc_server_requests_total") >= 1
    assert read_metric("grpc_server_request_duration_seconds") >= 1
