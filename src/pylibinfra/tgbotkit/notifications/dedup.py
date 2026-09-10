"""DeliveryDedup — доставка события в Telegram ровно один раз при at-least-once подписке."""

from __future__ import annotations

import redis.asyncio as redis

from pylibinfra.idkit import new_v7
from pylibinfra.tgbotkit.reservation import Reservation

KEY_PREFIX = "tg:notify:"


class DeliveryDedup:
    """Резерв бизнес-ключа события в общем Redis: короткое окно на доставку, длинное после.

    JetStream переотправляет сообщение после `NAK` или истёкшего ack, поэтому доставку
    гейтит `SET NX EX`: `reserve` захватывает ключ на `reserve_ttl` и отдаёт токен
    владения, `commit` поднимает ключ до `ttl` после успеха, `release` снимает резерв
    после транзиентного сбоя, чтобы повтор пришёл сразу, а не по истечении окна. Обе
    операции идут по токену: истёкший резерв, перехваченный соседним воркером, чужим
    `release` не снимается.
    """

    def __init__(
        self, client: redis.Redis, ttl: float = 86400.0, reserve_ttl: float = 60.0
    ) -> None:
        self._slots = Reservation(client, KEY_PREFIX, ttl=ttl, reserve_ttl=reserve_ttl)

    async def reserve(self, key: str) -> str | None:
        """Токен владения — доставку выполняет вызывающий; `None` — это дубль."""
        token = new_v7()
        return token if await self._slots.reserve(key, token) else None

    async def commit(self, key: str, token: str) -> bool:
        """Фиксирует доставленное событие на длинное окно; `False` — резерв уже не наш."""
        return await self._slots.commit(key, token)

    async def release(self, key: str, token: str) -> bool:
        """Снимает свой резерв, разрешая переотправке повторить доставку немедленно."""
        return await self._slots.release(key, token)
