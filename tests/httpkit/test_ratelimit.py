from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import FastAPI

from pylibinfra.clockkit import Tickable
from pylibinfra.httpkit import Config, RateLimitConfig, RateLimiter, client_ip
from pylibinfra.httpkit.ratelimit import IDLE_TTL, SWEEP_EVERY
from pylibinfra.testkit.logtest import Capture

from .conftest import client

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _scope(peer: str = "192.0.2.10", **headers: list[str]) -> dict[str, object]:
    raw: list[tuple[bytes, bytes]] = []
    for name, values in headers.items():
        for value in values:
            raw.append((name.replace("_", "-").encode(), value.encode()))
    return {"type": "http", "client": (peer, 5555), "headers": raw}


def test_client_ip_without_proxy_trust() -> None:
    scope = _scope(x_forwarded_for=["203.0.113.7, 10.0.0.1"], x_real_ip=["203.0.113.9"])
    assert client_ip(scope, False) == "192.0.2.10"


def test_client_ip_takes_rightmost_forwarded_value() -> None:
    scope = _scope(x_forwarded_for=["203.0.113.7, 10.0.0.1"])
    assert client_ip(scope, True) == "10.0.0.1"


def test_client_ip_takes_last_forwarded_header() -> None:
    scope = _scope(x_forwarded_for=["203.0.113.7, 198.51.100.77", "10.0.0.1"])
    assert client_ip(scope, True) == "10.0.0.1"


def test_client_ip_falls_back_to_real_ip_then_peer() -> None:
    scope = _scope(x_forwarded_for=["10.0.0.1, not-an-ip"], x_real_ip=["203.0.113.9"])
    assert client_ip(scope, True) == "203.0.113.9"
    assert client_ip(_scope(x_real_ip=["spoofed"]), True) == "192.0.2.10"
    assert client_ip({"type": "http", "headers": []}, True) == ""


def test_bucket_refills_over_time() -> None:
    clock = Tickable(_START)
    limiter = RateLimiter(RateLimitConfig(rps=1, burst=1), clock)
    assert limiter.allow("c") is True
    assert limiter.allow("c") is False
    clock.advance(1.0)
    assert limiter.allow("c") is True


def test_burst_defaults_to_rps() -> None:
    limiter = RateLimiter(RateLimitConfig(rps=3, burst=0), Tickable(_START))
    assert sum(limiter.allow("c") for _ in range(5)) == 3


def test_idle_buckets_are_evicted() -> None:
    clock = Tickable(_START)
    limiter = RateLimiter(RateLimitConfig(rps=10, burst=10), clock)
    for i in range(100):
        limiter.allow(f"10.0.0.{i}")
    assert limiter.size() == 100

    clock.advance(IDLE_TTL + SWEEP_EVERY)
    limiter.allow("fresh")
    assert limiter.size() == 1


async def test_middleware_rejects_over_burst(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    cfg = Config(rate_limit={"rps": 1, "burst": 1})
    async with client(make_app(cfg, log)) as c:
        first = await c.get("/ok")
        second = await c.get("/ok")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert second.headers["retry-after"] == "1"
    assert second.headers["x-content-type-options"] == "nosniff"
    assert read_metric("http_rate_limited_total") == 1
    assert read_metric("http_requests_total", {"status": "429"}) == 1


async def test_middleware_disabled_when_rps_zero(
    make_app: Callable[..., FastAPI], log: Capture
) -> None:
    cfg = Config(rate_limit={"rps": 0})
    async with client(make_app(cfg, log)) as c:
        statuses = [(await c.get("/ok")).status_code for _ in range(5)]
    assert statuses == [200] * 5
