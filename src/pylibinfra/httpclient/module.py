from collections.abc import AsyncIterator

import httpx
from dishka import Provider, Scope, provide

from .breaker import BreakerConfig
from .client import Client
from .config import Config


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


def new_config() -> Config:
    return Config()


def new_breaker_config() -> BreakerConfig:
    return BreakerConfig()
