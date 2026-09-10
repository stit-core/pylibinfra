"""UsedCodeStore — однократность принятого TOTP-кода и две его реализации.

`MemoryUsedCodeStore` живёт в памяти процесса (одна реплика), `CacheUsedCodeStore`
кладёт отметку в общий кэш через `set_nx` (несколько реплик).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from pylibinfra.clockkit import Clock, Real


@runtime_checkable
class UsedCodeStore(Protocol):
    """Отметка «код уже предъявляли»: `mark_used` — `False`, если ключ ещё жив."""

    async def mark_used(self, key: str, ttl: float) -> bool:
        """`True`, если ключ отмечен впервые (код ещё не использовали)."""
        ...


@runtime_checkable
class UsedCodeCache(Protocol):
    """Часть контракта `cachekit.Cache`, нужная `CacheUsedCodeStore`."""

    async def set_nx(self, key: str, value: bytes, ttl: float) -> bool:
        """Кладёт значение, только если ключа нет; `False` — ключ уже занят."""
        ...


class MemoryUsedCodeStore:
    """`UsedCodeStore` в памяти процесса: годится одной реплике.

    Нескольким репликам нужен общий store (`CacheUsedCodeStore`), иначе код,
    принятый на одной реплике, пройдёт на соседней.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else Real()
        # Тело mark_used не содержит await, но store может использоваться из
        # нескольких loop'ов в разных потоках, а подметание итерирует словарь.
        self._lock = threading.Lock()
        self._until: dict[str, datetime] = {}

    async def mark_used(self, key: str, ttl: float) -> bool:
        """`False`, если ключ уже отмечен и его ttl не истёк."""
        now = self._clock.now()
        with self._lock:
            # Записи живут единицы минут, а чистить их некому: подметаем на
            # каждой записи, иначе store растёт вместе с числом логинов.
            for stale in [k for k, exp in self._until.items() if exp <= now]:
                del self._until[stale]
            exp = self._until.get(key)
            if exp is not None and exp > now:
                return False
            self._until[key] = now + timedelta(seconds=ttl)
            return True


class CacheUsedCodeStore:
    """`UsedCodeStore` поверх общего кэша: отметка ставится атомарным `set_nx`."""

    def __init__(self, cache: UsedCodeCache) -> None:
        self._cache = cache

    async def mark_used(self, key: str, ttl: float) -> bool:
        """`False`, если ключ уже есть в кэше и не истёк."""
        return await self._cache.set_nx(key, b"1", ttl)
