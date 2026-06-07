"""start_redis — async context-manager: поднимает RedisContainer, отдаёт fixture
с URL и хелпером cache() → cachekit.RedisCache."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

import redis.asyncio as redis
from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]

from pylibinfra.cachekit import RedisCache

_PORT = 6379


@dataclass(frozen=True, slots=True)
class RedisFixture:
    url: str
    container: RedisContainer

    @contextlib.asynccontextmanager
    async def cache(self) -> AsyncIterator[RedisCache]:
        """Открывает redis.asyncio-клиент на URL и отдаёт RedisCache; клиент
        закрывается на выходе."""
        client: redis.Redis = redis.from_url(self.url)
        try:
            yield RedisCache(client)
        finally:
            await client.aclose()


@contextlib.asynccontextmanager
async def start_redis() -> AsyncIterator[RedisFixture]:
    container = RedisContainer("redis:7-alpine")
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(_PORT)
        yield RedisFixture(url=f"redis://{host}:{port}/0", container=container)
    finally:
        container.stop()
