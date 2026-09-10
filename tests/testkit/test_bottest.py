"""bottest: flow-тест диалога, JSON-хранилище и in-memory Redis для unit-lane бота."""

from __future__ import annotations

import asyncio
import datetime
from datetime import UTC

import pytest
from aiogram import Dispatcher
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.text import Const
from dishka import make_async_container
from redis.exceptions import RedisError

from pylibinfra.clockkit import Tickable
from pylibinfra.testkit.bottest import (
    BotClient,
    JsonMemoryStorage,
    MemoryRedis,
    MockMessageManager,
    bind_dialogs,
)
from pylibinfra.tgbotkit import build_isolation, build_storage
from pylibinfra.tgbotkit.middleware import GCRA_LUA, INCR_WINDOW_LUA
from pylibinfra.tgbotkit.reservation import CAS_COMMIT_LUA, CAS_DELETE_LUA, COMMITTED

START = datetime.datetime(2026, 1, 1, tzinfo=UTC)


class _SG(StatesGroup):
    main = State()


async def test_json_storage_accepts_serializable_data() -> None:
    storage = JsonMemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    await storage.set_data(key, {"draft": "ok"})
    assert await storage.get_data(key) == {"draft": "ok"}


async def test_json_storage_rejects_non_serializable_data() -> None:
    storage = JsonMemoryStorage()
    key = StorageKey(bot_id=1, chat_id=1, user_id=1)
    with pytest.raises(TypeError, match="not JSON-serializable"):
        await storage.set_data(key, {"when": datetime.datetime.now(tz=datetime.UTC)})


async def test_dialog_flow_with_bot_client() -> None:
    dialog = Dialog(Window(Const("Hello!"), state=_SG.main))

    async def start(message: Message, dialog_manager: DialogManager) -> None:
        await dialog_manager.start(_SG.main, mode=StartMode.RESET_STACK)

    dp = Dispatcher(storage=JsonMemoryStorage())
    dp.include_router(dialog)
    dp.message.register(start, CommandStart())
    messages = MockMessageManager()
    assert bind_dialogs(dp, message_manager=messages) is not None

    await BotClient(dp).send("/start")

    messages.assert_one_message()
    assert messages.last_message().text == "Hello!"


async def test_bind_dialogs_wires_the_container() -> None:
    container = make_async_container()
    dp = Dispatcher(storage=JsonMemoryStorage())
    try:
        assert bind_dialogs(dp, container=container) is not None
        assert any(
            type(mw).__module__.startswith("dishka.")
            for mw in dp.update.outer_middleware._middlewares
        )
    finally:
        await container.close()


async def test_memory_redis_set_nx_reserves_the_key() -> None:
    client = MemoryRedis()
    assert await client.set("k", b"1", nx=True, ex=60) is True
    assert await client.set("k", b"2", nx=True, ex=60) is None
    assert await client.get("k") == b"1"


async def test_memory_redis_delete_and_incr() -> None:
    client = MemoryRedis()
    await client.set("k", b"1")
    assert await client.delete("k", "missing") == 1
    assert await client.incr("c") == 1
    assert await client.incr("c") == 2
    assert await client.expire("c", 60) is True
    assert await client.expire("missing", 60) is False
    assert await client.ping() is True
    await client.aclose()


async def test_memory_redis_expires_keys_on_its_clock() -> None:
    clock = Tickable(START)
    client = MemoryRedis(clock=clock)
    await client.set("k", b"1", nx=True, ex=10)
    assert await client.ttl("k") == 10

    clock.advance(9.0)
    assert await client.get("k") == b"1"
    clock.advance(2.0)
    assert await client.get("k") is None
    assert await client.ttl("k") == -2
    assert await client.set("k", b"2", nx=True, ex=10) is True


async def test_memory_redis_ttl_reports_a_key_without_expiry() -> None:
    client = MemoryRedis()
    await client.set("k", b"1")
    assert await client.ttl("k") == -1


async def test_memory_redis_incr_keeps_the_window_ttl() -> None:
    clock = Tickable(START)
    client = MemoryRedis(clock=clock)
    await client.incr("w")
    await client.expire("w", 1)
    clock.advance(0.5)

    assert await client.incr("w") == 2
    assert await client.ttl("w") == 1  # INCR не продлевает окно, как и боевой Redis
    clock.advance(0.6)
    assert await client.incr("w") == 1


