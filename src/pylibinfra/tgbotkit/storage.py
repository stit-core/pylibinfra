"""FSM-хранилище и event-isolation бота поверх общего Redis из cachekit."""

from __future__ import annotations

import redis.asyncio as redis
from aiogram.fsm.storage.base import DefaultKeyBuilder
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage


class SharedRedisStorage(RedisStorage):
    """`RedisStorage` на клиенте, которым владеет не бот, а контейнер консьюмера.

    `close()` — no-op: диспетчер зовёт его на `dp.shutdown`, а базовая реализация
    гасит там весь пул соединений. Общий клиент нужен ещё дедупу, лимитам и кэшу
    сервиса до самого `container.close()`, поэтому его lifecycle ведёт cachekit.
    """

    async def close(self) -> None:
        """Ничего не делает: клиент закрывает владелец, а не диспетчер."""


def _key_builder() -> DefaultKeyBuilder:
    # aiogram_dialog адресует контексты через destiny, а дефолтный builder на таком
    # ключе бросает ValueError — падал бы каждый апдейт бота с диалогами.
    return DefaultKeyBuilder(with_bot_id=True, with_destiny=True)


def build_storage(client: redis.Redis, state_ttl: int = 0, data_ttl: int = 0) -> RedisStorage:
    """`RedisStorage` с изоляцией по боту и destiny; `0` в TTL — ключи живут вечно."""
    return SharedRedisStorage(
        client,
        key_builder=_key_builder(),
        state_ttl=state_ttl or None,
        data_ttl=data_ttl or None,
    )


def build_isolation(client: redis.Redis) -> RedisEventIsolation:
    """Event-isolation для нескольких воркеров; key_builder обязан совпадать со storage."""
    return RedisEventIsolation(client, key_builder=_key_builder())
