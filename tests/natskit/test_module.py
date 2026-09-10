"""dishka-проводка natskit: один инстанс, две шины в разных компонентах, валидация имени."""

from __future__ import annotations

import asyncio

import pytest
from dishka import Provider, Scope, make_async_container, provide

from pylibinfra import logkit
from pylibinfra.natskit import (
    Bus,
    Client,
    Config,
    ConfigError,
    Conn,
    ConnectionClosedError,
    NatsProvider,
    StreamBus,
    new_config_prefixed,
    probe,
)
from pylibinfra.testkit import NatsFixture
from pylibinfra.testkit.logtest import Capture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


class _AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config, log: logkit.Logger) -> None:
        super().__init__()
        self._cfg = cfg
        self._log = log

    @provide
    def config(self) -> Config:
        return self._cfg

    @provide
    def logger(self) -> logkit.Logger:
        return self._log


class _HubConfig(Provider):
    scope = Scope.APP

    def __init__(self, cfg: Config, *, component: str) -> None:
        super().__init__(component=component)
        self._cfg = cfg

    @provide
    def config(self) -> Config:
        return self._cfg


async def test_provider_wires_client_and_closes_it(nats_server: NatsFixture) -> None:
    cfg = nats_server.config(jetstream={"enabled": True, "stream_names": "MOD", "max_age": 3600.0})
    container = make_async_container(NatsProvider(), _AppProvider(cfg, Capture()))

    client = await container.get(Client)
    bus = await container.get(Bus)
    stream = await container.get(StreamBus)
    conn = await container.get(Conn)
    got = asyncio.Event()

    await bus.subscribe("module.core", lambda _: _set(got))
    await bus.publish("module.core", b"x")
    await asyncio.wait_for(got.wait(), 5)
    await stream.publish_stream("MOD.evt", b"{}", msg_id="1")

    assert client.name == "default"
    assert probe(conn).name == "natskit"
    await probe(conn).run()

    await container.close()

    with pytest.raises(ConnectionClosedError):
        conn.healthy()


async def test_two_buses_live_in_separate_components(
    nats_server: NatsFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HUB_NATS_URL", nats_server.url)
    hub_cfg = new_config_prefixed("hub")
    container = make_async_container(
        NatsProvider(),
        _AppProvider(nats_server.config(), Capture()),
        NatsProvider("hub", component="hub"),
        _HubConfig(hub_cfg, component="hub"),
    )
    try:
        own = await container.get(Bus)
        own_conn = await container.get(Conn)
        hub = await container.get(Bus, component="hub")
        hub_conn = await container.get(Conn, component="hub")
        seen: list[str] = []
        delivered = asyncio.Event()

        async def handler(_: bytes) -> None:
            seen.append("own")
            delivered.set()

        await own.subscribe("two.buses", handler)
        # Подписка и публикация идут по разным соединениям: без flush SUB может
        # дойти до сервера позже PUB.
        await own_conn.raw.flush()
        await hub.publish("two.buses", b"x")

        await asyncio.wait_for(delivered.wait(), 5)
        assert seen == ["own"]
        assert probe(hub_conn).name == "natskit/hub"
    finally:
        await container.close()


async def test_provider_rejects_invalid_instance_name() -> None:
    with pytest.raises(ConfigError, match="instance"):
        NatsProvider("hub/v2")


async def _set(event: asyncio.Event) -> None:
    event.set()
