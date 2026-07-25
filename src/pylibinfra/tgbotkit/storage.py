"""RedisStorage + RedisEventIsolation поверх ОБЩЕГО redis из cachekit (один
инстанс шарится — 05 §2). key_builder с with_bot_id+with_destiny изолирует
мульти-бот и FSM-vs-dialog-стораджи; RedisEventIsolation обязателен при ≥2
воркерах (иначе SimpleEventIsolation in-memory не синхронизирует процессы).
"""

from __future__ import annotations

import redis.asyncio as redis
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage


def build_storage(client: redis.Redis, state_ttl: int = 0, data_ttl: int = 0) -> RedisStorage:
    # 0 → None: aiogram трактует None как «без TTL» (ключи живут вечно)
    return RedisStorage(
        client,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
        state_ttl=state_ttl or None,
        data_ttl=data_ttl or None,
    )


def build_isolation(client: redis.Redis) -> RedisEventIsolation:
    # key_builder обязан совпадать со storage: aiogram_dialog адресует контексты
    # через destiny, а дефолтный builder на таком ключе бросает ValueError —
    # падает КАЖДЫЙ апдейт бота с диалогами.
    return RedisEventIsolation(
        client,
        key_builder=DefaultKeyBuilder(with_bot_id=True, with_destiny=True),
    )
