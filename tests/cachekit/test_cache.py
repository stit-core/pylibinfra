"""Unit-тесты Cache на in-memory async-фейке redis (без Docker)."""

from datetime import timedelta
from typing import TYPE_CHECKING

from pylibinfra.cachekit import RedisCache, get_json, set_json

if TYPE_CHECKING:
    from .conftest import FakeRedis


async def test_get_miss_returns_not_found(cache: RedisCache) -> None:
    val, found = await cache.get("nope")
    assert val is None
    assert found is False


async def test_set_get_roundtrip(cache: RedisCache) -> None:
    await cache.set("k", b"hello")
    val, found = await cache.get("k")
    assert found is True
    assert val == b"hello"


async def test_set_passes_ttl(cache: RedisCache, fake: "FakeRedis") -> None:
    await cache.set("k", b"v", timedelta(minutes=1))
    assert fake.last_ex == timedelta(minutes=1)


async def test_delete_removes_key(cache: RedisCache) -> None:
    await cache.set("k", b"v")
    await cache.delete("k")
    _, found = await cache.get("k")
    assert found is False


async def test_delete_noop_without_keys(cache: RedisCache) -> None:
    await cache.delete()  # не должно падать


async def test_json_roundtrip(cache: RedisCache) -> None:
    payload = {"name": "alice", "age": 30}
    await set_json(cache, "j", payload, timedelta(minutes=1))
    result: tuple[dict[str, object] | None, bool] = await get_json(cache, "j")
    got, found = result
    assert found is True
    assert got == payload


async def test_get_json_miss(cache: RedisCache) -> None:
    result: tuple[dict[str, object] | None, bool] = await get_json(cache, "missing")
    got, found = result
    assert got is None
    assert found is False


async def test_invalidate_by_tag(cache: RedisCache) -> None:
    await cache.set("a", b"1", tags=["grp"])
    await cache.set("b", b"2", tags=["grp"])
    await cache.set("c", b"3")

    await cache.invalidate_by_tag("grp")

    _, fa = await cache.get("a")
    _, fb = await cache.get("b")
    _, fc = await cache.get("c")
    assert fa is False
    assert fb is False
    assert fc is True


async def test_invalidate_unknown_tag_is_noop(cache: RedisCache) -> None:
    await cache.invalidate_by_tag("never")  # не должно падать


async def test_ping(cache: RedisCache) -> None:
    await cache.ping()
