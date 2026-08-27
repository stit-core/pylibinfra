from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, provide

from .api import ApiClient, ApiClients
from .breaker import BreakerConfig
from .client import Client
from .config import BackendConfig, BackendsConfig, Config


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
            built[name] = ApiClient(_build_client(name, backend))
        yield ApiClients(built)
        for api in built.values():
            await api.aclose()


def _build_client(name: str, backend: BackendConfig) -> Client:
    # Runtime-Config собирается из декларации целиком (каждое поле — явно): init-аргументы
    # приоритетнее env-источников BaseSettings, поэтому глобальные HTTPCLIENT_* не
    # протекают в именованный бэкенд. Имя breaker'а = ключ бэкенда — per-backend
    # ряды http_client_breaker_* без коллизий.
    runtime = Config(
        timeout=backend.timeout,
        max_retries=backend.max_retries,
        retry_wait=backend.retry_wait,
        base_url=backend.base_url,
        breaker=BreakerConfig(
            failure_threshold=backend.breaker.failure_threshold,
            recovery_timeout=backend.breaker.recovery_timeout,
            success_threshold=backend.breaker.success_threshold,
            name=name,
        ),
    )
    http = httpx.AsyncClient(
        timeout=backend.timeout, base_url=backend.base_url, headers=backend.headers
    )
    return Client(runtime, http)


def new_config() -> Config:
    return Config()


def new_breaker_config() -> BreakerConfig:
    return BreakerConfig()


def new_backends_config() -> BackendsConfig:
    return BackendsConfig()
