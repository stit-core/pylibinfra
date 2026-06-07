"""RedisStorage + RedisEventIsolation поверх ОБЩЕГО redis из cachekit (один
инстанс шарится — 05 §2). key_builder с with_bot_id+with_destiny изолирует
мульти-бот и FSM-vs-dialog-стораджи; RedisEventIsolation обязателен при ≥2
воркерах (иначе SimpleEventIsolation in-memory не синхронизирует процессы).
"""

from __future__ import annotations

import redis.asyncio as redis
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage


def build_storage(client: redis.Redis) -> RedisStorage:
    return RedisStorage(
        client,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )


def build_isolation(client: redis.Redis) -> RedisEventIsolation:
    return RedisEventIsolation(client)
