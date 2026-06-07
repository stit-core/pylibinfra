"""In-memory async-фейк redis для unit-lane (без Docker) и общие фикстуры."""

from __future__ import annotations

from datetime import timedelta

import pytest

from pylibinfra.cachekit import RedisCache


class FakeRedis:
    """Минимальный async-фейк redis: строковые ключи + set'ы для тегов.

    Реализует только методы, которые дёргает RedisCache. TTL не моделируется —
    для roundtrip-логики он не нужен, проверяется лишь проброс аргумента (last_ex).
    """

    def __init__(self) -> None:
        self._kv: dict[str, bytes] = {}
        self._sets: dict[str, set[bytes]] = {}
        self.last_ex: object = "unset"

    async def get(self, key: str) -> bytes | None:
        return self._kv.get(key)

    async def set(self, key: str, value: bytes, ex: timedelta | None = None) -> None:
        self.last_ex = ex
        self._kv[key] = value

    async def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            if self._kv.pop(k, None) is not None:
                n += 1
            if self._sets.pop(k, None) is not None:
                n += 1
        return n

    async def sadd(self, key: str, *members: str) -> int:
        s = self._sets.setdefault(key, set())
        before = len(s)
        s.update(m.encode() if isinstance(m, str) else m for m in members)
        return len(s) - before

    async def smembers(self, key: str) -> set[bytes]:
        return set(self._sets.get(key, set()))

    async def expire(self, key: str, ttl: timedelta) -> bool:
        return key in self._kv or key in self._sets

    async def ping(self) -> bool:
        return True


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def cache(fake: FakeRedis) -> RedisCache:
    return RedisCache(fake)  # type: ignore[arg-type]
