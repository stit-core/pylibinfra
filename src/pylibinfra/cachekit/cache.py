"""Порт `Cache`, реализация поверх `redis.asyncio` и типизированные `get_json`/`set_json`."""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import redis.asyncio as redis

from .errors import CacheDecodeError

TAG_PREFIX = "tag:"

# Порция SPOP: набор в миллион членов не превращается в один блокирующий ответ,
# но и round-trip'ов остаётся немного.
_INVALIDATE_BATCH = 256

# TTL набора тега держится не ниже TTL любого живого члена: новый набор получает
# TTL записи, существующий — только продление (`PEXPIRE … GT`, Redis 7.0+), а
# запись без TTL делает набор персистентным.
_SET_WITH_TAGS_LUA = """
local ttl = tonumber(ARGV[2])
if ttl > 0 then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ttl)
else
  redis.call('SET', KEYS[1], ARGV[1])
end
for i = 2, #KEYS do
  local fresh = redis.call('EXISTS', KEYS[i]) == 0
  redis.call('SADD', KEYS[i], KEYS[1])
  if ttl <= 0 then
    redis.call('PERSIST', KEYS[i])
  elseif fresh then
    redis.call('PEXPIRE', KEYS[i], ttl)
  else
    redis.call('PEXPIRE', KEYS[i], ttl, 'GT')
  end
end
return 1
"""

# Снятие членства и удаление ключей — одна серверная операция: между отдельными
# SPOP и UNLINK сетевой сбой оставил бы ключи в Redis вне какого-либо тега.
_INVALIDATE_TAG_LUA = """
local members = redis.call('SPOP', KEYS[1], ARGV[1])
if #members > 0 then
  redis.call('UNLINK', unpack(members))
end
return #members
"""

