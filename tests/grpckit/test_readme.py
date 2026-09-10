"""Быстрый старт README grpckit — исполняемый: App-Provider, lifecycle, запуск без DI."""

import grpc
import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import healthkit
from pylibinfra.grpckit import Config, GrpcProvider, Server, Services, new_config
from pylibinfra.healthkit import Probes
from pylibinfra.logkit import Logger, nop

from .conftest import echo_registrar, method


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def grpc_config(self) -> Config:
        return new_config()

    @provide
    def services(self) -> Services:
        return (echo_registrar(),)

    @provide
    def probes(self) -> Probes:
        async def check() -> None:
            return None

        return (healthkit.new("dep", check),)

    @provide
    def log(self) -> Logger:
        return nop()


async def test_readme_quickstart(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_ADDR", "127.0.0.1:0")
    monkeypatch.setenv("GRPC_SHUTDOWN_GRACE", "1")

    container = make_async_container(GrpcProvider(), AppProvider())

    # Рецепт из README: сервер резолвится на старте, иначе порт не занят.
    server = await container.get(Server)

    async with grpc.aio.insecure_channel(server.addr()) as channel:
        assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"

    await container.close()


async def test_readme_without_di(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRPC_ADDR", "127.0.0.1:0")
    monkeypatch.setenv("GRPC_SHUTDOWN_GRACE", "1")

    server = Server(new_config(), nop(), services=(echo_registrar(),))
    await server.serve()
    try:
        async with grpc.aio.insecure_channel(server.addr()) as channel:
            assert await channel.unary_unary(method("Unary"))(b"ping") == b"ping"
    finally:
        await server.stop()


def test_readme_default_addr_needs_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRPC_ADDR", raising=False)

    assert new_config().addr == ":9090"
