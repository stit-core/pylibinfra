"""MemoryCache: TTL по часам теста и амортизированная чистка истёкших ключей."""

from __future__ import annotations

from datetime import UTC, datetime

from pylibinfra.cachekit import MemoryCache
from pylibinfra.cachekit.memory import SWEEP_MIN
from pylibinfra.clockkit import Tickable

MINUTE = 60.0
START = datetime(2026, 1, 1, tzinfo=UTC)


def _tickable() -> tuple[MemoryCache, Tickable]:
    clock = Tickable(START)
    return MemoryCache(clock), clock


async def test_ttl_expires_by_the_clock() -> None:
    cache, clock = _tickable()
    await cache.set("k", b"v", MINUTE)

    assert await cache.set_nx("k", b"other", MINUTE) is False
    clock.advance(2 * MINUTE)

    assert await cache.get("k") is None
    assert await cache.set_nx("k", b"other", MINUTE) is True
    assert await cache.get_del("k") == b"other"
    assert await cache.get("k") is None


async def test_expired_key_leaves_no_entry_on_read() -> None:
    cache, clock = _tickable()
    await cache.set_with_tags("k", b"v", MINUTE, "t")
    clock.advance(2 * MINUTE)

    assert await cache.get("k") is None
    assert cache._data == {}


async def test_compare_and_delete_ignores_expired_key() -> None:
    cache, clock = _tickable()
    await cache.set("k", b"mine", MINUTE)
    clock.advance(2 * MINUTE)

    assert await cache.compare_and_delete("k", b"mine") is False


# Redis не снимает членство при UNLINK/GETDEL — фейк обязан быть таким же
# строгим, иначе тест консьюмера проходит, а прод сносит перезаписанный ключ.
async def test_delete_keeps_tag_membership() -> None:
    cache = MemoryCache()
    await cache.set_with_tags("a", b"1", 0, "t")

    await cache.delete("a")

    assert cache._tags["t"] == {"a"}


async def test_invalidate_by_tag_drops_the_tag_set() -> None:
    cache = MemoryCache()
    await cache.set_with_tags("a", b"1", 0, "t")

    await cache.invalidate_by_tag("t")

    assert cache._tags == {}
    assert cache._data == {}


# Записи, которые никто не читает, должны уходить сами: иначе single-replica
# потребитель течёт по памяти.
async def test_sweep_removes_expired_entries_and_tag_members() -> None:
    cache, clock = _tickable()
    written = 2000
    for i in range(written):
        await cache.set_with_tags(str(i), b"v", MINUTE, "t")

    clock.advance(2 * MINUTE)
    for i in range(written):
        await cache.set(f"fresh:{i}", b"v", 60 * MINUTE)

    assert len(cache._data) <= written + SWEEP_MIN
    assert len(cache._tags.get("t", ())) <= SWEEP_MIN


async def test_ttl_is_ignored_without_expiry() -> None:
    cache, clock = _tickable()
    await cache.set("forever", b"v", 0)

    clock.advance(365 * 24 * 3600)

    assert await cache.get("forever") == b"v"


async def test_sweep_collects_membership_of_deleted_keys() -> None:
    cache = MemoryCache()
    await cache.set_with_tags("a", b"1", 0, "t")
    await cache.delete("a")

    for i in range(SWEEP_MIN):
        await cache.set(f"fresh:{i}", b"v", 0)

    assert "t" not in cache._tags
