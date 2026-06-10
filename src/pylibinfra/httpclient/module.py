from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, provide

from .api import ApiClient, ApiClients
from .breaker import BREAKER_NAME, BreakerConfig
from .client import Client
from .config import BackendsConfig, Config


# Provider не provide'ит Config (asymmetry note, см. telemetry/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() / new_breaker_config() ниже.
class HttpClientProvider(Provider):
    scope = Scope.APP

    @provide
    async def client(self, cfg: Config) -> AsyncIterator[Client]:
        http = httpx.AsyncClient(
            timeout=cfg.timeout,
            base_url=cfg.base_url or "",
        )
        c = Client(cfg, http)
        yield c
        await c.aclose()

    @provide
    def api(self, client: Client) -> ApiClient:
        return ApiClient(client)


# Отдельный Provider (не метод HttpClientProvider): именованные бэкенды нужны не всем,
# а их граф требует BackendsConfig — одиночный клиент не должен этого требовать.
class ApiClientsProvider(Provider):
    scope = Scope.APP

    @provide
    async def clients(self, cfg: BackendsConfig) -> AsyncIterator[ApiClients]:
        built: dict[str, ApiClient] = {}
        for name, backend in cfg.backends.items():
            # дефолтное имя breaker'а → имя бэкенда: иначе http_client_breaker_*
            # метрики разных API схлопнутся в один ряд {"name": "httpclient"}
            if backend.breaker.name == BREAKER_NAME:
                backend = backend.model_copy(
                    update={"breaker": backend.breaker.model_copy(update={"name": name})}
                )
            http = httpx.AsyncClient(timeout=backend.timeout, base_url=backend.base_url or "")
            built[name] = ApiClient(Client(backend, http))
        yield ApiClients(built)
        for api in built.values():
            await api.aclose()


def new_config() -> Config:
    return Config()


def new_breaker_config() -> BreakerConfig:
    return BreakerConfig()


def new_backends_config() -> BackendsConfig:
    return BackendsConfig()
