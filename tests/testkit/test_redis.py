"""testkit.start_redis: контейнер, URL, детерминированный Config и хелпер cache()."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from pylibinfra.cachekit import RedisCache
from pylibinfra.testkit import REDIS_IMAGE, RedisFixture, start_redis
from pylibinfra.testkit import redis as testkit_redis

pytestmark = pytest.mark.integration


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


def test_fixture_exposes_url_and_container(redis_server: RedisFixture) -> None:
    assert redis_server.url.startswith("redis://")
    assert redis_server.container.get_exposed_port(6379)
    assert REDIS_IMAGE == "redis:7-alpine"


def test_config_is_independent_of_environment(
    redis_server: RedisFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REDIS_POOL_SIZE", "77")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "from-env:")

    cfg = redis_server.config(key_prefix="fixture:")

    assert cfg.url.get_secret_value() == redis_server.url
    assert cfg.key_prefix == "fixture:"
    assert cfg.pool_size == 5


async def test_failed_start_still_stops_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    # Сорвавшееся ожидание готовности не должно оставлять контейнер в демоне.
    stopped: list[int] = []

    class _Broken:
        def __init__(self, image: str) -> None:
            self.image = image

        def start(self) -> None:
            raise RuntimeError("wait strategy timed out")

        def stop(self) -> None:
            stopped.append(1)

    monkeypatch.setattr(testkit_redis, "RedisContainer", _Broken)

    with pytest.raises(RuntimeError, match="wait strategy"):
        async with start_redis():
            pass

    assert stopped == [1]


async def test_cache_helper_talks_to_the_container(redis_server: RedisFixture) -> None:
    async with redis_server.cache(key_prefix="testkit:") as cache:
        assert isinstance(cache, RedisCache)
        await cache.set("k", b"v", 60.0)
        assert await cache.get("k") == b"v"
