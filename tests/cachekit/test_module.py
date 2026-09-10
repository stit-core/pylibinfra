"""Проводка кита: сборка контейнера, стартовый PING, проба и фабрика вне DI."""

from __future__ import annotations

import pytest
from dishka import Provider, Scope, make_async_container, provide
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from pylibinfra.cachekit import (
    Cache,
    CacheError,
    CachekitProvider,
    Config,
    IdempotencyStore,
    MemoryCache,
    RedisCache,
    new_cache,
    probe,
)

from .test_client import closed_addr


class _Deps(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self._cfg = cfg

    @provide
    def config(self) -> Config:
        return self._cfg


def offline_config(*, ping_on_start: bool) -> Config:
    return Config.model_validate(
        {
            "REDIS_URL": f"redis://{closed_addr()}",
            "REDIS_MAX_RETRIES": -1,
            "REDIS_DIAL_TIMEOUT": 1.0,
            "REDIS_PING_ON_START": ping_on_start,
        }
    )


async def test_container_builds_the_graph_without_ping() -> None:
    container = make_async_container(CachekitProvider(), _Deps(offline_config(ping_on_start=False)))

    cache = await container.get(Cache)
    store = await container.get(IdempotencyStore)
    client = await container.get(Redis)

    assert isinstance(cache, RedisCache)
    assert isinstance(store, IdempotencyStore)
    assert isinstance(client, Redis)
    await container.close()


async def test_ping_on_start_fails_fast_without_redis() -> None:
    container = make_async_container(CachekitProvider(), _Deps(offline_config(ping_on_start=True)))

    with pytest.raises(RedisConnectionError):
        await container.get(Cache)

    await container.close()


async def test_probe_is_named_and_prefixes_its_error() -> None:
    healthy = probe(MemoryCache())
    assert healthy.name == "cachekit"
    await healthy.run()

    broken = probe(_BrokenCache())
    with pytest.raises(CacheError, match="cachekit: ping: "):
        await broken.run()


def test_new_cache_applies_the_key_prefix() -> None:
    cfg = Config.model_validate({"REDIS_KEY_PREFIX": "svc:"})

    cache = new_cache(cfg)

    assert cache._prefix == "svc:"


class _BrokenCache(MemoryCache):
    async def ping(self) -> None:
        raise RedisConnectionError("no route to redis")
