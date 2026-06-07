from collections.abc import Callable

import httpx
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.httpkit import Config, build_app, new_instruments
from pylibinfra.logkit import nop


async def test_requests_total_and_duration_incremented(
    metric_reader: InMemoryMetricReader,
    read_metric: Callable[[str], float],
) -> None:
    # instruments создаём ПОСЛЕ установки provider'а в фикстуре.
    app = build_app(Config(_env_file=None), nop(), instruments=new_instruments())

    @app.get("/api/users/{uid}")
    async def get_user(uid: str) -> dict[str, str]:
        return {"id": uid}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/users/123")
        assert r.status_code == 200

    assert read_metric("http_requests_total") >= 1
    assert read_metric("http_request_duration_seconds") >= 1


async def test_panic_metric_incremented(
    metric_reader: InMemoryMetricReader,
    read_metric: Callable[[str], float],
) -> None:
    app = build_app(Config(_env_file=None), nop(), instruments=new_instruments())

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("kaboom")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.get("/boom")

    assert read_metric("http_panics_total") >= 1


async def test_max_body_metric_incremented(
    metric_reader: InMemoryMetricReader,
    read_metric: Callable[[str], float],
) -> None:
    cfg = Config(_env_file=None, max_body={"max_bytes": 10})
    app = build_app(cfg, nop(), instruments=new_instruments())

    @app.post("/echo")
    async def echo() -> dict[str, bool]:
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/echo", content=b"a" * 100)
        assert r.status_code == 413

    assert read_metric("http_max_body_rejections_total") >= 1
