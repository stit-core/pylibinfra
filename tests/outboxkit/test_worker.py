from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from pylibinfra.outboxkit import Bus, Config, Message, Worker


class FakeStore:
    """In-memory outboxkit.Store: insert копит, list_unpublished отдаёт
    непомеченные, mark_published помечает."""

    def __init__(self) -> None:
        self._seq = itertools.count(1)
        self.rows: dict[int, Message] = {}
        self.published: set[int] = set()
        self.lag: dict[str, float] = {}

    async def insert(self, subject: str, payload: Any) -> None:
        mid = next(self._seq)
        self.rows[mid] = Message(id=mid, subject=subject, payload=payload)

    async def list_unpublished(self, limit: int) -> list[Message]:
        out = [m for mid, m in self.rows.items() if mid not in self.published]
        return out[:limit]

    async def mark_published(self, id: int) -> None:
        self.published.add(id)

    async def lag_by_subject(self) -> dict[str, float]:
        return dict(self.lag)


class FakeNatsBus:
    def __init__(self, fail_subjects: set[str] | None = None) -> None:
        self.sent: list[tuple[str, Any]] = []
        self._fail = fail_subjects or set()

    async def publish(self, subject: str, payload: Any) -> None:
        if subject in self._fail:
            raise RuntimeError("nats down")
        self.sent.append((subject, payload))

    async def subscribe(self, subject: str, handler: Any) -> None:  # pragma: no cover
        raise NotImplementedError


def _worker(store: FakeStore, bus: FakeNatsBus, **over: Any) -> Worker:
    cfg = Config.model_construct(batch_size=over.get("batch_size", 100), interval=0.0)
    return Worker(store, bus, cfg)  # type: ignore[arg-type]


async def test_tick_drains_publishes_and_marks() -> None:
    store = FakeStore()
    bus = FakeNatsBus()
    await store.insert("user.created", {"id": 1})
    await store.insert("user.deleted", {"id": 2})

    await _worker(store, bus).tick()

    assert {s for s, _ in bus.sent} == {"user.created", "user.deleted"}
    assert store.published == {1, 2}
    # Повторный тик — нечего отдавать, ничего не публикуется.
    await _worker(store, bus).tick()
    assert len(bus.sent) == 2


async def test_tick_respects_batch_size() -> None:
    store = FakeStore()
    bus = FakeNatsBus()
    for i in range(5):
        await store.insert("evt", {"i": i})

    await _worker(store, bus, batch_size=2).tick()

    assert len(bus.sent) == 2
    assert len(store.published) == 2


async def test_publish_error_does_not_mark() -> None:
    store = FakeStore()
    bus = FakeNatsBus(fail_subjects={"bad"})
    await store.insert("bad", {"x": 1})

    with pytest.raises(RuntimeError, match="nats down"):
        await _worker(store, bus).tick()

    # Событие не помечено опубликованным — повторится на следующем тике.
    assert store.published == set()
    assert bus.sent == []


async def test_run_loop_cancellation_stops_task() -> None:
    store = FakeStore()
    bus = FakeNatsBus()
    await store.insert("evt", {"i": 0})
    cfg = Config.model_construct(batch_size=10, interval=0.01)
    worker = Worker(store, bus, cfg)  # type: ignore[arg-type]

    task = asyncio.create_task(worker.run_loop())
    await asyncio.sleep(0.05)
    assert 1 in store.published

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_run_loop_survives_tick_error() -> None:
    store = FakeStore()
    bus = FakeNatsBus(fail_subjects={"boom"})
    await store.insert("boom", {"x": 1})
    cfg = Config.model_construct(batch_size=10, interval=0.01)
    worker = Worker(store, bus, cfg)  # type: ignore[arg-type]

    task = asyncio.create_task(worker.run_loop())
    await asyncio.sleep(0.05)
    # Тик падает на publish, но loop не умирает — задача всё ещё жива.
    assert not task.done()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_bus_publish_writes_to_store() -> None:
    store = FakeStore()
    bus = Bus(store)
    await bus.publish("user.created", {"id": 7})

    assert len(store.rows) == 1
    msg = next(iter(store.rows.values()))
    assert msg.subject == "user.created"
    assert msg.payload == {"id": 7}


async def test_bus_publish_propagates_store_error() -> None:
    class FailingStore(FakeStore):
        async def insert(self, subject: str, payload: Any) -> None:
            raise RuntimeError("db down")

    bus = Bus(FailingStore())
    with pytest.raises(RuntimeError, match="db down"):
        await bus.publish("evt", {})
