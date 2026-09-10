from collections.abc import Callable

from fastapi import FastAPI

from pylibinfra.httpkit import Config
from pylibinfra.httpkit.metrics import method_label, route_label
from pylibinfra.testkit.logtest import Capture

from .conftest import client


def test_method_label_limits_cardinality() -> None:
    assert method_label("GET") == "GET"
    assert method_label("PROPFIND") == "other"


def test_route_label_unmatched_without_route() -> None:
    assert route_label({"type": "http"}) == "unmatched"


async def test_requests_total_and_duration(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/users/1")
        await c.get("/users/2")

    attrs = {"method": "GET", "path": "/users/{uid}", "status": "200"}
    assert read_metric("http_requests_total", attrs) == 2
    assert read_metric("http_request_duration_seconds", attrs) == 2


async def test_unmatched_path_label(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/scan-me-1")
        await c.get("/scan-me-2")
    assert read_metric("http_requests_total", {"path": "unmatched", "status": "404"}) == 2


async def test_non_standard_method_becomes_other(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.request("PROPFIND", "/ok")
    assert read_metric("http_requests_total", {"method": "other"}) == 1


async def test_panic_request_is_counted(
    make_app: Callable[..., FastAPI],
    log: Capture,
    read_metric: Callable[..., float],
) -> None:
    async with client(make_app(Config(), log)) as c:
        await c.get("/boom")
    assert read_metric("http_requests_total", {"path": "/boom", "status": "500"}) == 1
