from __future__ import annotations

import asyncio

import nats
import pytest
from testcontainers.nats import NatsContainer

from pylibinfra.natskit import (
    Conn,
    JsBus,
    NatsBus,
    StreamMsg,
    headers_from_context,
    new_stream_bus,
    publish_json,
    publish_stream_json,
    subscribe_json,
    subscribe_stream_json,
)
from pylibinfra.natskit.config import Config, JetStreamConfig

pytestmark = pytest.mark.integration


async def test_publish_subscribe_roundtrip() -> None:
    with NatsContainer() as container:
        url = container.nats_uri()
        nc = await nats.connect(url)
        try:
            bus = NatsBus(Conn(nc))
            got: asyncio.Future[dict[str, int]] = asyncio.get_event_loop().create_future()

            async def handler(msg: dict[str, int]) -> None:
                if not got.done():
                    got.set_result(msg)

            await subscribe_json(bus, "test.roundtrip", handler)
            await asyncio.sleep(0.1)
            await publish_json(bus, "test.roundtrip", {"value": 42})

            result = await asyncio.wait_for(got, timeout=5)
            assert result == {"value": 42}
        finally:
            await nc.drain()


async def test_poison_message_terminated_after_max_deliver() -> None:
    with NatsContainer(jetstream=True) as container:
        nc = await nats.connect(container.nats_uri())
        try:
            cfg = Config(
                jetstream=JetStreamConfig(
                    enabled=True,
                    stream_names=["poison"],
                    max_deliver=2,
                    nak_backoff=0.1,
                    ack_wait=0.5,
                )
            )
            bus = await new_stream_bus(Conn(nc), cfg)
            assert isinstance(bus, JsBus)

            calls = 0

            async def always_fails(_msg: dict[str, int]) -> None:
                nonlocal calls
                calls += 1
                raise RuntimeError("poison")

            await subscribe_stream_json(bus, "poison", "poison.evt", "poison-durable", always_fails)
            await asyncio.sleep(0.2)
            await publish_stream_json(bus, "poison.evt", {"value": 1})

            # дольше max_deliver*(ack_wait+backoff): ловим любую лишнюю редоставку
            await asyncio.sleep(3.0)

            # term() после max_deliver-й попытки остановил петлю — иначе nak крутил бы её до max_age
            assert calls == 2
        finally:
            await nc.drain()


async def test_dlq_republish_on_exhaustion() -> None:
    with NatsContainer(jetstream=True) as container:
        nc = await nats.connect(container.nats_uri())
        try:
            cfg = Config(
                jetstream=JetStreamConfig(
                    enabled=True,
                    stream_names=["ORDERS"],
                    max_deliver=3,
                    ack_wait=0.5,
                    dlq_subject="DLQ.orders",
                )
            )
            bus = await new_stream_bus(Conn(nc), cfg)
            assert isinstance(bus, JsBus)
            core = NatsBus(Conn(nc), cfg=cfg)

            dlq: asyncio.Future[dict[str, str]] = asyncio.get_event_loop().create_future()

            async def on_dlq(_payload: bytes) -> None:
                if not dlq.done():
                    dlq.set_result(headers_from_context())

            await core.subscribe("DLQ.orders", on_dlq)

            attempts = 0

            async def poison(_msg: dict[str, int]) -> None:
                nonlocal attempts
                attempts += 1
                raise RuntimeError("boom")

            await subscribe_stream_json(bus, "ORDERS", "ORDERS.created", "orders-poison", poison)
            await asyncio.sleep(0.2)
            await publish_stream_json(bus, "ORDERS.created", {"id": 1})

            headers = await asyncio.wait_for(dlq, timeout=20)
            assert headers.get("Natskit-Dlq-Origin") == "ORDERS.created"
            assert headers.get("Natskit-Dlq-Error")

            # после term — попыток ровно max_deliver
            await asyncio.sleep(2.0)
            assert attempts == 3
        finally:
            await nc.drain()


async def test_publish_stream_batch_dedup() -> None:
    with NatsContainer(jetstream=True) as container:
        nc = await nats.connect(container.nats_uri())
        try:
            cfg = Config(
                jetstream=JetStreamConfig(
                    enabled=True,
                    stream_names=["ORDERS"],
                    duplicates=60.0,
                    ack_wait=5.0,
                )
            )
            bus = await new_stream_bus(Conn(nc), cfg)
            assert isinstance(bus, JsBus)

            seen: list[int] = []
            done = asyncio.Event()

            async def collect(m: dict[str, int]) -> None:
                seen.append(m["id"])
                if len(seen) >= 2:
                    done.set()

            await subscribe_stream_json(bus, "ORDERS", "ORDERS.created", "orders-batch", collect)
            await asyncio.sleep(0.2)

            batch = [
                StreamMsg(subject="ORDERS.created", payload=b'{"id":1}', msg_id="1"),
                StreamMsg(subject="ORDERS.created", payload=b'{"id":1}', msg_id="1"),  # дубль
                StreamMsg(subject="ORDERS.created", payload=b'{"id":2}', msg_id="2"),
            ]
            results = await bus.publish_stream_batch(batch)
            # дубль по msg_id — это PubAck, не ошибка
            assert results == [None, None, None]

            await asyncio.wait_for(done.wait(), timeout=10)
            await asyncio.sleep(1.0)  # ловим лишнюю доставку дубля
            assert sorted(seen) == [1, 2]
        finally:
            await nc.drain()


async def test_drain_waits_inflight() -> None:
    with NatsContainer(jetstream=True) as container:
        nc = await nats.connect(container.nats_uri())
        try:
            cfg = Config(
                jetstream=JetStreamConfig(
                    enabled=True,
                    stream_names=["draintest"],
                    ack_wait=10.0,
                )
            )
            bus = await new_stream_bus(Conn(nc), cfg)
            assert isinstance(bus, JsBus)

            started = asyncio.Event()
            finished = False

            async def slow_handler(_msg: dict[str, int]) -> None:
                nonlocal finished
                started.set()
                await asyncio.sleep(0.3)
                finished = True

            await subscribe_stream_json(
                bus, "draintest", "draintest.evt", "drain-durable", slow_handler
            )
            await asyncio.sleep(0.2)
            await publish_stream_json(bus, "draintest.evt", {"value": 1})

            await asyncio.wait_for(started.wait(), timeout=5)
            assert finished is False

            await bus.drain()

            # drain дождался in-flight handler'а (unsubscribe → gather(_tasks)) до возврата
            assert finished is True
        finally:
            await nc.drain()
