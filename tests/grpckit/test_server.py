import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra.grpckit import Config, Server
from pylibinfra.testkit.grpctest import running_server


def echo_registrar(server: grpc.aio.Server) -> None:
    # Идентичные de/ser (bytes) — не нужен codegen для теста транспорта.
    async def unary(request: bytes, context: grpc.aio.ServicerContext) -> bytes:
        return request

    handler = grpc.unary_unary_rpc_method_handler(unary)
    generic = grpc.method_handlers_generic_handler("test.Echo", {"Unary": handler})
    server.add_generic_rpc_handlers((generic,))


async def test_echo_over_uds() -> None:
    async with (
        running_server(echo_registrar) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        call = channel.unary_unary("/test.Echo/Unary")
        assert await call(b"ping") == b"ping"


async def test_health_serving() -> None:
    async with (
        running_server(echo_registrar) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        resp = await stub.Check(health_pb2.HealthCheckRequest())
        assert resp.status == health_pb2.HealthCheckResponse.SERVING


async def test_readiness_probe_failure_marks_not_serving() -> None:
    async def failing_probe() -> None:
        raise RuntimeError("dependency down")

    async with (
        running_server(echo_registrar, probes=(failing_probe,)) as target,
        grpc.aio.insecure_channel(target) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        resp = await stub.Check(health_pb2.HealthCheckRequest())
        assert resp.status == health_pb2.HealthCheckResponse.NOT_SERVING


async def test_bind_requires_a_target() -> None:
    # async: grpc.aio.server() требует event loop; bind падает ValueError'ом.
    with pytest.raises(ValueError, match="некуда биндить"):
        Server(Config())  # ни addr, ни uds_path
