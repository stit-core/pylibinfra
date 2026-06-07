"""Адаптер идемпотентности поверх Cache: хранит пару (response, status) под
ключом idempotency:<key> с TTL. Это то, что использует httpkit для повтора
идемпотентных запросов.
"""

import base64
import json
from datetime import timedelta
from typing import Protocol, runtime_checkable

from .cache import Cache

_KEY_PREFIX = "idempotency:"


@runtime_checkable
class IdempotencyStore(Protocol):
    async def get(self, key: str) -> tuple[bytes | None, int, bool]: ...
    async def set(self, key: str, response: bytes, status: int) -> None: ...


class CacheIdempotencyStore:
    def __init__(self, cache: Cache, ttl: timedelta) -> None:
        self._cache = cache
        self._ttl = ttl

    async def get(self, key: str) -> tuple[bytes | None, int, bool]:
        data, found = await self._cache.get(_KEY_PREFIX + key)
        if not found or data is None:
            return None, 0, found
        entry = json.loads(data)
        # response хранится base64 в JSON: тело ответа — произвольные байты.
        return base64.b64decode(entry["response"]), int(entry["status"]), True

    async def set(self, key: str, response: bytes, status: int) -> None:
        entry = {"response": base64.b64encode(response).decode(), "status": status}
        data = json.dumps(entry, separators=(",", ":")).encode()
        await self._cache.set(_KEY_PREFIX + key, data, self._ttl)
