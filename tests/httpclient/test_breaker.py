from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from pylibinfra.clockkit import Tickable
from pylibinfra.httpclient import BreakerConfig, BreakerOpenError, Client, State
from pylibinfra.httpclient.breaker import Breakers
from pylibinfra.httpclient.metrics import new_instruments

from .conftest import Received, cfg, mock_client, respond, serve


def clock() -> Tickable:
    return Tickable(datetime(2026, 1, 1, tzinfo=UTC))


def breakers(threshold: int = 2, recovery: float = 30.0, tick: Tickable | None = None) -> Breakers:
    return Breakers(
        BreakerConfig(failure_threshold=threshold, recovery_timeout=recovery),
        new_instruments(),
        tick if tick is not None else clock(),
    )


async def test_opens_after_consecutive_failures() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    config = cfg(max_retries=0, breaker={"failure_threshold": 5, "recovery_timeout": 30.0})
    async with mock_client(handler, config) as client:
        for _ in range(5):
            assert (await client.get("/ping")).status_code == 503
        with pytest.raises(BreakerOpenError, match="circuit breaker open"):
            await client.get("/ping")


async def test_500_from_a_live_upstream_is_not_a_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    config = cfg(max_retries=0, breaker={"failure_threshold": 2})
    async with mock_client(handler, config) as client:
        for _ in range(5):
            assert (await client.get("/ping")).status_code == 500


async def test_transport_error_is_a_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    config = cfg(max_retries=0, breaker={"failure_threshold": 2})
    async with mock_client(handler, config) as client:
        for _ in range(2):
            with pytest.raises(httpx.ConnectError):
                await client.get("/ping")
        with pytest.raises(BreakerOpenError):
            await client.get("/ping")


async def test_breaker_is_per_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503 if request.url.host == "bad" else 200)

    config = cfg(max_retries=0, base_url="", breaker={"failure_threshold": 2})
    async with mock_client(handler, config) as client:
        for _ in range(2):
            await client.get("http://bad/x")
        with pytest.raises(BreakerOpenError):
            await client.get("http://bad/x")

        assert (await client.get("http://good/x")).status_code == 200


async def test_recovery_probe_reopens_then_closes_the_circuit() -> None:
    healthy = False
    tick = clock()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if healthy else 503)

    config = cfg(max_retries=0, breaker={"failure_threshold": 2, "recovery_timeout": 30.0})
    async with mock_client(handler, config, clock=tick) as client:
        for _ in range(2):
            await client.get("/ping")
        with pytest.raises(BreakerOpenError):
            await client.get("/ping")

        # Проба в half-open проваливается — цепь снова open.
        tick.advance(31)
        assert (await client.get("/ping")).status_code == 503
        with pytest.raises(BreakerOpenError):
            await client.get("/ping")

        tick.advance(31)
        healthy = True
        assert (await client.get("/ping")).status_code == 200
        # Цепь замкнулась: следующий вызов идёт без пробы.
        assert (await client.get("/ping")).status_code == 200


async def test_zero_threshold_disables_the_breaker() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    config = cfg(max_retries=0, breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        for _ in range(10):
            assert (await client.get("/ping")).status_code == 503


async def test_caller_cancellation_does_not_open_the_circuit() -> None:
    async def handler(request: Received, writer: asyncio.StreamWriter) -> None:
        if request.target == "/slow":
            await asyncio.sleep(5)
        await respond(writer, 200)

    async with serve(handler) as server:
        config = cfg(max_retries=0, breaker={"failure_threshold": 2})
        async with Client(config) as client:
            for _ in range(3):
                task = asyncio.create_task(client.get(f"{server.url}/slow"))
                await asyncio.sleep(0.02)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert (await client.get(f"{server.url}/fast")).status_code == 200


async def test_before_attempt_error_does_not_open_the_circuit() -> None:
    reject = True

    async def hook(_: int) -> None:
        if reject:
            raise RuntimeError("rate limited")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    config = cfg(max_retries=0, breaker={"failure_threshold": 2})
    async with mock_client(handler, config, before_attempt=hook) as client:
        for _ in range(5):
            with pytest.raises(RuntimeError):
                await client.get("/ping")
        reject = False
        assert (await client.get("/ping")).status_code == 200


def test_half_open_admits_a_single_probe() -> None:
    tick = clock()
    brk = breakers(threshold=1, recovery=10.0, tick=tick)

    brk.guard("api").failure()
    assert brk.state("api") is State.OPEN

    tick.advance(11)
    probe = brk.guard("api")
    assert brk.state("api") is State.HALF_OPEN
    with pytest.raises(BreakerOpenError, match="half-open"):
        brk.guard("api")

    probe.success()
    assert brk.state("api") is State.CLOSED


def test_skipped_probe_frees_the_half_open_slot() -> None:
    tick = clock()
    brk = breakers(threshold=1, recovery=10.0, tick=tick)
    brk.guard("api").failure()
    tick.advance(11)

    brk.guard("api").skip()

    assert brk.state("api") is State.HALF_OPEN
    brk.guard("api").success()
    assert brk.state("api") is State.CLOSED


def test_success_resets_the_failure_counter() -> None:
    brk = breakers(threshold=2)

    brk.guard("api").failure()
    brk.guard("api").success()
    brk.guard("api").failure()

    assert brk.state("api") is State.CLOSED


def test_unknown_host_is_closed() -> None:
    assert breakers().state("never-called") is State.CLOSED


async def test_breaker_metrics_carry_host_label(read_metric: Callable[..., float]) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    config = cfg(max_retries=0, base_url="http://crm", breaker={"failure_threshold": 2})
    async with mock_client(handler, config) as client:
        for _ in range(2):
            await client.get("/ping")
        with pytest.raises(BreakerOpenError):
            await client.get("/ping")

    assert read_metric("http_client_breaker_open", {"host": "crm"}) == 1
    assert read_metric("http_client_breaker_open", {"host": "other"}) == 0
    assert read_metric("http_client_breaker_transitions_total", {"host": "crm", "to": "open"}) == 1


async def test_breaker_open_gauge_returns_to_zero(read_metric: Callable[..., float]) -> None:
    tick = clock()
    healthy = False

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if healthy else 503)

    config = cfg(max_retries=0, base_url="http://crm", breaker={"failure_threshold": 1})
    async with mock_client(handler, config, clock=tick) as client:
        await client.get("/ping")
        tick.advance(31)
        healthy = True
        await client.get("/ping")

    assert read_metric("http_client_breaker_open", {"host": "crm"}) == 0
    assert (
        read_metric("http_client_breaker_transitions_total", {"host": "crm", "to": "half-open"})
        == 1
    )


async def test_client_exposes_breaker_state() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    config = cfg(max_retries=0, base_url="http://crm", breaker={"failure_threshold": 1})
    async with mock_client(handler, config) as client:
        assert client.breaker_state("crm") is State.CLOSED
        await client.get("/ping")
        assert client.breaker_state("crm") is State.OPEN
