"""dishka-проводка кита: клиент, JSON-слой и именованные бэкенды."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, provide

from .api import ApiClient, ApiClients
from .client import BeforeAttempt, Client
from .config import BackendConfig, BackendsConfig, Config

OptionalTransport = httpx.AsyncBaseTransport | None
OptionalBeforeAttempt = BeforeAttempt | None


class HttpClientProvider(Provider):
    """Provide'ит `Client` и `ApiClient`; на стопе клиент закрывается.

    `Config` регистрирует caller (asymmetry note). Собственный транспорт и hook
    перед попыткой — паттерн override: свой Provider регистрируется ПОСЛЕ этого.
    """

    scope = Scope.APP

    @provide
    def default_transport(self) -> OptionalTransport:
        """Дефолт опциональной зависимости: транспорт httpx по умолчанию."""
        return None

    @provide
    def default_before_attempt(self) -> OptionalBeforeAttempt:
        """Дефолт опциональной зависимости: hook перед попыткой не задан."""
        return None

    @provide
    async def client(
        self,
        cfg: Config,
        transport: OptionalTransport,
        before_attempt: OptionalBeforeAttempt,
    ) -> AsyncIterator[Client]:
        """Клиент на весь App-scope; на выходе пул соединений закрывается."""
        c = Client(cfg, transport=transport, before_attempt=before_attempt)
        yield c
        await c.aclose()

    @provide
    def api(self, client: Client) -> ApiClient:
        """JSON-слой поверх клиента."""
        return ApiClient(client)


class ApiClientsProvider(Provider):
    """Provide'ит `ApiClients` из `BackendsConfig` — по клиенту на объявленный бэкенд.

    Отдельный Provider: именованные бэкенды нужны не всем, а их граф требует
    `BackendsConfig` — одиночный клиент не должен этого требовать.
    """

    scope = Scope.APP

    @provide
    async def clients(self, cfg: BackendsConfig) -> AsyncIterator[ApiClients]:
        """Клиенты всех объявленных бэкендов; на выходе каждый закрывается."""
        built = {name: ApiClient(_build_client(backend)) for name, backend in cfg.backends.items()}
        yield ApiClients(built)
        for api in built.values():
            await api.aclose()


def _build_client(backend: BackendConfig) -> Client:
    return Client(backend.client_config(), headers=backend.headers)
