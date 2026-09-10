"""FSM-хранилище и event-isolation: ключи изолированы по боту и destiny."""

from __future__ import annotations

from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage

from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.tgbotkit import build_isolation, build_storage


def test_storage_isolates_bot_and_destiny(redis_client: MemoryRedis) -> None:
    storage = build_storage(redis_client)  # type: ignore[arg-type]
    assert isinstance(storage, RedisStorage)
    assert storage.key_builder.with_bot_id is True
    assert storage.key_builder.with_destiny is True


def test_storage_has_no_ttl_by_default(redis_client: MemoryRedis) -> None:
    storage = build_storage(redis_client)  # type: ignore[arg-type]
    assert storage.state_ttl is None
    assert storage.data_ttl is None


def test_storage_maps_zero_ttl_to_none(redis_client: MemoryRedis) -> None:
    storage = build_storage(redis_client, state_ttl=3600, data_ttl=0)  # type: ignore[arg-type]
    assert storage.state_ttl == 3600
    assert storage.data_ttl is None


async def test_storage_close_does_not_touch_the_shared_client(redis_client: MemoryRedis) -> None:
    closed: list[int] = []
    redis_client.aclose = lambda: closed.append(1)  # type: ignore[assignment, return-value]

    await build_storage(redis_client).close()  # type: ignore[arg-type]

    # dp.shutdown зовёт storage.close(); базовая реализация гасила бы там весь пул,
    # который до container.close() нужен ещё дедупу, лимитам и кэшу сервиса.
    assert closed == []


def test_isolation_matches_storage_key_builder(redis_client: MemoryRedis) -> None:
    isolation = build_isolation(redis_client)  # type: ignore[arg-type]
    assert isinstance(isolation, RedisEventIsolation)
    assert isolation.key_builder.with_bot_id is True
    assert isolation.key_builder.with_destiny is True
