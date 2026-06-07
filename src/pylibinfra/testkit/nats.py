"""start_nats — async context-manager: поднимает NatsContainer, отдаёт fixture с
URL и хелпером bus() → natskit.NatsBus."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass

from testcontainers.nats import NatsContainer  # type: ignore[import-untyped]

from pylibinfra.natskit import Bus, NatsBus, new_conn
from pylibinfra.natskit.config import Config


@dataclass(frozen=True, slots=True)
class NatsFixture:
    url: str
    container: NatsContainer

    @contextlib.asynccontextmanager
    async def bus(self) -> AsyncIterator[Bus]:
        """Подключается к NATS на URL и отдаёт NatsBus; соединение гасится на выходе."""
        # Config — BaseSettings: url заполняется по env-alias'у NATS_URL.
        conn = await new_conn(Config.model_validate({"NATS_URL": self.url}))
        try:
            yield NatsBus(conn)
        finally:
            await conn.raw.close()


@contextlib.asynccontextmanager
async def start_nats() -> AsyncIterator[NatsFixture]:
    container = NatsContainer("nats:2-alpine")
    container.start()
    try:
        url = container.nats_uri()
        yield NatsFixture(url=url, container=container)
    finally:
        container.stop()
