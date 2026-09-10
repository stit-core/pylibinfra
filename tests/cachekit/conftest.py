"""Фикстуры кита: контейнер Redis на модуль и фабрика кэша для контрактных тестов."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager

import pytest

from pylibinfra.cachekit import Cache, MemoryCache
from pylibinfra.testkit import RedisFixture, start_redis

CacheFactory = Callable[[str], AbstractAsyncContextManager[Cache]]

_ENV = (
    "REDIS_URL",
    "REDIS_KEY_PREFIX",
    "REDIS_POOL_SIZE",
    "REDIS_MAX_RETRIES",
    "REDIS_DIAL_TIMEOUT",
    "REDIS_READ_TIMEOUT",
    "REDIS_WRITE_TIMEOUT",
    "REDIS_PING_ON_START",
    "REDIS_SKIP_VERSION_CHECK",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Иначе `REDIS_*` разработчика меняет результат теста дефолтов и сниппетов README."""
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def redis_server() -> Iterator[RedisFixture]:
    """Один контейнер Redis на модуль; без Docker тест пропускается (`require_docker`)."""
    manager = start_redis()
    # Свой loop живёт до конца модуля: asyncio.run закрыл бы async-генератор
    # start_redis сразу после старта (shutdown_asyncgens) и погасил контейнер.
    runner = asyncio.Runner()
    try:
        yield runner.run(manager.__aenter__())
        runner.run(manager.__aexit__(None, None, None))
    finally:
        runner.close()


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("redis", id="redis", marks=pytest.mark.integration),
    ]
)
def cache_backend(request: pytest.FixtureRequest) -> CacheFactory:
    """Фабрика кэша под общий контракт; аргумент — префикс ключей для изоляции теста."""
    if request.param == "memory":
        return lambda _prefix: _memory_cache()
    server: RedisFixture = request.getfixturevalue("redis_server")
    return lambda prefix: server.cache(key_prefix=prefix)


@contextlib.asynccontextmanager
async def _memory_cache() -> AsyncIterator[Cache]:
    cache = MemoryCache()
    try:
        yield cache
    finally:
        await cache.aclose()
