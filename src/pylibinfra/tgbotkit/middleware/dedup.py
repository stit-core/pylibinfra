"""DedupMiddleware: маркер `update_id` в общем Redis отсекает повторную доставку апдейта."""

from __future__ import annotations

import math
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


class DedupMiddleware(BaseMiddleware):
    """Отбрасывает апдейт, чей `update_id` уже брал в работу этот или соседний воркер.

    Telegram доставляет апдейты at-least-once (ретраит, пока не получит `200`), поэтому
    при нескольких воркерах один `update_id` приходит дважды. Первый атомарно ставит
    маркер `SET NX EX` и проходит, второй отбрасывается со счётчиком `tg_updates_deduped_total`.
    Фоновые события диалога (`update_id = 0`) не дедупятся: уникального id у них нет.
    Недоступный Redis — fail-open (повтор дешевле немого бота), деградация видна в
    `tg_redis_errors_total{op=dedup}`.
    """

    def __init__(
        self,
        client: redis.Redis,
        ttl: float,
        instruments: Instruments,
        log: Logger | None = None,
    ) -> None:
        self._redis = client
        # Вверх до секунды: `EX` мельче не умеет, а урезанное окно — лишний дубль.
        self._ttl = max(math.ceil(ttl), 1)
        self._instr = instruments
        self._log = log if log is not None else nop()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Пропускает апдейт дальше либо гасит его как дубль (`None` вместо результата)."""
        update_id = int(getattr(event, "update_id", 0) or 0)
        if not update_id:
            return await handler(event, data)
        bot_id = getattr(data.get("bot"), "id", 0)
        try:
            fresh = await self._redis.set(
                f"tg:dedup:{bot_id}:{update_id}", b"1", nx=True, ex=self._ttl
            )
        except RedisError as exc:
            self._instr.redis_errors.add(1, {"op": "dedup"})
            self._log.warn("tgbotkit: dedup unavailable, update passed through", err=str(exc))
            return await handler(event, data)
        if not fresh:
            self._instr.deduped.add(1, {"type": update_type(event)})
            return None
        return await handler(event, data)
