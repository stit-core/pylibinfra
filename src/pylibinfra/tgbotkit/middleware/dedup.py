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

from pylibinfra.tgbotkit.metrics import TgInstruments

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DedupMiddleware(BaseMiddleware):
    def __init__(self, client: redis.Redis, ttl: float, instruments: TgInstruments) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)
        self._instr = instruments

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update_id = int(getattr(event, "update_id", 0) or 0)
        if update_id:
            bot = data.get("bot")
            bot_id = getattr(bot, "id", 0)
            key = f"tg:dedup:{bot_id}:{update_id}"
            fresh = await self._redis.set(key, b"1", nx=True, ex=self._ttl)
            if not fresh:
                self._instr.deduped_total.add(1)
                return None
        return await handler(event, data)
