"""DedupMiddleware (outer): Telegram доставляет апдейты at-least-once (ретраит,
пока не получит 200) — при ≥2 воркерах один update_id может прийти дважды.
Атомарный SET NX EX по update_id в общем redis (из cachekit) — первый ставит
маркер и проходит, дубль дропается. Фоновые dialog-события (update_id=0) не
дедупим (у них нет уникального id).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from redis.exceptions import RedisError

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.metrics import TgInstruments

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DedupMiddleware(BaseMiddleware):
    def __init__(
        self,
        client: redis.Redis,
        ttl: float,
        instruments: TgInstruments,
        log: Logger | None = None,
    ) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)
        self._instr = instruments
        self._log = log or nop()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update_id = int(getattr(event, "update_id", 0) or 0)
        if update_id:
            bot = data.get("bot")
            bot_id = getattr(bot, "id", 0)
            key = f"tg:dedup:{bot_id}:{update_id}"
            try:
                fresh = await self._redis.set(key, b"1", nx=True, ex=self._ttl)
            except RedisError as exc:
                # fail-open: дубль при недоступном redis — меньшее зло, чем немота бота
                self._log.warn("tgbotkit: dedup redis down, skipping dedup", err=str(exc))
                return await handler(event, data)
            if not fresh:
                self._instr.deduped_total.add(1)
                return None
        return await handler(event, data)
