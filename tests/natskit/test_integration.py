from __future__ import annotations

import asyncio

import nats
import pytest
from testcontainers.nats import NatsContainer

from pylibinfra.natskit import (
    Conn,
    JsBus,
    NatsBus,
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
                    backoff=[0.1, 0.1],
                    ack_wait=0.5,
                    dlq_enabled=False,
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
