from __future__ import annotations

import httpx
import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.httpclient import (
    ApiClient,
    ApiClients,
    ApiClientsProvider,
    BackendConfig,
    BackendsConfig,
    BeforeAttempt,
    Client,
    Config,
    HttpClientProvider,
    OptionalBeforeAttempt,
    OptionalTransport,
    UnknownBackendError,
)

from .conftest import cfg


class _AppConfig(Provider):
    scope = Scope.APP

    @provide
    def config(self) -> Config:
        return cfg(max_retries=1, base_url="http://svc")


class _Transport(Provider):
    scope = Scope.APP

    @provide
    def transport(self) -> OptionalTransport:
        return httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))


class _Hook(Provider):
    scope = Scope.APP

    def __init__(self, attempts: list[int]) -> None:
        super().__init__()
        self._attempts = attempts

    @provide
    def hook(self) -> OptionalBeforeAttempt:
        async def before(attempt: int) -> None:
            self._attempts.append(attempt)

        return before


async def test_provider_builds_client_and_api() -> None:
    container = make_async_container(HttpClientProvider(), _AppConfig(), _Transport())

    client = await container.get(Client)
    api = await container.get(ApiClient)

    assert client.config.max_retries == 1
    assert await api.get("/ping") == {"ok": True}
    await container.close()


async def test_provider_closes_client_on_stop() -> None:
    container = make_async_container(HttpClientProvider(), _AppConfig(), _Transport())
    client = await container.get(Client)

    await container.close()

    with pytest.raises(RuntimeError):
        await client.get("/ping")


async def test_before_attempt_override_reaches_the_client() -> None:
    attempts: list[int] = []
    container = make_async_container(
        HttpClientProvider(), _AppConfig(), _Transport(), _Hook(attempts)
    )

    await (await container.get(Client)).get("/ping")
    await container.close()

    assert attempts == [0]


async def test_provider_graph_without_overrides_resolves() -> None:
    container = make_async_container(HttpClientProvider(), _AppConfig())

    assert await container.get(OptionalTransport) is None
    assert await container.get(OptionalBeforeAttempt) is None
    await container.close()


class _Backends(Provider):
    scope = Scope.APP

    @provide
    def backends(self) -> BackendsConfig:
        return BackendsConfig(
            backends={
                "crm": BackendConfig(base_url="http://crm", max_retries=1),
                "billing": BackendConfig(
                    base_url="http://billing",
                    headers={"Authorization": "Bearer tok"},
                ),
            }
        )


async def test_named_backends_resolve_with_their_own_policy() -> None:
    container = make_async_container(ApiClientsProvider(), _Backends())
    clients = await container.get(ApiClients)

    assert clients.names() == ("billing", "crm")
    assert clients.get("crm").client.config.base_url == "http://crm"
    assert clients.get("crm").client.config.max_retries == 1
    assert clients.get("billing").client.config.max_retries == 3
    with pytest.raises(UnknownBackendError):
        clients.get("nope")
    await container.close()


async def test_named_backend_carries_declared_headers() -> None:
    container = make_async_container(ApiClientsProvider(), _Backends())
    clients = await container.get(ApiClients)

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={})

    billing = clients.get("billing").client
    billing._http._transport = httpx.MockTransport(handler)
    await billing.get("/x")
    await container.close()

    assert seen["authorization"] == "Bearer tok"
    assert seen["host"] == "billing"


def test_before_attempt_type_is_exported() -> None:
    async def hook(_: int) -> None:
        return None

    checked: BeforeAttempt = hook

    assert checked is hook
