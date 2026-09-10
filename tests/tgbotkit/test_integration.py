"""Кит против живых Redis и NATS: дедуп, троттлинг, TTL состояния и доставка из JetStream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as redis
from aiogram.fsm.storage.base import StorageKey

from pylibinfra.clockkit import Tickable
from pylibinfra.logkit import nop
from pylibinfra.natskit import StreamBus, new_conn, new_stream_bus, publish_stream_json
from pylibinfra.testkit import NatsFixture, RedisFixture, start_nats, start_redis
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.tgbotkit import (
    ChargeGuard,
    Config,
    Delivery,
    DeliveryDedup,
    NotificationRouter,
    NotificationRunner,
    build_storage,
    new_instruments,
    redis_probe,
)
from pylibinfra.tgbotkit.middleware import (
    GCRA_LUA,
    INCR_WINDOW_LUA,
    DedupMiddleware,
    OutgoingThrottleMiddleware,
    ThrottleMiddleware,
)

from .conftest import TOKEN

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_WAIT = 20.0


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def redis_server() -> AsyncIterator[RedisFixture]:
    async with start_redis() as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def nats_server() -> AsyncIterator[NatsFixture]:
    async with start_nats(jetstream=True) as fixture:
        yield fixture


@pytest_asyncio.fixture(loop_scope="module")
async def client(redis_server: RedisFixture) -> AsyncIterator[redis.Redis]:
    connection = redis.Redis.from_url(redis_server.url)
    try:
        await connection.flushall()
        yield connection
    finally:
        await connection.aclose()


async def _pass_through(event: Any, data: dict[str, Any]) -> str:
    return "handled"


async def test_dedup_is_shared_between_workers(client: redis.Redis) -> None:
    instruments = new_instruments()
    first = DedupMiddleware(client, 60, instruments)
    second = DedupMiddleware(client, 60, instruments)
    update = SimpleNamespace(update_id=4242, event_type="message")

    assert await first(_pass_through, update, {}) == "handled"
    assert await second(_pass_through, update, {}) is None


async def test_dedup_marker_expires(client: redis.Redis) -> None:
    mw = DedupMiddleware(client, 1, new_instruments())
    update = SimpleNamespace(update_id=99, event_type="message")

    await mw(_pass_through, update, {})
    assert await client.ttl("tg:dedup:0:99") == 1


async def test_throttle_window_is_atomic_and_expires(client: redis.Redis) -> None:
    mw = ThrottleMiddleware(client, 2, new_instruments())
    data = {"event_from_user": SimpleNamespace(id=7)}

    results = [
        await mw(_pass_through, SimpleNamespace(update_id=n, event_type="message"), data)
        for n in range(1, 5)
    ]
    assert results == ["handled", "handled", None, None]
    # Ключ с bot_id: без него два бота на одном Redis делили бы лимит пользователя.
    assert await client.ttl("tg:throttle:0:7") == 1


async def test_charge_guard_survives_a_second_worker(client: redis.Redis) -> None:
    token = await ChargeGuard(client).reserve("ch-int")
    assert token is not None
    assert await ChargeGuard(client).reserve("ch-int") is None

    # Чужой токен живой Redis отвергает так же, как фейк: CAS идёт в Lua-скрипте.
    assert await ChargeGuard(client).release("ch-int", "foreign") is False
    assert await ChargeGuard(client).commit("ch-int", token) is True
    assert await client.ttl("tg:charge:ch-int") == 604800


async def test_delivery_dedup_commit_extends_the_window(client: redis.Redis) -> None:
    dedup = DeliveryDedup(client, ttl=120, reserve_ttl=5)
    token = await dedup.reserve("order-int")
    assert token is not None
    assert await client.ttl("tg:notify:order-int") == 5

    assert await dedup.commit("order-int", token) is True
    assert await client.ttl("tg:notify:order-int") == 120
    # Зафиксированный ключ свой же release не отпирает — иначе повтор доставил бы дважды.
    assert await dedup.release("order-int", token) is False
    assert await dedup.reserve("order-int") is None


async def test_memory_redis_scripts_match_live_redis(client: redis.Redis) -> None:
    """Дубли скриптов bottest обязаны отвечать ровно то же, что боевой Redis."""
    fake = MemoryRedis()
    for name, lua, keys, argv in (
        ("gcra", GCRA_LUA, ["p:g"], [1000.0, 0.0, 60_000.0]),
        ("gcra-fractional", GCRA_LUA, ["p:gf"], [33.3, 0.0, 60_000.0]),
        ("gcra-over-cap", GCRA_LUA, ["p:gc"], [1000.0, 0.0, 500.0]),
        ("window", INCR_WINDOW_LUA, ["p:w"], [1]),
    ):
        live = client.register_script(lua)
        memory = fake.register_script(lua)
        for _ in range(3):
            assert int(await live(keys=keys, args=argv)) == int(
                await memory(keys=keys, args=argv)
            ), name
        assert await client.ttl(keys[0]) == await fake.ttl(keys[0]), name


async def test_outgoing_rate_limit_waits_for_a_live_slot(client: redis.Redis) -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    mw = OutgoingThrottleMiddleware(
        client,
        Config(token=TOKEN, rate_global=0.0, rate_per_chat=1.0),  # type: ignore[arg-type]
        nop(),
        clock,
    )
    method = SimpleNamespace(chat_id=77)

    async def send(bot: Any, m: Any) -> str:
        return "ok"

    assert await mw(send, None, method) == "ok"  # type: ignore[arg-type]
    second = asyncio.create_task(mw(send, None, method))  # type: ignore[arg-type]
    await clock.wait_sleepers(1)
    assert not second.done()
    clock.advance(1.0)
    assert await second == "ok"


async def test_state_ttl_reaches_redis(client: redis.Redis) -> None:
    storage = build_storage(client, state_ttl=60, data_ttl=60)
    key = StorageKey(bot_id=1, chat_id=2, user_id=3, destiny="default")

    await storage.set_data(key, {"draft": "text"})
    assert await storage.get_data(key) == {"draft": "text"}
    assert await client.ttl(storage.key_builder.build(key, "data")) == 60


async def test_redis_probe_passes_against_a_live_server(client: redis.Redis) -> None:
    await redis_probe(client).run()


@pytest_asyncio.fixture(loop_scope="module")
async def stream_bus(nats_server: NatsFixture) -> AsyncIterator[StreamBus]:
    cfg = nats_server.config(
        jetstream={"enabled": True, "stream_names": "TGBOT", "max_age": 3600.0}
    )
    conn = await new_conn(cfg)
    try:
        yield await new_stream_bus(conn, cfg)
    finally:
        await conn.close(timeout=3)


async def test_notifications_deliver_from_jetstream(stream_bus: StreamBus) -> None:
    delivered: asyncio.Queue[tuple[Any, Delivery]] = asyncio.Queue()
    delivery = Delivery(None, None)  # type: ignore[arg-type]

    async def on_paid(payload: Any, deliver: Delivery) -> None:
        await delivered.put((payload, deliver))

    router = NotificationRouter()
    router.on("TGBOT", "TGBOT.paid", "bot-paid", on_paid)
    runner = NotificationRunner(stream_bus, delivery, (router,))
    await runner.start()
    try:
        await publish_stream_json(stream_bus, "TGBOT.paid", {"order": 7}, msg_id="paid-7")
        payload, deliver = await asyncio.wait_for(delivered.get(), timeout=_WAIT)
        assert payload == {"order": 7}
        assert deliver is delivery
    finally:
        await runner.drain(_WAIT)


async def test_drained_subscription_stops_receiving(stream_bus: StreamBus) -> None:
    seen: list[Any] = []

    async def on_event(payload: Any, deliver: Delivery) -> None:
        seen.append(payload)

    router = NotificationRouter()
    router.on("TGBOT", "TGBOT.drained", "bot-drained", on_event)
    runner = NotificationRunner(stream_bus, Delivery(None, None), (router,))  # type: ignore[arg-type]
    await runner.start()
    await runner.drain(_WAIT)

    await publish_stream_json(stream_bus, "TGBOT.drained", {"n": 1}, msg_id="drained-1")
    await asyncio.sleep(0.5)
    assert seen == []
