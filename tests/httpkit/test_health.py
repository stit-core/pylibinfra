import asyncio
from collections.abc import Callable

from fastapi import FastAPI

from pylibinfra import healthkit
from pylibinfra.httpkit import Config, register_health
from pylibinfra.testkit.logtest import Capture

from .conftest import client, one


def _probe(name: str, fn: Callable[[], object]) -> healthkit.Probe:
    async def check() -> None:
        fn()

    return healthkit.new(name, check)


async def test_health_is_always_ok(make_app: Callable[..., FastAPI], log: Capture) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_without_probes_omits_list(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    async with client(make_app(Config(), log)) as c:
        resp = await c.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_lists_probe_statuses(make_app: Callable[..., FastAPI], log: Capture) -> None:
    probes = (_probe("pgkit", lambda: None), _probe("cachekit", lambda: None))
    async with client(make_app(Config(), log, probes=probes)) as c:
        resp = await c.get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "probes": [{"name": "pgkit", "status": "ok"}, {"name": "cachekit", "status": "ok"}],
    }


async def test_ready_503_and_error_text_stays_in_log(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    def boom() -> None:
        raise RuntimeError("dsn=postgres://user:pass@host/db")

    probes = (_probe("pgkit", boom), _probe("cachekit", lambda: None))
    async with client(make_app(Config(), log, probes=probes)) as c:
        resp = await c.get("/ready")

    assert resp.status_code == 503
    assert resp.json() == {
        "status": "unhealthy",
        "probes": [{"name": "pgkit", "status": "down"}, {"name": "cachekit", "status": "ok"}],
    }
    assert "postgres://" not in resp.text
    failed = one(log, "readiness probe failed")
    assert failed.level == "warn"
    assert failed.value("probe") == "pgkit"
    assert "postgres://" in failed.value("err")


async def test_ready_runs_probes_in_parallel(log: Capture) -> None:
    running = 0
    peak = 0

    def make(name: str) -> healthkit.Probe:
        async def check() -> None:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1

        return healthkit.new(name, check)

    app = FastAPI()
    register_health(app, log, 5.0, (make("a"), make("b"), make("c")))
    async with client(app) as c:
        resp = await c.get("/ready")

    assert resp.status_code == 200
    assert peak == 3


async def test_ready_probe_timeout(log: Capture) -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    app = FastAPI()
    register_health(app, log, 0.01, (healthkit.new("slow", hang),))
    async with client(app) as c:
        resp = await c.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["probes"] == [{"name": "slow", "status": "down"}]
    assert "timed out" in one(log, "readiness probe failed").value("err")


async def test_httpkit_probe_is_healthkit_probe() -> None:
    from pylibinfra.httpkit import Probe

    assert Probe is healthkit.Probe
