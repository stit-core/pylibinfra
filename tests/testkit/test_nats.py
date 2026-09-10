"""testkit.start_nats: контейнер, детерминированный Config и хелперы conn()/bus()/stream_bus()."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from pylibinfra.natskit import ConnectionClosedError, subject_from_context
from pylibinfra.testkit import NATS_IMAGE, NatsFixture, start_nats

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def nats() -> AsyncIterator[NatsFixture]:
    async with start_nats(jetstream=True, image=NATS_IMAGE) as fixture:
        yield fixture


async def test_fixture_exposes_url_and_container(nats: NatsFixture) -> None:
    assert nats.url.startswith("nats://")
    assert nats.container.get_exposed_port(4222)


async def test_config_ignores_ambient_env(
    nats: NatsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NATS_URL", "nats://elsewhere:4222")
    monkeypatch.setenv("NATS_MAX_IN_FLIGHT", "1")

    cfg = nats.config(handler_timeout=1.0, jetstream={"enabled": True, "stream_names": "FIX"})

    assert cfg.url == nats.url
    assert cfg.handler_timeout == 1.0
    assert cfg.backpressure.max_in_flight == 100
    assert cfg.jetstream.stream_names == ("FIX",)


async def test_conn_helper_closes_connection(nats: NatsFixture) -> None:
    async with nats.conn() as conn:
        conn.healthy()
        captured = conn

    with pytest.raises(ConnectionClosedError):
        captured.healthy()


async def test_bus_helper_delivers_messages(nats: NatsFixture) -> None:
    got: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def handler(_: bytes) -> None:
        if not got.done():
            got.set_result(subject_from_context())

    async with nats.bus() as bus:
        await bus.subscribe("fixture.core", handler)
        await bus.publish("fixture.core", b"x")
        assert await asyncio.wait_for(got, 5) == "fixture.core"


async def test_stream_bus_helper_enables_jetstream(nats: NatsFixture) -> None:
    async with nats.stream_bus(jetstream={"stream_names": "FIXTURE", "max_age": 3600.0}) as stream:
        await stream.publish_stream("FIXTURE.evt", b"{}", msg_id="1")
        results = await stream.publish_stream_batch([])

    assert results == []
