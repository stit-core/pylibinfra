from __future__ import annotations

import asyncio

import httpx
import pytest

from pylibinfra.httpclient import Client, CrossHostRedirectError, TooManyRedirectsError

from .conftest import Received, cfg, mock_client, respond, serve


async def test_same_host_redirect_is_followed() -> None:
    async def handler(request: Received, writer: asyncio.StreamWriter) -> None:
        if request.target == "/":
            await respond(writer, 302, headers={"location": "/final"})
            return
        await respond(writer, 200, body=b"done")

    async with serve(handler) as server, Client(cfg(max_retries=0)) as client:
        response = await client.get(f"{server.url}/")

    assert response.status_code == 200
    assert response.text == "done"
    assert server.count() == 2


async def test_redirect_chain_is_capped() -> None:
    hits = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        return httpx.Response(302, headers={"Location": f"/next/{hits}"})

    config = cfg(max_retries=2, max_redirects=3, breaker={"failure_threshold": 0})
    async with mock_client(handler, config) as client:
        with pytest.raises(TooManyRedirectsError, match="limit 3"):
            await client.get("/start")

    # Перманентная петля редиректов не ретраится.
    assert hits == 3


async def test_cross_host_redirect_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "svc":
            return httpx.Response(302, headers={"Location": "http://attacker/steal"})
        return httpx.Response(200)

    async with mock_client(handler, cfg(max_retries=0)) as client:
        with pytest.raises(CrossHostRedirectError, match="svc -> attacker"):
            await client.get("/start")


async def test_cross_host_redirect_strips_secrets_when_allowed() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "svc":
            return httpx.Response(302, headers={"Location": "http://partner/next"})
        seen.update(dict(request.headers))
        return httpx.Response(200)

    config = cfg(max_retries=0, allow_cross_host_redirects=True)
    async with mock_client(handler, config) as client:
        response = await client.get(
            "/start",
            headers={
                "Authorization": "Bearer secret",
                "X-Api-Key": "secret-key",
                "X-Signature": "sig",
                "Accept": "application/json",
                "X-Request-ID": "req-1",
            },
        )

    assert response.status_code == 200
    assert "authorization" not in seen
    assert "x-api-key" not in seen
    assert "x-signature" not in seen
    assert seen["accept"] == "application/json"
    assert seen["x-request-id"] == "req-1"
    assert seen["host"] == "partner"


async def test_redirect_back_to_origin_keeps_headers() -> None:
    seen: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "http://svc/final"})
        return httpx.Response(200)

    async with mock_client(handler, cfg(max_retries=0)) as client:
        await client.get("/start", headers={"X-Api-Key": "secret-key"})

    assert seen[-1]["x-api-key"] == "secret-key"


async def test_redirect_violation_counts_as_breaker_failure() -> None:
    from pylibinfra.httpclient import BreakerOpenError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://attacker/steal"})

    config = cfg(max_retries=0, breaker={"failure_threshold": 2})
    async with mock_client(handler, config) as client:
        for _ in range(2):
            with pytest.raises(CrossHostRedirectError):
                await client.get("/start")
        with pytest.raises(BreakerOpenError):
            await client.get("/start")
