"""RedisCache на живом сервере: TTL тегов, префикс, наблюдаемость, старт и версия сервера."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import redis.asyncio as aioredis
from dishka import make_async_container
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from pylibinfra.cachekit import (
    Cache,
    CachekitProvider,
    IdempotencyStore,
    RedisCache,
    ServerVersionError,
    new_cache,
    probe,
)
from pylibinfra.cachekit.client import ObservedRedis, check_server_version
from pylibinfra.testkit import RedisFixture, start_redis

from .test_module import _Deps

pytestmark = pytest.mark.integration

MINUTE = 60.0
HOUR = 3600.0


@pytest.fixture
async def raw(redis_server: RedisFixture) -> Any:
    """Сырой клиент без инструментации: TTL и раскладку ключей смотрим мимо кита."""
    client = aioredis.from_url(redis_server.url)
    try:
        yield client
    finally:
        await client.aclose()


async def test_ttl_is_applied_and_sub_millisecond_survives(
    redis_server: RedisFixture, raw: Any
) -> None:
    async with redis_server.cache(key_prefix="ttl:") as cache:
        await cache.set("minute", b"v", MINUTE)
        assert await raw.pttl("ttl:minute") > 0

        await cache.set_with_tags("tagged", b"v", MINUTE, "t")
        assert await raw.pttl("ttl:tagged") > 0

        await cache.set("forever", b"v", 0)
        assert await raw.ttl("ttl:forever") == -1

        # Субмиллисекундный TTL не должен превратиться в «без срока жизни».
        await cache.set_with_tags("sub-ms", b"v", 0.0001, "t")
        assert await raw.ttl("ttl:sub-ms") != -1


# Набор тега — единственный индекс группы: истечь раньше живого члена он не имеет
# права ни при каком порядке записей.
async def test_tag_set_outlives_its_longest_lived_member(
    redis_server: RedisFixture, raw: Any
) -> None:
    async with redis_server.cache(key_prefix="tags:") as cache:
        await cache.set_with_tags("long-first", b"v", HOUR, "t-long-first")
        await cache.set_with_tags("short-after", b"v", MINUTE, "t-long-first")
        assert await raw.ttl("tags:tag:t-long-first") > 30 * 60

        await cache.set_with_tags("short-first", b"v", MINUTE, "t-short-first")
        await cache.set_with_tags("long-after", b"v", HOUR, "t-short-first")
        assert await raw.ttl("tags:tag:t-short-first") > 30 * 60

        await cache.set_with_tags("volatile", b"v", MINUTE, "t-persistent")
        await cache.set_with_tags("persistent", b"v", 0, "t-persistent")
        assert await raw.ttl("tags:tag:t-persistent") == -1
        await cache.set_with_tags("volatile-again", b"v", MINUTE, "t-persistent")
        assert await raw.ttl("tags:tag:t-persistent") == -1


async def test_large_tag_set_is_fully_invalidated(redis_server: RedisFixture, raw: Any) -> None:
    total = 2000
    async with redis_server.cache(key_prefix="bulk:") as cache:
        keys = [f"key:{i}" for i in range(total)]
        for key in keys:
            await cache.set_with_tags(key, b"v", HOUR, "bulk")

        await cache.invalidate_by_tag("bulk")

        assert await raw.exists(*(f"bulk:{k}" for k in keys)) == 0
        assert await raw.exists("bulk:tag:bulk") == 0


async def test_key_prefix_isolates_services(redis_server: RedisFixture, raw: Any) -> None:
    async with (
        redis_server.cache(key_prefix="svc-a:") as first,
        redis_server.cache(key_prefix="svc-b:") as second,
    ):
        await first.set("k", b"a", MINUTE)
        assert await raw.exists("svc-a:k") == 1
        assert await second.get("k") is None

        await first.set_with_tags("tagged", b"a", MINUTE, "t")
        await second.set_with_tags("tagged", b"b", MINUTE, "t")
        assert await raw.exists("svc-a:tag:t", "svc-b:tag:t") == 2

        await second.invalidate_by_tag("t")
        assert await first.get("tagged") == b"a"

        await IdempotencyStore(first).reserve("req-1", MINUTE)
        assert await raw.exists("svc-a:idempotency:req-1") == 1


# Снятие членства и удаление ключей — одна серверная операция: отдельный UNLINK,
# упавший по сети, оставил бы ключи в Redis вне какого-либо тега.
async def test_invalidate_by_tag_needs_no_client_unlink(redis_server: RedisFixture) -> None:
    cfg = redis_server.config(key_prefix="nounlink:")
    client = _RefusingUnlink.from_url(cfg.url.get_secret_value(), max_connections=cfg.pool_size)
    cache = RedisCache(client, cfg.key_prefix)
    try:
        keys = ["a", "b", "c"]
        for key in keys:
            await cache.set_with_tags(key, b"v", MINUTE, "t")

        await cache.invalidate_by_tag("t")

        for key in keys:
            assert await cache.get(key) is None
        with pytest.raises(RuntimeError):
            await cache.delete("a")
    finally:
        await cache.aclose()


async def test_commands_are_counted(
    redis_server: RedisFixture, read_metric: Callable[..., float]
) -> None:
    async with redis_server.cache(key_prefix="metrics:") as cache:
        await cache.set("k", b"v", MINUTE)
        await cache.get("k")
        await cache.get("missing")

    assert read_metric("cache_commands_total", {"cmd": "set", "result": "ok"}) == 1
    assert read_metric("cache_commands_total", {"cmd": "get", "result": "ok"}) == 1
    assert read_metric("cache_commands_total", {"cmd": "get", "result": "miss"}) == 1
    assert read_metric("cache_command_duration_seconds", {"cmd": "get"}) == 2


async def test_unknown_script_is_reloaded_once(
    redis_server: RedisFixture, raw: Any, read_metric: Callable[..., float]
) -> None:
    # Алерт по cmd="evalsha", result="error" обязан переживать рестарт Redis:
    # первый вызов на пустом кэше скриптов — один datapoint, а не отказ кита.
    await raw.script_flush()

    async with redis_server.cache(key_prefix="noscript:") as cache:
        await cache.set_with_tags("k", b"v", MINUTE, "t")
        await cache.set_with_tags("k2", b"v", MINUTE, "t")

    assert read_metric("cache_commands_total", {"cmd": "evalsha", "result": "error"}) == 1
    assert read_metric("cache_commands_total", {"cmd": "script", "result": "ok"}) == 1
    assert read_metric("cache_commands_total", {"cmd": "evalsha", "result": "ok"}) == 2


async def test_commands_are_traced(
    redis_server: RedisFixture, span_exporter: InMemorySpanExporter
) -> None:
    async with redis_server.cache(key_prefix="spans:") as cache:
        await cache.set("k", b"secret-value", MINUTE)
        await cache.get("k")

    spans = span_exporter.get_finished_spans()
    assert [s.name for s in spans] == ["redis set", "redis get"]
    for span in spans:
        assert span.attributes["db.system"] == "redis"
        assert "secret-value" not in str(dict(span.attributes))
        assert "spans:k" not in str(dict(span.attributes))


async def test_probe_pings_the_server(redis_server: RedisFixture) -> None:
    async with redis_server.cache() as cache:
        await probe(cache).run()


async def test_server_version_is_accepted(redis_server: RedisFixture) -> None:
    cache = new_cache(redis_server.config())
    try:
        await check_server_version(cache._client)
    finally:
        await cache.aclose()


async def test_command_after_close_reconnects(redis_server: RedisFixture) -> None:
    # redis-py переоткрывает пул: `aclose` освобождает соединения, но не хоронит клиент.
    cache = new_cache(redis_server.config(key_prefix="reopen:"))
    await cache.set("k", b"v", MINUTE)
    await cache.aclose()

    assert await cache.get("k") == b"v"
    await cache.aclose()


async def test_provider_starts_on_a_supported_server(redis_server: RedisFixture) -> None:
    container = make_async_container(CachekitProvider(), _Deps(redis_server.config()))

    cache = await container.get(Cache)
    await cache.ping()

    await container.close()


async def test_provider_rejects_an_old_server() -> None:
    async with start_redis(image="redis:6-alpine") as old:
        container = make_async_container(CachekitProvider(), _Deps(old.config()))
        with pytest.raises(ServerVersionError):
            await container.get(Cache)
        await container.close()

        skipping = make_async_container(
            CachekitProvider(),
            _Deps(old.config().model_copy(update={"skip_version_check": True})),
        )
        cache = await skipping.get(Cache)
        await cache.ping()
        await skipping.close()


class _RefusingUnlink(ObservedRedis):
    """Клиент, отвергающий команду `unlink` до отправки на сервер."""

    async def execute_command(self, *args: Any, **options: Any) -> Any:
        if str(args[0]).lower() == "unlink":
            raise RuntimeError("unlink is refused")
        return await super().execute_command(*args, **options)


async def test_decode_responses_client_still_returns_bytes(redis_server: RedisFixture) -> None:
    # Query-опция URL сильнее одноимённой REDIS_*: клиент отдаёт str, кит — bytes.
    cfg = redis_server.config().model_copy(
        update={"url": SecretStr(f"{redis_server.url}?decode_responses=True")}
    )
    cache = new_cache(cfg)
    try:
        await cache.set("decoded", b"v", MINUTE)
        assert await cache.get("decoded") == b"v"
        assert await cache.get_del("decoded") == b"v"
    finally:
        await cache.aclose()
