import httpx
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.fastapi import FromDishka, inject
from fastapi import FastAPI

from pylibinfra.httpkit import Config, HttpProvider, Server, setup


class _CfgProvider(Provider):
    scope = Scope.APP

    @provide
    def cfg(self) -> Config:
        # addr с портом 0 — uvicorn возьмёт свободный, тест не привязывается к сети.
        return Config(_env_file=None, addr=":0")


async def test_provider_builds_server_and_app() -> None:
    container = make_async_container(HttpProvider(), _CfgProvider())
    server = await container.get(Server)
    assert isinstance(server, Server)
    app = await container.get(FastAPI)
    assert isinstance(app, FastAPI)
    await container.close()


class _Greeter:
    def hello(self) -> str:
        return "hi"


class _GreeterProvider(Provider):
    scope = Scope.APP

    @provide
    def greeter(self) -> _Greeter:
        return _Greeter()


async def test_setup_dishka_injects_into_route() -> None:
    container = make_async_container(_GreeterProvider())
    app = FastAPI()
    setup(app, container)

    @app.get("/greet")
    @inject
    async def greet(greeter: FromDishka[_Greeter]) -> dict[str, str]:
        return {"msg": greeter.hello()}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/greet")
        assert r.status_code == 200
        assert r.json() == {"msg": "hi"}

    await container.close()
