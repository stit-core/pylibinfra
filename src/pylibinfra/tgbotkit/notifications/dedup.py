"""DeliveryDedup — защита от повторной доставки при at-least-once JetStream
(redelivery после nak/таймаута ack). Атомарный SET NX EX по бизнес-ключу события
в общем redis; True = уже доставляли. Шаблон зовёт seen() в своём deliver_*.
"""

from __future__ import annotations

import redis.asyncio as redis


class DeliveryDedup:
    def __init__(self, client: redis.Redis, ttl: float = 86400.0) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)

    async def seen(self, key: str) -> bool:
        fresh = await self._redis.set(f"tg:notify:{key}", b"1", nx=True, ex=self._ttl)
        return not fresh
