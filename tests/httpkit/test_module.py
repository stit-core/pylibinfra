import asyncio

import httpx
import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.exceptions import NoFactoryError
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import FastAPI
from starlette.middleware import Middleware

from pylibinfra import healthkit
from pylibinfra.httpkit import (
    Config,
    HttpProvider,
    ListenError,
    MemoryStore,
    Middlewares,
    OptionalStore,
    Server,
    setup,
)
from pylibinfra.logkit import Logger
from pylibinfra.testkit.logtest import Capture

from .conftest import one


class _CfgProvider(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config, log: Logger) -> None:
        super().__init__()
        self._cfg = cfg
        self._log = log

    @provide
    def cfg(self) -> Config:
        return self._cfg

    @provide
    def logger(self) -> Logger:
        return self._log


async def test_provider_builds_and_serves(log: Capture) -> None:
    container = make_async_container(HttpProvider(), _CfgProvider(Config(addr="127.0.0.1:0"), log))
    server = await container.get(Server)
    app = await container.get(FastAPI)

    assert isinstance(server, Server)
    assert app is server.app
    listening = one(log, "http server listening")
    assert listening.value("addr").startswith("127.0.0.1:")
    assert server.addr() == listening.value("addr")

    await container.close()
    assert server.addr() == ""


async def test_provider_fails_on_busy_port(log: Capture) -> None:
    import socket

    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    busy = holder.getsockname()[1]
    container = make_async_container(
        HttpProvider(), _CfgProvider(Config(addr=f"127.0.0.1:{busy}"), log)
    )
    try:
        with pytest.raises(ListenError):
            await container.get(Server)
    finally:
        holder.close()
        await container.close()


async def test_autostart_false_skips_serving(log: Capture) -> None:
    container = make_async_container(
        HttpProvider(autostart=False), _CfgProvider(Config(addr="127.0.0.1:0"), log)
    )
    server = await container.get(Server)
    assert server.autostart is False
    assert server.addr() == ""
    assert not log.has("http server listening")
    await container.close()


async def test_optional_dependencies_are_overridable(log: Capture) -> None:
    store = MemoryStore()
    probe = healthkit.new("app", lambda: _ok())

    async def _ok() -> None:
        return None

    class AppProvider(Provider):
        scope = Scope.APP

        @provide
        def store(self) -> OptionalStore:
            return store

        @provide
        def probes(self) -> healthkit.Probes:
            return (probe,)

        @provide
        def middlewares(self) -> Middlewares:
            return ()

    container = make_async_container(
        HttpProvider(),
        _CfgProvider(Config(addr="127.0.0.1:0"), log),
        AppProvider(),
    )
    server = await container.get(Server)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://t"
    ) as c:
        ready = await c.get("/ready")
    assert ready.json() == {"status": "ok", "probes": [{"name": "app", "status": "ok"}]}
    await container.close()


async def test_setup_injects_into_route() -> None:
    class Greeter:
        def hello(self) -> str:
            return "hi"

    class GreeterProvider(Provider):
        scope = Scope.APP

        @provide
        def greeter(self) -> Greeter:
            return Greeter()

    container = make_async_container(GreeterProvider())
    app = FastAPI()
    setup(app, container)

    @app.get("/greet")
    @inject
    async def greet(greeter: FromDishka[Greeter]) -> dict[str, str]:
        return {"msg": greeter.hello()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/greet")
    assert resp.json() == {"msg": "hi"}
    await container.close()


def test_consumer_middleware_type_is_a_tuple_of_starlette_middleware() -> None:
    entry = Middleware(object)  # type: ignore[arg-type]
    value: Middlewares = (entry,)
    assert value[0] is entry


def test_provider_requires_logger_in_container() -> None:
    class OnlyCfg(Provider):
        scope = Scope.APP

        @provide
        def cfg(self) -> Config:
            return Config(addr="127.0.0.1:0")

    # Logger даёт logkit, дефолта у кита нет: граф без него не собирается.
    with pytest.raises(NoFactoryError):
        make_async_container(HttpProvider(), OnlyCfg())


async def test_serve_failure_reaches_the_log(log: Capture) -> None:
    from pylibinfra.httpkit.module import _report

    async def boom() -> None:
        raise OSError("listener closed")

    task = asyncio.create_task(boom())
    with pytest.raises(OSError, match="listener closed"):
        await task
    _report(task, log)
    assert log.has("http server stopped", level="error")

    async def fine() -> None:
        return None

    ok = asyncio.create_task(fine())
    await ok
    _report(ok, log)
    assert len([e for e in log.entries() if e.msg == "http server stopped"]) == 1

    async def forever() -> None:
        await asyncio.sleep(10)

    hung = asyncio.create_task(forever())
    hung.cancel()
    with pytest.raises(asyncio.CancelledError):
        await hung
    _report(hung, log)
    assert len([e for e in log.entries() if e.msg == "http server stopped"]) == 1
