from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import httpclient

from .conftest import Received, cfg, respond, send_chunk, serve, start_events

README = Path(httpclient.__file__).with_name("README.md")


class AppConfigs(Provider):
    scope = Scope.APP

    @provide
    def http_config(self) -> httpclient.Config:
        return httpclient.new_config()


class Transport(Provider):
    scope = Scope.APP

    @provide
    def transport(self) -> httpclient.OptionalTransport:
        return httpx.MockTransport(lambda _: httpx.Response(200, json={"items": []}))


class RateLimited(Provider):
    scope = Scope.APP

    def __init__(self, seen: list[int]) -> None:
        super().__init__()
        self._seen = seen

    @provide
    def before_attempt(self) -> httpclient.OptionalBeforeAttempt:
        async def wait(attempt: int) -> None:
            self._seen.append(attempt)

        return wait


async def test_quickstart_snippet_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "https://api.example.com")
    attempts: list[int] = []
    container = make_async_container(
        httpclient.HttpClientProvider(),
        AppConfigs(),
        Transport(),
        RateLimited(attempts),
    )
    client = await container.get(httpclient.Client)

    response = await client.get("https://api.example.com/v1/items")
    items = response.json() if response.status_code == 200 else None

    assert items == {"items": []}
    assert attempts == [0]
    await container.close()


async def test_without_di_snippet_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPCLIENT_BASE_URL", "https://api.example.com")

    async with httpclient.new(httpclient.new_config()) as client:
        assert client.config.base_url == "https://api.example.com"


async def test_stream_snippet_works() -> None:
    async def handler(_: Received, writer: asyncio.StreamWriter) -> None:
        await start_events(writer)
        await send_chunk(writer, b"data: token\n\ndata: [DONE]\n\n")
        await asyncio.sleep(5)

    handled: list[str] = []
    async with serve(handler) as server, httpclient.new(cfg()) as client:
        stream = await client.stream("POST", f"{server.url}/v1/chat", json={})
        async with stream:
            async for event in stream:
                if event.data == "[DONE]":
                    break
                handled.append(event.data)

    assert handled == ["token"]


async def test_named_backends_snippet_works() -> None:
    config = httpclient.BackendsConfig(
        backends={"crm": httpclient.BackendConfig(base_url="http://crm")}
    )

    class Backends(Provider):
        scope = Scope.APP

        @provide
        def backends(self) -> httpclient.BackendsConfig:
            return config

    container = make_async_container(httpclient.ApiClientsProvider(), Backends())
    clients = await container.get(httpclient.ApiClients)

    assert isinstance(clients.get("crm"), httpclient.ApiClient)
    with pytest.raises(httpclient.UnknownBackendError):
        clients.get("nope")
    await container.close()


async def test_allow_retry_snippet_works() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200)

    config = cfg(base_url="http://svc", breaker={"failure_threshold": 0})
    async with httpclient.new(config, transport=httpx.MockTransport(handler)) as client:
        with httpclient.allow_retry():
            response = await client.post("/v1/orders", json={})

    assert response.status_code == 200
    assert calls == 2


def test_env_table_lists_every_alias() -> None:
    text = README.read_text(encoding="utf-8")
    documented = set(re.findall(r"`(HTTPCLIENT_[A-Z_]+)`", text))

    declared = {
        str(field.validation_alias)
        for model in (httpclient.Config, httpclient.BreakerConfig, httpclient.BackendsConfig)
        for field in model.model_fields.values()
        if isinstance(field.validation_alias, str)
    }

    assert declared <= documented


def test_readme_documents_every_export() -> None:
    text = README.read_text(encoding="utf-8")
    missing = [name for name in httpclient.__all__ if name not in text]

    assert missing == []


def test_readme_metric_names_match_the_kit() -> None:
    text = README.read_text(encoding="utf-8")

    for metric in (
        "http_client_request_duration_seconds",
        "http_client_retry_attempts_total",
        "http_client_breaker_transitions_total",
        "http_client_breaker_open",
    ):
        assert f"`{metric}`" in text


async def test_breaker_states_snippet_matches_behaviour() -> None:
    from datetime import UTC, datetime

    from pylibinfra.clockkit import Tickable

    tick = Tickable(datetime(2026, 1, 1, tzinfo=UTC))

    async def handler(request: Received, writer: asyncio.StreamWriter) -> None:
        await respond(writer, 200 if request.target == "/ok" else 503)

    config = cfg(max_retries=0, breaker={"failure_threshold": 1, "recovery_timeout": 30.0})
    async with serve(handler) as server, httpclient.new(config, clock=tick) as client:
        await client.get(f"{server.url}/down")
        with pytest.raises(httpclient.BreakerOpenError):
            await client.get(f"{server.url}/down")
        tick.advance(31)
        assert (await client.get(f"{server.url}/ok")).status_code == 200
