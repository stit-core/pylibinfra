from __future__ import annotations

import asyncio
import itertools
from typing import Any

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.natskit import StreamBus, StreamMsg
from pylibinfra.outboxkit import (
    Bus,
    Config,
    Failure,
    Message,
    OutboxProvider,
    Publisher,
    Repository,
    Worker,
)


class FakeRepo:
    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self.rows: dict[int, Message] = {}
        self.published: set[int] = set()

    async def insert(self, subject: str, payload: Any) -> None:
        mid = next(self._seq)
        self.rows[mid] = Message(id=mid, subject=subject, payload=payload)

    async def list_unpublished(self, limit: int) -> list[Message]:
        return [m for mid, m in self.rows.items() if mid not in self.published][:limit]

    async def mark_published(self, ids: list[int]) -> None:
        self.published.update(ids)

    async def record_failures(self, failures: list[Failure]) -> None:  # pragma: no cover
        pass


class FakeStreamBus:
    def __init__(self) -> None:
        self.sent: list[StreamMsg] = []

    async def publish_stream(
        self, subject: str, payload: bytes, msg_id: str | None = None
    ) -> None:  # pragma: no cover
        self.sent.append(StreamMsg(subject=subject, payload=payload, msg_id=msg_id or ""))

    async def publish_stream_batch(self, msgs: list[StreamMsg]) -> list[Exception | None]:
        self.sent.extend(msgs)
        return [None] * len(msgs)

    async def subscribe_stream(self, stream: str, subject: str, durable: str, handler: Any) -> None:
        raise NotImplementedError

    async def drain(self) -> None:  # pragma: no cover
        return


def _deps(repo: FakeRepo, sb: FakeStreamBus) -> Provider:
    class _Deps(Provider):
        scope = Scope.APP

        @provide
        def repo(self) -> Repository:
            return repo

        @provide
        def stream_bus(self) -> StreamBus:
            return sb

        @provide
        def cfg(self) -> Config:
            return Config.model_construct(batch_size=10, interval=0.01)

        @provide
        def log(self) -> Logger:
            return nop()

    return _Deps()


async def test_provider_runs_and_stops_worker() -> None:
    repo = FakeRepo()
    sb = FakeStreamBus()
    await repo.insert("evt", {"i": 1})

    container = make_async_container(OutboxProvider(), _deps(repo, sb))

    worker = await container.get(Worker)
    assert worker is not None
    await asyncio.sleep(0.05)
    assert repo.published == {1}

    # close() триггерит async-generator-выход → задача воркера отменяется.
    await container.close()
    snapshot = len(sb.sent)
    await asyncio.sleep(0.03)
    assert len(sb.sent) == snapshot


async def test_provider_wires_bus_and_publisher() -> None:
    repo = FakeRepo()
    sb = FakeStreamBus()
    container = make_async_container(OutboxProvider(), _deps(repo, sb))

    bus = await container.get(Bus)
    publisher = await container.get(Publisher)
    assert publisher is bus

    await bus.publish("user.created", {"id": 1})
    assert len(repo.rows) == 1

    await container.close()
