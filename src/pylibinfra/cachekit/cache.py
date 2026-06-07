"""Redis-обёртка: порт Cache, типизированные helper'ы get_json[T]/set_json[T],
tag-based invalidation и production-реализация поверх redis.asyncio.

Тестовый in-memory фейк живёт в тестах (tests/cachekit), чтобы non-integration
lane не требовал Docker.
"""

import json
from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol, runtime_checkable

import redis.asyncio as redis

_TAG_PREFIX = "tag:"


@runtime_checkable
class Cache(Protocol):
    async def get(self, key: str) -> tuple[bytes | None, bool]: ...
    async def set(
        self,
        key: str,
        value: bytes,
        ttl: timedelta | None = None,
        *,
        tags: Sequence[str] = (),
    ) -> None: ...
    async def delete(self, *keys: str) -> None: ...
    async def invalidate_by_tag(self, tag: str) -> None: ...
    async def ping(self) -> None: ...


class RedisCache:
    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def get(self, key: str) -> tuple[bytes | None, bool]:
        val = await self._client.get(key)
        if val is None:
            return None, False
        # Клиент без decode_responses отдаёт bytes; стабы redis union'ят со str.
        return (val.encode() if isinstance(val, str) else val), True

    async def set(
        self,
        key: str,
        value: bytes,
        ttl: timedelta | None = None,
        *,
        tags: Sequence[str] = (),
    ) -> None:
        await self._client.set(key, value, ex=ttl)
        for tag in tags:
            tag_key = _TAG_PREFIX + tag
            await self._client.sadd(tag_key, key)
            # Тег живёт не дольше самой записи: иначе set растёт мусором мёртвых ключей.
            if ttl is not None:
                await self._client.expire(tag_key, ttl)

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        await self._client.delete(*keys)

    async def invalidate_by_tag(self, tag: str) -> None:
        tag_key = _TAG_PREFIX + tag
        members = await self._client.smembers(tag_key)
        keys = [m.decode() if isinstance(m, bytes) else m for m in members]
        if keys:
            await self._client.delete(*keys)
        await self._client.delete(tag_key)

    async def ping(self) -> None:
        await self._client.ping()


# Свободные generic-функции (а не методы Cache), как GetJSON[T]/SetJSON[T] в
# golibinfra: T у get_json фигурирует только в возврате, поэтому задаётся
# ожидаемым типом call-site'а (`x: tuple[T | None, bool] = await get_json(...)`),
# а не выводится из аргументов — для метода это эргономично не выражается.
async def get_json[T](cache: Cache, key: str) -> tuple[T | None, bool]:
    data, found = await cache.get(key)
    if not found or data is None:
        return None, found
    value: T = json.loads(data)
    return value, True


async def set_json[T](
    cache: Cache,
    key: str,
    value: T,
    ttl: timedelta | None = None,
    *,
    tags: Sequence[str] = (),
) -> None:
    data = json.dumps(value, separators=(",", ":")).encode()
    await cache.set(key, data, ttl, tags=tags)
