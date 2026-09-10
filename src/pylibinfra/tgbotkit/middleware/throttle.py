"""ThrottleMiddleware: per-user лимит входящих апдейтов в общем Redis."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from redis.exceptions import RedisError

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.metrics import Instruments

from .observability import update_type

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

INCR_WINDOW_LUA = (
    "local c = redis.call('INCR', KEYS[1])\n"
    "if c == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end\n"
    "return c"
)
"""Счётчик секундного окна: INCR и EXPIRE атомарны, поэтому окно не залипает без TTL."""


class ThrottleMiddleware(BaseMiddleware):
    """Роняет апдейты пользователя сверх `rps` в секунду, считая `tg_updates_throttled_total`.

    Дешёвая защита от спама без БД: счётчик секундного окна в общем Redis, поэтому
    лимит общий на все воркеры. Фоновые события диалога (`update_id = 0`) не троттлятся —
    их инициировал не пользователь. Недоступный Redis — fail-open, деградация видна в
    `tg_redis_errors_total{op=throttle}`.
    """

    def __init__(
        self,
        client: redis.Redis,
        rps: int,
        instruments: Instruments,
        log: Logger | None = None,
    ) -> None:
        self._rps = rps
        self._instr = instruments
        self._log = log if log is not None else nop()
        self._incr_window = client.register_script(INCR_WINDOW_LUA)

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Пропускает апдейт дальше либо гасит его как превышение лимита."""
        update_id = int(getattr(event, "update_id", 0) or 0)
        user = data.get("event_from_user")
        if not update_id or user is None:
            return await handler(event, data)
        # Ключ с bot_id: два бота на одном Redis не делят лимит одного пользователя.
        bot_id = getattr(data.get("bot"), "id", 0)
        try:
            count = int(await self._incr_window(keys=[f"tg:throttle:{bot_id}:{user.id}"], args=[1]))
        except RedisError as exc:
            self._instr.redis_errors.add(1, {"op": "throttle"})
            self._log.warn("tgbotkit: throttle unavailable, update passed through", err=str(exc))
            return await handler(event, data)
        if count > self._rps:
            self._instr.throttled.add(1, {"type": update_type(event)})
            return None
        return await handler(event, data)
