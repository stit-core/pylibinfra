from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage

from pylibinfra.tgbotkit.storage import build_isolation, build_storage

from .conftest import FakeRedis


def test_build_storage_isolates_bot_and_destiny() -> None:
    storage = build_storage(FakeRedis())  # type: ignore[arg-type]
    assert isinstance(storage, RedisStorage)
    assert storage.key_builder.with_bot_id is True
    assert storage.key_builder.with_destiny is True


def test_build_storage_no_ttl_by_default() -> None:
    storage = build_storage(FakeRedis())  # type: ignore[arg-type]
    assert storage.state_ttl is None
    assert storage.data_ttl is None


def test_build_storage_passes_ttl_and_maps_zero_to_none() -> None:
    storage = build_storage(FakeRedis(), state_ttl=3600, data_ttl=0)  # type: ignore[arg-type]
    assert storage.state_ttl == 3600
    assert storage.data_ttl is None  # 0 → None (без TTL)


def test_build_isolation() -> None:
    iso = build_isolation(FakeRedis())  # type: ignore[arg-type]
    assert isinstance(iso, RedisEventIsolation)
