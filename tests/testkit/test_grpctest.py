from pathlib import Path
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra import healthkit
from pylibinfra.grpckit import Config, ServiceRegistrar
from pylibinfra.testkit.grpctest import TCP, UDS, new_config, running_server, target

SERVICE = "test.Echo"


def _exists(path: Path) -> bool:
    # Синхронный хелпер: обращение к ФС из async-функции ловит линтер (ASYNC240).
    return path.exists()


def echo_registrar() -> ServiceRegistrar:
    async def echo(request: bytes, context: Any) -> bytes:
        return request

    def register(server: Any) -> None:
        handlers = {"Unary": grpc.unary_unary_rpc_method_handler(echo)}
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE, handlers),))

    return register


async def test_uds_transport_serves_and_cleans_up() -> None:
    async with running_server(echo_registrar()) as address:
        assert address.startswith("unix:")
        sock = Path(address.removeprefix("unix:"))
        assert _exists(sock)

        async with grpc.aio.insecure_channel(address) as channel:
            assert await channel.unary_unary(f"/{SERVICE}/Unary")(b"ping") == b"ping"

    assert not _exists(sock)


async def test_tcp_transport_uses_an_os_port() -> None:
    async with running_server(echo_registrar(), transport=TCP) as address:
        assert address.startswith("127.0.0.1:")
        assert not address.endswith(":0")

        async with grpc.aio.insecure_channel(address) as channel:
            assert await channel.unary_unary(f"/{SERVICE}/Unary")(b"ping") == b"ping"


async def test_tcp_port_is_released_on_exit() -> None:
    async with running_server(echo_registrar(), transport=TCP) as address:
        pass

    # Выход из контекста гасит сервер: порт свободен, вызовы больше не обслуживаются.
    async with grpc.aio.insecure_channel(address) as channel:
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(f"/{SERVICE}/Unary")(b"ping", timeout=2)

    assert exc_info.value.code() is grpc.StatusCode.UNAVAILABLE


async def _failing_body() -> str:
    async with running_server(echo_registrar(), transport=TCP) as address:
        raise RuntimeError(address)


async def test_failure_inside_the_context_still_stops_the_server() -> None:
    with pytest.raises(RuntimeError) as exc_info:
        await _failing_body()
    address = str(exc_info.value)

    async with grpc.aio.insecure_channel(address) as channel:
        with pytest.raises(grpc.aio.AioRpcError):
            await channel.unary_unary(f"/{SERVICE}/Unary")(b"ping", timeout=2)


async def test_health_is_available_immediately() -> None:
    async with (
        running_server() as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        response = await health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest())

        assert response.status == health_pb2.HealthCheckResponse.SERVING


async def test_probes_are_passed_through() -> None:
    called = False

    async def check() -> None:
        nonlocal called
        called = True

    cfg = Config.model_validate(
        {
            "addr": "127.0.0.1:0",
            "shutdown_grace": 1.0,
            "health_interval": 0.01,
            "health_probe_timeout": 0.01,
        }
    )
    async with (
        running_server(probes=(healthkit.new("dep", check),), config=cfg) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        await health_pb2_grpc.HealthStub(channel).Check(health_pb2.HealthCheckRequest())

    assert called


def test_new_config_per_transport() -> None:
    uds = new_config(UDS)
    tcp = new_config(TCP)

    assert uds.addr == ""
    assert uds.uds_path.endswith(".sock")
    assert tcp.addr == "127.0.0.1:0"
    assert tcp.uds_path == ""


def test_unknown_transport_is_rejected() -> None:
    with pytest.raises(ValueError, match="grpctest: unknown transport"):
        new_config("quic")


async def test_target_prefers_uds() -> None:
    cfg = new_config(UDS)

    async with running_server(config=cfg) as address:
        assert address == f"unix:{cfg.uds_path}"


async def test_target_normalizes_bare_port_addr() -> None:
    cfg = Config.model_validate({"addr": ":0", "shutdown_grace": 1.0})

    async with running_server(config=cfg) as address:
        assert address.startswith("127.0.0.1:")


def test_target_helper_is_exported() -> None:
    assert callable(target)
