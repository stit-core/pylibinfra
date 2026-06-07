"""ThrottleMiddleware (outer): per-user token-bucket в общем redis (INCR+EX 1с).
Сверх rps апдейты тихо дропаются — дешёвая защита от спама/флуда без Postgres
(боту pgkit не нужен). Off при throttle_rps=0 (middleware не навешивается).
Фоновые dialog-события (update_id=0, доставка через BgManager) не троттлим —
они инициированы не пользователем. Счётчик окна атомарен: INCR и EXPIRE в одном
Lua-скрипте, так что окно не залипает без TTL при отмене таски между вызовами.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

# INCR + (на первом инкременте) EXPIRE одной атомарной операцией — TTL гарантирован.
_INCR_WINDOW = (
    "local c = redis.call('INCR', KEYS[1])\n"
    "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end\n"
    "return c"
)


class ThrottleMiddleware(BaseMiddleware):
    def __init__(self, client: redis.Redis, rps: int) -> None:
        self._redis = client
        self._rps = rps
        self._incr_window = client.register_script(_INCR_WINDOW)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update_id = int(getattr(event, "update_id", 0) or 0)
        user = data.get("event_from_user")
        if update_id and user is not None:
            key = f"tg:throttle:{user.id}"
            count = int(await self._incr_window(keys=[key], args=[1]))
            if count > self._rps:
                return None
        return await handler(event, data)
