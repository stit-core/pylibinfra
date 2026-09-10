from typing import Any

import grpc
import pytest
from dishka import Provider, Scope, make_async_container, provide
from grpc_health.v1 import health_pb2, health_pb2_grpc

from pylibinfra import healthkit
from pylibinfra.grpckit import (
    Authenticator,
    Config,
    GrpcProvider,
    OptionalAuthenticator,
    Server,
    ServiceNames,
    Services,
)
from pylibinfra.logkit import Logger, nop

from .conftest import CaptureLogger, echo_registrar, method, stub_reflection, wait_status


class _AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, log: Logger) -> None:
        super().__init__()
        self._log = log

    @provide
    def grpc_config(self) -> Config:
        return Config.model_validate({"addr": "127.0.0.1:0", "shutdown_grace": 1.0})

    @provide
    def log(self) -> Logger:
        return self._log


class _ServicesProvider(Provider):
    scope = Scope.APP

    @provide
    def services(self) -> Services:
        return (echo_registrar(),)


class _ProbesProvider(Provider):
    scope = Scope.APP

    @provide
    def probes(self) -> healthkit.Probes:
        async def check() -> None:
            raise RuntimeError("dependency down")

        return (healthkit.new("pgkit", check),)


class _AuthProvider(Provider):
    scope = Scope.APP

    @provide
    def authenticator(self) -> OptionalAuthenticator:
        return _DenyAll()


class _DenyAll:
    async def authenticate(self, token: str) -> Any:
        raise PermissionError("nope")


async def test_provider_serves_and_stops_with_the_container(log: CaptureLogger) -> None:
    container = make_async_container(GrpcProvider(), _AppProvider(log), _ServicesProvider())

    # Рецепт: сервер резолвится на старте — dishka ничего не строит без запроса.
    server = await container.get(Server)
    assert isinstance(server, Server)

    async with grpc.aio.insecure_channel(server.addr()) as channel:
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"

    await container.close()

    assert log.find("grpckit: stopped") is not None


async def test_defaults_are_empty_bundles(log: CaptureLogger) -> None:
    container = make_async_container(GrpcProvider(), _AppProvider(log))

    assert await container.get(Services) == ()
    assert await container.get(ServiceNames) == ()
    assert await container.get(healthkit.Probes) == ()
    assert await container.get(OptionalAuthenticator) is None

    await container.close()


async def test_consumer_service_names_reach_reflection(
    log: CaptureLogger, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, ...]] = []
    stub_reflection(monkeypatch, seen)

    class _NamesProvider(Provider):
        scope = Scope.APP

        @provide
        def service_names(self) -> ServiceNames:
            return ServiceNames(("test.Echo",))

    class _ReflectionConfig(Provider):
        scope = Scope.APP

        @provide
        def grpc_config(self) -> Config:
            return Config.model_validate(
                {"addr": "127.0.0.1:0", "shutdown_grace": 1.0, "reflection": True}
            )

    container = make_async_container(
        GrpcProvider(), _AppProvider(log), _ReflectionConfig(), _NamesProvider()
    )
    await container.get(Server)

    assert seen == [
        ("test.Echo", "grpc.health.v1.Health", "grpc.reflection.v1alpha.ServerReflection")
    ]

    await container.close()


async def test_consumer_probes_drive_health(log: CaptureLogger) -> None:
    container = make_async_container(
        GrpcProvider(),
        _AppProvider(log),
        _ServicesProvider(),
        _ProbesProvider(),
    )
    server = await container.get(Server)

    async with grpc.aio.insecure_channel(server.addr()) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        status = await wait_status(stub, health_pb2.HealthCheckResponse.NOT_SERVING)

    assert status == health_pb2.HealthCheckResponse.NOT_SERVING
    assert log.find("grpckit: health not serving") is not None

    await container.close()


async def test_consumer_authenticator_is_picked_up(log: CaptureLogger) -> None:
    container = make_async_container(
        GrpcProvider(),
        _AppProvider(log),
        _ServicesProvider(),
        _AuthProvider(),
    )
    server = await container.get(Server)

    async with grpc.aio.insecure_channel(server.addr()) as channel:
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await channel.unary_unary(method("Unary"))(b"ping")

    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED

    await container.close()


async def test_authenticator_protocol_is_structural() -> None:
    assert isinstance(_DenyAll(), Authenticator)
    assert not isinstance(nop(), Authenticator)
