"""Сниппеты README кита выполняются как есть."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import cachekit, clockkit, healthkit
from pylibinfra.testkit import RedisFixture


class AppProvider(Provider):
    scope = Scope.APP

    @provide
    def cache_config(self) -> cachekit.Config:
        return cachekit.new_config()  # REDIS_* из ENV; caller провайдит явно

    @provide
    def probes(self, cache: cachekit.Cache) -> healthkit.Probes:
        return (cachekit.probe(cache),)  # /ready через httpkit


@pytest.mark.integration
async def test_quick_start(redis_server: RedisFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", redis_server.url)
    monkeypatch.setenv("REDIS_KEY_PREFIX", "readme:")

    container = make_async_container(cachekit.CachekitProvider(), AppProvider())

    cache = await container.get(cachekit.Cache)
    await cachekit.set_json(cache, "user:42", {"name": "alice"}, 300.0)
    profile: dict[str, str] | None = await cachekit.get_json(cache, "user:42")

    await cache.set_with_tags("user:42:orders", b"[]", 3600.0, "user:42")
    await cache.invalidate_by_tag("user:42")

    probes = await container.get(healthkit.Probes)
    await probes[0].run()

    await container.close()  # PING на старте, aclose() на стопе

    assert profile == {"name": "alice"}
    assert [p.name for p in probes] == ["cachekit"]


@pytest.mark.integration
async def test_without_dishka(redis_server: RedisFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", redis_server.url)

    cache = cachekit.new_cache(cachekit.new_config())
    try:
        await cache.set("session:abc", b"{}", 900.0)
    finally:
        await cache.aclose()


async def test_memory_cache_with_a_test_clock() -> None:
    clock = clockkit.Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    cache = cachekit.MemoryCache(clock)
    await cache.set("k", b"v", 60.0)
    clock.advance(120.0)
    assert await cache.get("k") is None


async def test_idempotency_store_wiring() -> None:
    cache = cachekit.MemoryCache()
    store = cachekit.IdempotencyStore(cache)

    token, ok = await store.reserve("req-1", 60.0)
    assert ok is True
    await store.set("req-1", b'{"status":201}', 3600.0)
    await store.release("req-1", token)

    assert await store.get("req-1") == b'{"status":201}'
