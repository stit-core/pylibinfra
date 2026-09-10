"""start_nats — контейнер NATS для интеграционных тестов и хелперы поверх natskit."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from testcontainers.nats import NatsContainer  # type: ignore[import-untyped]

from pylibinfra.natskit import (
    Bus,
    Config,
    Conn,
    StreamBus,
    config_without_env,
    new_bus,
    new_conn,
    new_stream_bus,
)

from ._docker import NATS_IMAGE, quiet_testcontainers, require_docker


@dataclass(frozen=True, slots=True)
class NatsFixture:
    """Живой NATS теста: клиентский `url` и handle контейнера."""

    url: str
    container: NatsContainer

    def config(self, **overrides: Any) -> Config:
        """Конфиг кита на URL контейнера; `overrides` — поля `natskit.Config` по именам.

        Собирается мимо ENV, поэтому `NATS_*` окружения на тест не влияют.
        """
        return config_without_env(url=self.url, **overrides)

    @contextlib.asynccontextmanager
    async def conn(self, **overrides: Any) -> AsyncIterator[Conn]:
        """Соединение с контейнером; на выходе — graceful `close`."""
        conn = await new_conn(self.config(**overrides))
        try:
            yield conn
        finally:
            await conn.close()

    @contextlib.asynccontextmanager
    async def bus(self, **overrides: Any) -> AsyncIterator[Bus]:
        """Core-шина поверх соединения с контейнером."""
        cfg = self.config(**overrides)
        conn = await new_conn(cfg)
        try:
            yield new_bus(conn, cfg)
        finally:
            await conn.close()

    @contextlib.asynccontextmanager
    async def stream_bus(self, **overrides: Any) -> AsyncIterator[StreamBus]:
        """JetStream-шина поверх соединения с контейнером (нужен `start_nats(jetstream=True)`)."""
        jetstream = {"enabled": True, **overrides.pop("jetstream", {})}
        cfg = self.config(jetstream=jetstream, **overrides)
        conn = await new_conn(cfg)
        try:
            yield await new_stream_bus(conn, cfg)
        finally:
            await conn.close()


@contextlib.asynccontextmanager
async def start_nats(
    *, jetstream: bool = False, image: str = NATS_IMAGE
) -> AsyncIterator[NatsFixture]:
    """Поднимает NATS (`-js` при `jetstream=True`); контейнер гасится на выходе из CM."""
    require_docker()
    quiet_testcontainers()
    container = NatsContainer(image, jetstream=jetstream)
    try:
        # start() внутри try: сорвавшееся ожидание готовности оставляет контейнер
        # запущенным — снять его обязан этот же helper.
        container.start()
        yield NatsFixture(url=container.nats_uri(), container=container)
    finally:
        with contextlib.suppress(Exception):
            container.stop()
