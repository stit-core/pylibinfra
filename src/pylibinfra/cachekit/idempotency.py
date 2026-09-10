"""IdempotencyStore — резерв и запись идемпотентности httpkit поверх `Cache`."""

from __future__ import annotations

import secrets

from .cache import Cache

KEY_PREFIX = "idempotency:"

# Маркер занимает ключ на время выполнения запроса; `get` его не отдаёт, чтобы
# httpkit различал «выполняется» и «готовый ответ». За префиксом — токен
# владельца резерва: запись всегда JSON, коллизии нет.
RESERVED_PREFIX = b"\x00reserved:"

_TOKEN_BYTES = 16


class IdempotencyStore:
    """Байтовый store для `httpkit.Idempotency`: namespace `idempotency:`, резерв через `set_nx`."""

    def __init__(self, cache: Cache) -> None:
        """Оборачивает `Cache`; TTL приходит от httpkit на каждый вызов."""
        self._cache = cache

    async def reserve(self, key: str, ttl: float) -> tuple[str, bool]:
        """Занимает ключ маркером на `ttl`: `(токен, True)`; занятый ключ — `("", False)`."""
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        if not await self._cache.set_nx(KEY_PREFIX + key, marker(token), ttl):
            return "", False
        return token, True

    async def get(self, key: str) -> bytes | None:
        """Отдаёт готовую запись; ключ под резервом читается как промах."""
        record = await self._cache.get(KEY_PREFIX + key)
        if record is None or record.startswith(RESERVED_PREFIX):
            return None
        return record

    async def set(self, key: str, record: bytes, ttl: float) -> None:
        """Кладёт готовую запись на `ttl` поверх резерва."""
        await self._cache.set(KEY_PREFIX + key, record, ttl)

    async def release(self, key: str, token: str) -> None:
        """Снимает резерв, только пока ключ держит маркер с этим токеном.

        Ни готовая запись, ни резерв следующего запроса (предыдущий истёк по
        своему TTL) опоздавшим `release` не сносятся.
        """
        await self._cache.compare_and_delete(KEY_PREFIX + key, marker(token))


def marker(token: str) -> bytes:
    """Значение резерва: `\\x00reserved:<token>`."""
    return RESERVED_PREFIX + token.encode()
