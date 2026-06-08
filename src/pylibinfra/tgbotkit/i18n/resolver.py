"""LocaleResolver — как выбрать локаль для апдейта. Дефолт FromUserLanguage
(event.from_user.language_code). FromCachePref читает явное предпочтение из
cachekit (фича смены языка в шаблоне его пишет). ChainResolver — первый
непустой. Маппинг/хранение предпочтения — продуктовое, остаётся в шаблоне.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol

from aiogram.types import TelegramObject

from pylibinfra.cachekit import Cache


class LocaleResolver(Protocol):
    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None: ...


class FromUserLanguage:
    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        user = data.get("event_from_user")
        code = getattr(user, "language_code", None)
        return code if isinstance(code, str) and code else None


class LocalePrefStore:
    """Хранилище явного выбора локали в cachekit (ключ locale:<user_id>)."""

    def __init__(self, cache: Cache, ttl: timedelta | None = None) -> None:
        self._cache = cache
        self._ttl = ttl

    async def get(self, user_id: int) -> str | None:
        data, found = await self._cache.get(f"locale:{user_id}")
        if not found or data is None:
            return None
        return data.decode()

    async def set(self, user_id: int, locale: str) -> None:
        await self._cache.set(f"locale:{user_id}", locale.encode(), self._ttl)


class FromCachePref:
    def __init__(self, store: LocalePrefStore) -> None:
        self._store = store

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        user = data.get("event_from_user")
        if user is None:
            return None
        return await self._store.get(user.id)


class ChainResolver:
    def __init__(self, *resolvers: LocaleResolver) -> None:
        self._resolvers = resolvers

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        for r in self._resolvers:
            locale = await r.resolve(event, data)
            if locale:
                return locale
        return None
