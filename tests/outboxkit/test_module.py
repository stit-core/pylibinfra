from __future__ import annotations

import asyncio
import itertools
from typing import Any

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import Bus as NatsBus
from pylibinfra.outboxkit import Bus, Config, Message, OutboxProvider, Publisher, Store, Worker


class FakeStore:
    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self.rows: dict[int, Message] = {}
        self.published: set[int] = set()

    async def insert(self, subject: str, payload: Any) -> None:
        mid = next(self._seq)
        self.rows[mid] = Message(id=mid, subject=subject, payload=payload)

    async def list_unpublished(self, limit: int) -> list[Message]:
        return [m for mid, m in self.rows.items() if mid not in self.published][:limit]

    async def mark_published(self, id: int) -> None:
        self.published.add(id)

    async def lag_by_subject(self) -> dict[str, float]:
        return {"evt": 1.0}


class FakeNatsBus:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any]] = []

    async def publish(self, subject: str, payload: Any) -> None:
        self.sent.append((subject, payload))

    async def subscribe(self, subject: str, handler: Any) -> None:  # pragma: no cover
        raise NotImplementedError


def _deps(store: FakeStore, bus: FakeNatsBus) -> Provider:
    class _Deps(Provider):
        scope = Scope.APP

        @provide
        def store(self) -> Store:
            return store

        @provide
        def nats(self) -> NatsBus:
            return bus

        @provide
        def cfg(self) -> Config:
            return Config.model_construct(batch_size=10, interval=0.01)

        @provide
        def log(self) -> Logger:
            return nop()

    return _Deps()


async def test_provider_runs_and_stops_worker() -> None:
    store = FakeStore()
    nats = FakeNatsBus()
    await store.insert("evt", {"i": 1})

    container = make_async_container(OutboxProvider(), _deps(store, nats))

    worker = await container.get(Worker)
    assert worker is not None
    await asyncio.sleep(0.05)
    assert store.published == {1}

    # close() триггерит async-generator-выход → задача воркера отменяется.
    await container.close()
    snapshot = len(nats.sent)
    await asyncio.sleep(0.03)
    assert len(nats.sent) == snapshot


async def test_provider_wires_bus_and_publisher() -> None:
    store = FakeStore()
    nats = FakeNatsBus()
    container = make_async_container(OutboxProvider(), _deps(store, nats))

    bus = await container.get(Bus)
    publisher = await container.get(Publisher)
    assert publisher is bus

    await bus.publish("user.created", {"id": 1})
    assert len(store.rows) == 1

    await container.close()
