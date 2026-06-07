"""Unit-тесты IdempotencyStore на in-memory async-фейке redis."""

from datetime import timedelta

import pytest

from pylibinfra.cachekit import CacheIdempotencyStore, RedisCache


@pytest.fixture
def store(cache: RedisCache) -> CacheIdempotencyStore:
    return CacheIdempotencyStore(cache, timedelta(minutes=5))


async def test_get_miss(store: CacheIdempotencyStore) -> None:
    resp, status, found = await store.get("k")
    assert resp is None
    assert status == 0
    assert found is False


async def test_set_get_roundtrip(store: CacheIdempotencyStore) -> None:
    await store.set("req-1", b'{"ok":true}', 201)
    resp, status, found = await store.get("req-1")
    assert found is True
    assert resp == b'{"ok":true}'
    assert status == 201


async def test_binary_response_survives(store: CacheIdempotencyStore) -> None:
    body = bytes(range(256))
    await store.set("bin", body, 200)
    resp, status, found = await store.get("bin")
    assert found is True
    assert resp == body
    assert status == 200
