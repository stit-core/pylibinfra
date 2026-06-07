from collections.abc import Callable

import httpx
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from pylibinfra.httpkit import Config, IdempotencyStore, build_app, new_instruments
from pylibinfra.logkit import nop


class _MemStore:
    def __init__(self) -> None:
        self._data: dict[str, tuple[bytes, int]] = {}

    async def get(self, key: str) -> tuple[bytes | None, int, bool]:
        if key in self._data:
            body, status = self._data[key]
            return body, status, True
        return None, 0, False

    async def set(self, key: str, response: bytes, status: int) -> None:
        self._data[key] = (response, status)


def test_store_satisfies_protocol() -> None:
    assert isinstance(_MemStore(), IdempotencyStore)


async def test_first_call_invokes_handler_second_replays(
    metric_reader: InMemoryMetricReader,
    read_metric: Callable[[str], float],
) -> None:
    store = _MemStore()
    app = build_app(Config(_env_file=None), nop(), instruments=new_instruments(), store=store)

    calls = 0

    @app.post("/create")
    async def create() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"n": calls}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        headers = {"Idempotency-Key": "key-1"}
        r1 = await c.post("/create", headers=headers)
        r2 = await c.post("/create", headers=headers)

    assert calls == 1
    assert r1.json() == r2.json()
    assert read_metric("http_idempotency_total") >= 2


async def test_get_not_idempotent() -> None:
    store = _MemStore()
    app = build_app(Config(_env_file=None), nop(), instruments=new_instruments(), store=store)

    calls = 0

    @app.get("/read")
    async def read() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        headers = {"Idempotency-Key": "key-get"}
        for _ in range(3):
            await c.get("/read", headers=headers)

    assert calls == 3


async def test_no_key_bypasses_store() -> None:
    store = _MemStore()
    app = build_app(Config(_env_file=None), nop(), instruments=new_instruments(), store=store)

    calls = 0

    @app.post("/create")
    async def create() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/create")
        await c.post("/create")

    assert calls == 2
