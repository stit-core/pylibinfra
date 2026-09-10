"""LocaleResolver — как выбрать локаль апдейта: язык клиента, явный выбор, цепочка."""

from __future__ import annotations

from typing import Any, Protocol

from aiogram.types import TelegramObject

from pylibinfra.cachekit import Cache

KEY_PREFIX = "locale:"


class LocaleResolver(Protocol):
    """Порт выбора локали; `None` означает «не знаю», решает следующий в цепочке."""

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        """Локаль для этого апдейта либо `None`."""


class FromUserLanguage:
    """Язык клиента Telegram (`from_user.language_code`) — дефолт кита."""

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        """Код языка из апдейта; `None`, если Telegram его не прислал."""
        code = getattr(data.get("event_from_user"), "language_code", None)
        return code if isinstance(code, str) and code else None


class LocalePrefStore:
    """Явный выбор локали пользователем в `cachekit` (`locale:<user_id>`)."""

    def __init__(self, cache: Cache, ttl: float = 0.0) -> None:
        """`ttl` — секунды; `0` (дефолт) хранит выбор без срока."""
        self._cache = cache
        self._ttl = ttl

    async def get(self, user_id: int) -> str | None:
        """Сохранённая локаль пользователя; `None` — выбора нет."""
        data = await self._cache.get(f"{KEY_PREFIX}{user_id}")
        return data.decode() if data is not None else None

    async def set(self, user_id: int, locale: str) -> None:
        """Запоминает выбор пользователя."""
        await self._cache.set(f"{KEY_PREFIX}{user_id}", locale.encode(), self._ttl)


class FromCachePref:
    """Явный выбор локали из `LocalePrefStore`; ставят его фичи смены языка в сервисе."""

    def __init__(self, store: LocalePrefStore) -> None:
        self._store = store

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        """Сохранённая локаль пользователя апдейта; `None` вне пользовательского апдейта."""
        user = data.get("event_from_user")
        if user is None:
            return None
        return await self._store.get(user.id)


class ChainResolver:
    """Первый непустой ответ из цепочки резолверов."""

    def __init__(self, *resolvers: LocaleResolver) -> None:
        self._resolvers = resolvers

    async def resolve(self, event: TelegramObject, data: dict[str, Any]) -> str | None:
        """Локаль от первого резолвера, который её знает; `None`, если не знает никто."""
        for resolver in self._resolvers:
            locale = await resolver.resolve(event, data)
            if locale:
                return locale
        return None