async def test_memory_redis_holds_is_false_for_a_missing_key() -> None:
    client = MemoryRedis()
    assert await client.holds("gone", "token") is False
    await client.set("k", "mine")
    assert await client.holds("k", "mine") is True
    assert await client.holds("k", b"mine") is True  # CAS сверяет байты, как Lua


async def test_memory_redis_failure_mode_raises_everywhere() -> None:
    client = MemoryRedis(fail=True)
    for call in (
        client.set("k", b"1"),
        client.get("k"),
        client.delete("k"),
        client.incr("c"),
        client.expire("c", 1),
        client.ttl("c"),
        client.holds("c", "token"),
        client.ping(),
    ):
        with pytest.raises(RedisError):
            await call


async def test_memory_redis_scripts_mirror_the_kit() -> None:
    client = MemoryRedis()
    window = client.register_script(INCR_WINDOW_LUA)
    assert [await window(keys=["w"], args=[1]) for _ in range(3)] == [1, 2, 3]
    assert await client.ttl("w") == 1

    gcra = client.register_script(GCRA_LUA)
    assert await gcra(keys=["g"], args=[1000.0, 0.0, 60_000.0]) == 0
    assert await gcra(keys=["g"], args=[1000.0, 0.0, 60_000.0]) == 1000


async def test_memory_redis_gcra_truncates_the_wait_like_redis() -> None:
    gcra = MemoryRedis().register_script(GCRA_LUA)
    await gcra(keys=["g"], args=[33.3, 0.0, 60_000.0])
    wait = await gcra(keys=["g"], args=[33.3, 0.0, 60_000.0])
    assert wait == 33  # Redis приводит ответ Lua к целому — дробных миллисекунд не бывает


async def test_memory_redis_gcra_does_not_reserve_beyond_the_cap() -> None:
    client = MemoryRedis()
    gcra = client.register_script(GCRA_LUA)
    await gcra(keys=["g"], args=[1000.0, 0.0, 500.0])
    reserved = client.store["g"]

    assert await gcra(keys=["g"], args=[1000.0, 0.0, 500.0]) == 1000
    assert client.store["g"] == reserved  # перелив не двигает TAT дальше в будущее


async def test_memory_redis_cas_scripts_respect_the_owner() -> None:
    client = MemoryRedis()
    commit = client.register_script(CAS_COMMIT_LUA)
    release = client.register_script(CAS_DELETE_LUA)
    await client.set("k", "mine", nx=True, ex=60)

    assert await release(keys=["k"], args=["foreign"]) == 0
    assert await commit(keys=["k"], args=["foreign", COMMITTED, 600]) == 0
    assert await commit(keys=["k"], args=["mine", COMMITTED, 600]) == 1
    assert await client.get("k") == COMMITTED
    assert await release(keys=["k"], args=["mine"]) == 0


async def test_memory_redis_scripts_honour_failure_mode() -> None:
    client = MemoryRedis()
    window = client.register_script(INCR_WINDOW_LUA)
    gcra = client.register_script(GCRA_LUA)
    commit = client.register_script(CAS_COMMIT_LUA)
    release = client.register_script(CAS_DELETE_LUA)
    client.fail = True
    for call in (
        window(keys=["w"], args=[1]),
        gcra(keys=["g"], args=[1.0, 0.0, 1.0]),
        commit(keys=["k"], args=["t", COMMITTED, 1]),
        release(keys=["k"], args=["t"]),
    ):
        with pytest.raises(RedisError):
            await call


def test_memory_redis_rejects_unknown_script() -> None:
    with pytest.raises(RedisError, match="only tgbotkit scripts"):
        MemoryRedis().register_script("return 1")


async def test_memory_redis_lock_serializes_holders() -> None:
    client = MemoryRedis()
    events: list[str] = []

    async def hold(tag: str) -> None:
        async with client.lock("chat:1"):
            events.append(f"in-{tag}")
            await asyncio.sleep(0)
            events.append(f"out-{tag}")

    await asyncio.gather(hold("a"), hold("b"))
    assert events in (
        ["in-a", "out-a", "in-b", "out-b"],
        ["in-b", "out-b", "in-a", "out-a"],
    )


async def test_memory_redis_lock_honours_failure_mode() -> None:
    client = MemoryRedis(fail=True)
    with pytest.raises(RedisError):
        async with client.lock("chat:1"):
            pass


def test_memory_redis_drives_kit_storage_and_isolation() -> None:
    client = MemoryRedis()
    assert build_storage(client).key_builder.with_destiny is True  # type: ignore[arg-type]
    assert build_isolation(client).key_builder.with_destiny is True  # type: ignore[arg-type]
