"""ThrottleMiddleware (outer): per-user token-bucket в общем redis (INCR+EX 1с).
Сверх rps апдейты тихо дропаются — дешёвая защита от спама/флуда без Postgres
(боту pgkit не нужен). Off при throttle_rps=0 (middleware не навешивается).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, client: redis.Redis, rps: int) -> None:
        self._redis = client
        self._rps = rps

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is not None:
            key = f"tg:throttle:{user.id}"
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, 1)
            if count > self._rps:
                return None
        return await handler(event, data)
