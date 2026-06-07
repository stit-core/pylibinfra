import httpx
from fastapi import FastAPI

from pylibinfra.httpkit import register_health


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_health_endpoint_ok() -> None:
    app = FastAPI()
    register_health(app)
    async with _client(app) as c:
        r = await c.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


async def test_ready_ok_with_passing_probe() -> None:
    async def probe() -> None:
        return None

    app = FastAPI()
    register_health(app, probe)
    async with _client(app) as c:
        r = await c.get("/ready")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


async def test_ready_503_when_probe_fails() -> None:
    from pylibinfra.httpkit import register_error_handlers

    async def probe() -> None:
        raise RuntimeError("db down")

    app = FastAPI()
    register_error_handlers(app)
    register_health(app, probe)
    async with _client(app) as c:
        r = await c.get("/ready")
        assert r.status_code == 503
        assert r.json()["code"] == "UNHEALTHY"