_COMPARE_AND_DELETE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('UNLINK', KEYS[1])
end
return 0
"""


@runtime_checkable
class Cache(Protocol):
    """Байтовый KV с TTL: промах — `None`, `ttl == 0` — ключ без срока жизни."""

    async def get(self, key: str) -> bytes | None:
        """Значение по ключу; промах — `None`."""
        ...

    async def set(self, key: str, value: bytes, ttl: float) -> None:
        """Пишет значение с TTL в секундах (`0` — без срока жизни)."""
        ...

    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool:
        """Пишет только отсутствующий ключ (атомарный резерв); `False` — ключ занят."""
        ...

    async def get_del(self, key: str) -> bytes | None:
        """Читает и удаляет ключ атомарно: одноразовые токены и state."""
        ...

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        """Удаляет ключ, только если его значение равно `expected`; иначе `False` без ошибки."""
        ...

    async def delete(self, *keys: str) -> None:
        """Удаляет ключи; вызов без ключей — no-op."""
        ...

    async def set_with_tags(self, key: str, value: bytes, ttl: float, *tags: str) -> None:
        """Пишет ключ и включает его в наборы тегов для `invalidate_by_tag`."""
        ...

    async def invalidate_by_tag(self, tag: str) -> None:
        """Удаляет все ключи тега и сам набор; отсутствующий тег — не ошибка."""
        ...

    async def ping(self) -> None:
        """Проверяет доступность хранилища."""
        ...

    async def aclose(self) -> None:
        """Закрывает соединения; повторный вызов — no-op."""
        ...


class RedisCache:
    """`Cache` поверх `redis.asyncio`: TTL, атомарные `set_nx`/`get_del`/`compare_and_delete`, теги.

    Все ключи кита (включая `tag:` и `idempotency:`) пишутся с префиксом
    `REDIS_KEY_PREFIX`. Cluster-режим не поддержан: инвалидация тега удаляет
    ключи, не объявленные в `KEYS` скрипта.
    """

    def __init__(self, client: redis.Redis, prefix: str = "") -> None:
        """Оборачивает готовый клиент; `prefix` — `REDIS_KEY_PREFIX` консьюмера."""
        self._client = client
        self._prefix = prefix
        self._set_with_tags = client.register_script(_SET_WITH_TAGS_LUA)
        self._invalidate_tag = client.register_script(_INVALIDATE_TAG_LUA)
        self._compare_and_delete = client.register_script(_COMPARE_AND_DELETE_LUA)

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def get(self, key: str) -> bytes | None:
        """Значение по ключу; промах — `None`."""
        return _as_bytes(await self._client.get(self._key(key)))

    async def set(self, key: str, value: bytes, ttl: float) -> None:
        """Пишет значение с TTL в секундах (`0` — без срока жизни)."""
        await self._client.set(self._key(key), value, px=milliseconds(ttl))

    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool:
        """`SET … NX`: `True`, если ключ записан; `False`, если он уже занят."""
        stored = await self._client.set(self._key(key), value, px=milliseconds(ttl), nx=True)
        return bool(stored)

    async def get_del(self, key: str) -> bytes | None:
        """`GETDEL`: значение и удаление одной атомарной командой; промах — `None`."""
        return _as_bytes(await self._client.getdel(self._key(key)))

    async def compare_and_delete(self, key: str, expected: bytes) -> bool:
        """Удаляет ключ скриптом, только если его значение равно `expected`."""
        deleted = await self._compare_and_delete(keys=[self._key(key)], args=[expected])
        return int(deleted) > 0

    async def delete(self, *keys: str) -> None:
        """`UNLINK` по ключам; вызов без ключей — no-op."""
        if not keys:
            return
        await self._client.unlink(*(self._key(k) for k in keys))

    async def set_with_tags(self, key: str, value: bytes, ttl: float, *tags: str) -> None:
        """`SET` + `SADD tag:<tag>` одним скриптом; набор живёт не меньше долгоживущего члена."""
        if not tags:
            await self.set(key, value, ttl)
            return
        keys = [self._key(key), *(self._key(TAG_PREFIX + tag) for tag in tags)]
        await self._set_with_tags(keys=keys, args=[value, milliseconds(ttl) or 0])

    async def invalidate_by_tag(self, tag: str) -> None:
        """Снимает членство и удаляет ключи порциями по 256 (`SPOP` + `UNLINK` одним скриптом)."""
        tag_key = self._key(TAG_PREFIX + tag)
        while True:
            popped = int(await self._invalidate_tag(keys=[tag_key], args=[_INVALIDATE_BATCH]))
            if popped < _INVALIDATE_BATCH:
                return

    async def ping(self) -> None:
        """`PING` на сервер."""
        await self._client.ping()

    async def aclose(self) -> None:
        """Закрывает пул клиента; повторный вызов — no-op."""
        await self._client.aclose()


def milliseconds(ttl: float) -> int | None:
    """TTL в целых миллисекундах для `PX`/`PEXPIRE`; `<= 0` — `None` (без срока жизни).

    Субмиллисекундный TTL округляется вверх: иначе он неотличим от «без срока».
    """
    if ttl <= 0:
        return None
    return max(1, int(ttl * 1000))


def _as_bytes(value: Any) -> bytes | None:
    """Ответ redis-py: `bytes`, `str` у клиента с `decode_responses` или `None`-промах."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode()
    return bytes(value)


async def get_json[T](cache: Cache, key: str) -> T | None:
    """Читает значение как JSON; промах — `None`, битое значение — `CacheDecodeError`."""
    data = await cache.get(key)
    if data is None:
        return None
    try:
        value: T = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CacheDecodeError(f"cachekit: decode json: {exc}") from exc
    return value


async def set_json[T](cache: Cache, key: str, value: T, ttl: float) -> None:
    """Сериализует значение в JSON и пишет по ключу; нерепрезентируемое значение — `TypeError`."""
    data = json.dumps(value, separators=(",", ":")).encode()
    await cache.set(key, data, ttl)
