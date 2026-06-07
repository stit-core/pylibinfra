"""Integration-тесты против реального Redis (testcontainers). Не гоняются в
волновом gate — помечены @pytest.mark.integration (нужен Docker).
"""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import redis.asyncio as redis
from testcontainers.redis import RedisContainer

from pylibinfra.cachekit import CacheIdempotencyStore, RedisCache, get_json, set_json

pytestmark = pytest.mark.integration


@pytest.fixture
async def cache() -> AsyncIterator[RedisCache]:
    with RedisContainer() as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(container.port)
        client: redis.Redis = redis.from_url(f"redis://{host}:{port}/0")
        yield RedisCache(client)
        await client.aclose()


async def test_set_get_delete(cache: RedisCache) -> None:
    await cache.set("k", b"hello", timedelta(minutes=1))
    val, found = await cache.get("k")
    assert found is True
    assert val == b"hello"

    await cache.delete("k")
    _, found = await cache.get("k")
    assert found is False


async def test_json_roundtrip(cache: RedisCache) -> None:
    payload = {"name": "alice", "age": 30}
    await set_json(cache, "j", payload, timedelta(minutes=1))
    result: tuple[dict[str, object] | None, bool] = await get_json(cache, "j")
    got, found = result
    assert found is True
    assert got == payload


async def test_invalidate_by_tag(cache: RedisCache) -> None:
    await cache.set("a", b"1", tags=["grp"])
    await cache.set("b", b"2", tags=["grp"])
    await cache.invalidate_by_tag("grp")
    _, fa = await cache.get("a")
    _, fb = await cache.get("b")
    assert fa is False
    assert fb is False


async def test_idempotency_roundtrip(cache: RedisCache) -> None:
    store = CacheIdempotencyStore(cache, timedelta(minutes=5))
    await store.set("req-1", b'{"ok":true}', 201)
    resp, status, found = await store.get("req-1")
    assert found is True
    assert resp == b'{"ok":true}'
    assert status == 201


async def test_ping(cache: RedisCache) -> None:
    await cache.ping()
