import httpx
from fastapi import Depends, FastAPI

from pylibinfra.httpkit import (
    SharedSecretConfig,
    register_error_handlers,
    shared_secret_guard,
)

_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _app(cfg: SharedSecretConfig) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/hook", dependencies=[Depends(shared_secret_guard(cfg))])
    async def hook() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_valid_secret_passes() -> None:
    app = _app(SharedSecretConfig(header=_HEADER, secret="s3cret"))
    async with _client(app) as c:
        r = await c.post("/hook", headers={_HEADER: "s3cret"})
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


async def test_wrong_secret_403() -> None:
    app = _app(SharedSecretConfig(header=_HEADER, secret="s3cret"))
    async with _client(app) as c:
        r = await c.post("/hook", headers={_HEADER: "nope"})
        assert r.status_code == 403
        assert r.json()["code"] == "FORBIDDEN"


async def test_missing_header_403() -> None:
    app = _app(SharedSecretConfig(header=_HEADER, secret="s3cret"))
    async with _client(app) as c:
        r = await c.post("/hook")
        assert r.status_code == 403


async def test_empty_secret_fails_closed() -> None:
    # Не настроено (secret="") → отбиваем всё, даже совпадающий пустой заголовок.
    app = _app(SharedSecretConfig(header=_HEADER, secret=""))
    async with _client(app) as c:
        r = await c.post("/hook", headers={_HEADER: ""})
        assert r.status_code == 403
