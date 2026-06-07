from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta

import redis.asyncio as redis
from dishka import Provider, Scope, provide
from opentelemetry.instrumentation.redis import RedisInstrumentor

from .cache import Cache, RedisCache
from .config import Config
from .idempotency import CacheIdempotencyStore, IdempotencyStore


# Provider не provide'ит Config (asymmetry note, см. flagkit/module.py) — caller
# регистрирует Config сам. Дешёвый mode: new_config() ниже. Probe тоже не
# provide'ится: его тип Callable[[], Awaitable[None]] коллизит с любым другим
# health-probe (как natskit/module.go), caller регистрирует с нужным тегом сам.
class CachekitProvider(Provider):
    scope = Scope.APP

    @provide
    async def client(self, cfg: Config) -> AsyncIterator[redis.Redis]:
        # Спаны команд redis по умолчанию: без них cache hit/miss и latency
        # неотличимы от прочего I/O. instrument() идемпотентен.
        RedisInstrumentor().instrument()
        r: redis.Redis = redis.from_url(
            cfg.url,
            max_connections=cfg.pool_size,
            socket_connect_timeout=cfg.dial_timeout,
            socket_timeout=cfg.read_timeout,
        )
        yield r
        await r.aclose()

    @provide(provides=Cache)
    def cache(self, client: redis.Redis) -> RedisCache:
        return RedisCache(client)

    @provide(provides=IdempotencyStore)
    def idempotency(self, cache: Cache, cfg: Config) -> CacheIdempotencyStore:
        return CacheIdempotencyStore(cache, timedelta(seconds=cfg.idempotency_ttl))


def probe(cache: Cache) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        await cache.ping()

    return check


def new_config() -> Config:
    return Config()
