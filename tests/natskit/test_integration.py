from __future__ import annotations

import asyncio

import nats
import pytest
from testcontainers.nats import NatsContainer

from pylibinfra.natskit import Conn, NatsBus, publish_json, subscribe_json

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
