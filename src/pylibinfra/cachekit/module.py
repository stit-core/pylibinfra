"""dishka-проводка кита: клиент, `Cache`, `IdempotencyStore`, health-проба и `new_config`."""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as redis
from dishka import Provider, Scope, provide

from pylibinfra import healthkit

from .cache import Cache, RedisCache
from .client import check_server_version, new_client
from .config import Config
from .errors import CacheError
from .idempotency import IdempotencyStore


class CachekitProvider(Provider):
    """Граф кита: `redis.Redis` → `Cache` → `IdempotencyStore`.

    `Config` регистрирует caller (asymmetry note), пробу — тоже: `probe` кладёт
    консьюмер в свой `healthkit.Probes`.
    """

    scope = Scope.APP

    @provide
    async def client(self, cfg: Config) -> AsyncIterator[redis.Redis]:
        """Клиент Redis: `PING` и проверка версии сервера на старте, `aclose()` на стопе."""
        client = new_client(cfg)
        try:
            if cfg.ping_on_start:
                await client.ping()
                if not cfg.skip_version_check:
                    await check_server_version(client)
            yield client
        finally:
            await client.aclose()

    @provide(provides=Cache)
    def cache(self, client: redis.Redis, cfg: Config) -> RedisCache:
        """`Cache` поверх клиента с префиксом ключей `REDIS_KEY_PREFIX`."""
        return RedisCache(client, cfg.key_prefix)

    @provide
    def idempotency(self, cache: Cache) -> IdempotencyStore:
        """Store идемпотентности; в ключ `httpkit.IdempotencyStore` его кладёт консьюмер."""
        return IdempotencyStore(cache)


def probe(cache: Cache) -> healthkit.Probe:
    """Проба `cachekit`: `PING`; ошибка префиксуется именем кита."""

    async def check() -> None:
        try:
            await cache.ping()
        except Exception as exc:
            raise CacheError(f"cachekit: ping: {exc}") from exc

    return healthkit.new("cachekit", check)


def new_cache(cfg: Config) -> RedisCache:
    """Собирает `RedisCache` вне DI; закрывать — `aclose()`, версия сервера не проверяется."""
    return RedisCache(new_client(cfg), cfg.key_prefix)
