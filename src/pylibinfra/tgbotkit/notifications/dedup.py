"""DeliveryDedup — защита от повторной доставки при at-least-once JetStream
(redelivery после nak/таймаута ack). Reserve/commit/release по бизнес-ключу
события в общем redis:

- reserve(key): атомарный SET NX EX reserve_ttl — короткое окно «доставка в
  процессе». True = ключ захвачен (звонящий доставляет), False = ключ уже занят
  (другой консьюмер в процессе ИЛИ уже закоммичено → звонящий пропускает).
- commit(key): после успешной доставки поднимает ключ до длинного окна ttl.
- release(key): после транзиентного сбоя снимает резервацию, чтобы redelivery
  (через nak в natskit.JsBus) повторил сразу, не дожидаясь истечения reserve_ttl.
"""

from __future__ import annotations

import redis.asyncio as redis


class DeliveryDedup:
    def __init__(
        self, client: redis.Redis, ttl: float = 86400.0, reserve_ttl: float = 60.0
    ) -> None:
        self._redis = client
        self._ttl = max(int(ttl), 1)
        self._reserve_ttl = max(int(reserve_ttl), 1)

    async def reserve(self, key: str) -> bool:
        fresh = await self._redis.set(f"tg:notify:{key}", b"1", nx=True, ex=self._reserve_ttl)
        return bool(fresh)

    async def commit(self, key: str) -> None:
        await self._redis.set(f"tg:notify:{key}", b"1", ex=self._ttl)

    async def release(self, key: str) -> None:
        await self._redis.delete(f"tg:notify:{key}")
