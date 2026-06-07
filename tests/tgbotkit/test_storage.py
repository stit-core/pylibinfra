from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage

from pylibinfra.tgbotkit.storage import build_isolation, build_storage

from .conftest import FakeRedis


def test_build_storage_isolates_bot_and_destiny() -> None:
    storage = build_storage(FakeRedis())  # type: ignore[arg-type]
    assert isinstance(storage, RedisStorage)
    assert storage.key_builder.with_bot_id is True
    assert storage.key_builder.with_destiny is True


def test_build_isolation() -> None:
    iso = build_isolation(FakeRedis())  # type: ignore[arg-type]
    assert isinstance(iso, RedisEventIsolation)
