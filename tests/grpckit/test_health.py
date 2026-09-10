import asyncio
from collections.abc import Callable

import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra import healthkit
from pylibinfra.grpckit import OVERALL, Config, Health, Server
from pylibinfra.logkit import nop
from pylibinfra.testkit.grpctest import running_server, target

from .conftest import CaptureLogger, echo_registrar, wait_status

SERVING = health_pb2.HealthCheckResponse.SERVING
NOT_SERVING = health_pb2.HealthCheckResponse.NOT_SERVING

FAST = {"addr": "", "shutdown_grace": 1.0, "health_interval": 0.01, "health_probe_timeout": 0.01}


def fast_config(uds: str) -> Config:
    return Config.model_validate({**FAST, "uds_path": uds})


class Dependency:
    """Зависимость, готовностью которой управляет тест."""

    def __init__(self) -> None:
        self.up = True
        self.checked = asyncio.Event()

    async def check(self) -> None:
        self.checked.set()
        if not self.up:
            raise RuntimeError("dependency down")


async def test_probe_failure_flips_overall_status(log: CaptureLogger, tmp_path: object) -> None:
    dependency = Dependency()
    probe = healthkit.new("pgkit", dependency.check)
    cfg = fast_config(str(tmp_path / "health.sock"))  # type: ignore[operator]

    async with (
        running_server(echo_registrar(), probes=(probe,), config=cfg, log=log) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        assert await wait_status(stub, SERVING) == SERVING

        dependency.up = False
        assert await wait_status(stub, NOT_SERVING) == NOT_SERVING

        dependency.up = True
        assert await wait_status(stub, SERVING) == SERVING

    not_serving = log.find("grpckit: health not serving")
    assert not_serving is not None
    assert not_serving.level == "warn"
    assert not_serving.fields["probe"] == "pgkit"
    assert "dependency down" in not_serving.fields["error"]

    recovered = log.find("grpckit: health serving")
    assert recovered is not None
    assert recovered.level == "info"


async def test_hanging_probe_is_bounded_by_the_probe_timeout(
    log: CaptureLogger, tmp_path: object
) -> None:
    async def hang() -> None:
        await asyncio.sleep(30)

    cfg = fast_config(str(tmp_path / "health.sock"))  # type: ignore[operator]
    async with (
        running_server(
            echo_registrar(), probes=(healthkit.new("natskit", hang),), config=cfg, log=log
        ) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        assert await wait_status(stub, NOT_SERVING) == NOT_SERVING

    record = log.find("grpckit: health not serving")
    assert record is not None
    assert record.fields["probe"] == "natskit"
    assert "timed out" in record.fields["error"]


async def test_loop_is_off_without_probes(tmp_path: object) -> None:
    cfg = fast_config(str(tmp_path / "health.sock"))  # type: ignore[operator]

    async with (
        running_server(echo_registrar(), config=cfg) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        assert await wait_status(stub, SERVING) == SERVING


async def test_zero_interval_disables_the_loop(tmp_path: object) -> None:
    dependency = Dependency()
    dependency.up = False
    cfg = Config.model_validate(
        {
            "addr": "",
            "uds_path": str(tmp_path / "health.sock"),  # type: ignore[operator]
            "shutdown_grace": 1.0,
            "health_interval": 0,
        }
    )

    async with (
        running_server(
            echo_registrar(), probes=(healthkit.new("pgkit", dependency.check),), config=cfg
        ) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        assert await wait_status(stub, SERVING) == SERVING
        await asyncio.sleep(0.05)

        assert not dependency.checked.is_set()
        assert (await stub.Check(health_pb2.HealthCheckRequest())).status == SERVING


async def test_stop_moves_everything_to_not_serving() -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(cfg)
    await server.serve()
    await server.set_serving("test.Echo", True)
    address = target(cfg, server)
    try:
        async with grpc.aio.insecure_channel(address) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            assert (await stub.Check(health_pb2.HealthCheckRequest())).status == SERVING

            # Health гасится до закрытия портов: оркестратор успевает увести трафик.
            await server._health.stop()

            overall = await stub.Check(health_pb2.HealthCheckRequest())
            named = await stub.Check(health_pb2.HealthCheckRequest(service="test.Echo"))
            assert overall.status == NOT_SERVING
            assert named.status == NOT_SERVING
    finally:
        await server.stop()


async def test_health_unit_start_and_stop_are_idempotent() -> None:
    async def ok() -> None:
        return None

    cfg = Config.model_validate({**FAST, "addr": "127.0.0.1:0"})
    health = Health(cfg, nop(), probes=(healthkit.new("x", ok),))

    health.start()
    health.start()
    await health.stop()
    await health.stop()


@pytest.mark.parametrize("service", [OVERALL, "test.Echo"])
async def test_set_serving_accepts_any_service(service: str) -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(cfg)
    await server.serve()
    try:
        await server.set_serving(service, False)
        async with grpc.aio.insecure_channel(target(cfg, server)) as channel:
            stub = health_pb2_grpc.HealthStub(channel)
            response = await stub.Check(health_pb2.HealthCheckRequest(service=service))
            assert response.status == NOT_SERVING
    finally:
        await server.stop()


async def test_check_of_an_unknown_service_is_not_a_panic(
    read_metric: Callable[..., float], log: CaptureLogger
) -> None:
    async with (
        running_server(echo_registrar(), log=log) as address,
        grpc.aio.insecure_channel(address) as channel,
    ):
        stub = health_pb2_grpc.HealthStub(channel)
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.Check(health_pb2.HealthCheckRequest(service="nope"))

    # Abort стандартного health-сервиса — намеренный статус, а не авария.
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    assert read_metric("grpc_server_panics_total") == 0
    record = log.find("grpc call failed")
    assert record is not None
    assert record.level == "warn"
    assert record.fields["grpc_code"] == "NOT_FOUND"


async def test_watch_streams_status_changes() -> None:
    cfg = Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})
    server = Server(cfg)
    await server.serve()
    try:
        async with grpc.aio.insecure_channel(target(cfg, server)) as channel:
            watch = health_pb2_grpc.HealthStub(channel).Watch(health_pb2.HealthCheckRequest())
            assert (await watch.read()).status == SERVING

            await server.set_serving(OVERALL, False)
            assert (await watch.read()).status == NOT_SERVING

            watch.cancel()
    finally:
        await server.stop()
