"""Общий контракт Cache: один набор проверок для RedisCache и MemoryCache."""

from __future__ import annotations

import pytest

from pylibinfra.cachekit import (
    CacheDecodeError,
    IdempotencyStore,
    MemoryCache,
    get_json,
    set_json,
)

from .conftest import CacheFactory

MINUTE = 60.0


async def test_set_get_and_miss(cache_backend: CacheFactory) -> None:
    async with cache_backend("c1:") as cache:
        await cache.set("k", b"hello", MINUTE)
        assert await cache.get("k") == b"hello"
        assert await cache.get("nonexistent") is None


async def test_set_without_ttl_keeps_key(cache_backend: CacheFactory) -> None:
    async with cache_backend("c2:") as cache:
        await cache.set("forever", b"v", 0)
        assert await cache.get("forever") == b"v"


async def test_binary_value_survives(cache_backend: CacheFactory) -> None:
    payload = bytes(range(256))
    async with cache_backend("c3:") as cache:
        await cache.set("bin", payload, MINUTE)
        assert await cache.get("bin") == payload


async def test_set_nx_and_get_del(cache_backend: CacheFactory) -> None:
    async with cache_backend("c4:") as cache:
        assert await cache.set_nx("lock", b"1", MINUTE) is True
        assert await cache.set_nx("lock", b"2", MINUTE) is False

        assert await cache.get_del("lock") == b"1"
        assert await cache.get_del("lock") is None
        assert await cache.set_nx("lock", b"3", MINUTE) is True


async def test_compare_and_delete(cache_backend: CacheFactory) -> None:
    async with cache_backend("c5:") as cache:
        await cache.set("cad", b"mine", MINUTE)

        assert await cache.compare_and_delete("cad", b"theirs") is False
        assert await cache.get("cad") == b"mine"

        assert await cache.compare_and_delete("cad", b"mine") is True
        assert await cache.get("cad") is None
        assert await cache.compare_and_delete("cad", b"mine") is False


async def test_delete_removes_keys(cache_backend: CacheFactory) -> None:
    async with cache_backend("c6:") as cache:
        await cache.set("a", b"1", MINUTE)
        await cache.set("b", b"2", MINUTE)

        await cache.delete("a", "b", "missing")
        assert await cache.get("a") is None
        assert await cache.get("b") is None


async def test_delete_without_keys_is_noop(cache_backend: CacheFactory) -> None:
    async with cache_backend("c7:") as cache:
        await cache.delete()


async def test_invalidate_by_tag(cache_backend: CacheFactory) -> None:
    async with cache_backend("c8:") as cache:
        await cache.set_with_tags("user:1", b"a", MINUTE, "users")
        await cache.set_with_tags("user:2", b"b", MINUTE, "users", "vip")
        await cache.set("other", b"c", MINUTE)

        await cache.invalidate_by_tag("users")

        assert await cache.get("user:1") is None
        assert await cache.get("user:2") is None
        assert await cache.get("other") == b"c"


# Членство привязано к имени ключа, а не к значению: Redis не снимает его ни
# при `delete`, ни при `get_del`, и перезаписанный ключ уходит вместе с тегом.
async def test_tag_membership_outlives_the_value(cache_backend: CacheFactory) -> None:
    async with cache_backend("c8a:") as cache:
        await cache.set_with_tags("deleted", b"1", MINUTE, "t")
        await cache.set_with_tags("taken", b"1", MINUTE, "t")

        await cache.delete("deleted")
        assert await cache.get_del("taken") == b"1"

        await cache.set("deleted", b"2", MINUTE)
        await cache.set("taken", b"2", MINUTE)
        await cache.invalidate_by_tag("t")

        assert await cache.get("deleted") is None
        assert await cache.get("taken") is None


async def test_invalidate_unknown_tag_is_noop(cache_backend: CacheFactory) -> None:
    async with cache_backend("c9:") as cache:
        await cache.invalidate_by_tag("never-used")


async def test_set_with_tags_without_tags_writes_key(cache_backend: CacheFactory) -> None:
    async with cache_backend("c10:") as cache:
        await cache.set_with_tags("plain", b"v", MINUTE)
        assert await cache.get("plain") == b"v"


async def test_json_helpers(cache_backend: CacheFactory) -> None:
    payload = {"name": "alice", "age": 30}
    async with cache_backend("c11:") as cache:
        await set_json(cache, "profile", payload, MINUTE)
        got: dict[str, object] | None = await get_json(cache, "profile")
        assert got == payload

        assert await get_json(cache, "missing") is None

        await cache.set("broken", b"{not json", MINUTE)
        with pytest.raises(CacheDecodeError):
            await get_json(cache, "broken")


async def test_idempotency_store(cache_backend: CacheFactory) -> None:
    async with cache_backend("c12:") as cache:
        store = IdempotencyStore(cache)

        token, ok = await store.reserve("req-1", MINUTE)
        assert ok is True
        assert token != ""
        # Резерв записью не считается: httpkit отличает «выполняется» от ответа.
        assert await store.get("req-1") is None

        _, again = await store.reserve("req-1", MINUTE)
        assert again is False

        await store.set("req-1", b'{"status":201}', MINUTE)
        assert await store.get("req-1") == b'{"status":201}'

        # Опоздавший release не сносит готовую запись.
        await store.release("req-1", token)
        assert await store.get("req-1") == b'{"status":201}'


async def test_idempotency_release_requires_own_token(cache_backend: CacheFactory) -> None:
    async with cache_backend("c13:") as cache:
        store = IdempotencyStore(cache)
        token, ok = await store.reserve("own", MINUTE)
        assert ok is True

        await store.release("own", "someone-else")
        assert (await store.reserve("own", MINUTE))[1] is False

        await store.release("own", token)
        assert (await store.reserve("own", MINUTE))[1] is True


def test_idempotency_store_fits_the_httpkit_port() -> None:
    # Контракт между китами: httpkit кладёт этот store под свой Protocol.
    from pylibinfra import httpkit

    store: httpkit.IdempotencyStore = IdempotencyStore(MemoryCache())
    assert isinstance(store, httpkit.IdempotencyStore)


async def test_ping_and_close_are_idempotent(cache_backend: CacheFactory) -> None:
    async with cache_backend("c14:") as cache:
        await cache.ping()
        await cache.aclose()
        await cache.aclose()
