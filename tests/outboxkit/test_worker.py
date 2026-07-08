from __future__ import annotations

import asyncio
import itertools
from typing import Any

import pytest

from pylibinfra.outboxkit import (
    Bus,
    Config,
    Failure,
    Message,
    OutMsg,
    PublishMode,
    Worker,
)


class FakeRepo:
    """In-memory outboxkit.Repository: insert копит, list_unpublished отдаёт
    непомеченные и не-dead, mark_published/record_failures применяют батч."""

    def __init__(self, mark_err: Exception | None = None) -> None:
        self._seq = itertools.count(1)
        self.rows: dict[int, Message] = {}
        self.published: set[int] = set()
        self.dead: set[int] = set()
        self.failures: list[Failure] = []
        self.mark_err = mark_err

    def preload(self, *msgs: Message) -> None:
        for m in msgs:
            self.rows[m.id] = m

    async def insert(self, subject: str, payload: Any) -> None:
        mid = next(self._seq)
        self.rows[mid] = Message(id=mid, subject=subject, payload=payload)

    async def list_unpublished(self, limit: int) -> list[Message]:
        out = [
            m for mid, m in self.rows.items() if mid not in self.published and mid not in self.dead
        ]
        return out[:limit]

    async def mark_published(self, ids: list[int]) -> None:
        if self.mark_err is not None:
            raise self.mark_err
        self.published.update(ids)

    async def record_failures(self, failures: list[Failure]) -> None:
        self.failures.extend(failures)
        self.dead.update(f.id for f in failures if f.dead)


class FakeTransport:
    def __init__(
        self, fail_on: dict[str, Exception] | None = None, batch_err: Exception | None = None
    ) -> None:
        self.published: list[str] = []
        self._fail = fail_on or {}
        self._batch_err = batch_err

    async def publish(self, msg: OutMsg) -> None:
        exc = self._fail.get(msg.subject)
        if exc is not None:
            raise exc
        self.published.append(msg.subject)

    async def publish_batch(self, msgs: list[OutMsg]) -> list[Exception | None]:
        if self._batch_err is not None:
            raise self._batch_err
        results: list[Exception | None] = []
        for m in msgs:
            exc = self._fail.get(m.subject)
            if exc is not None:
                results.append(exc)
                continue
            self.published.append(m.subject)
            results.append(None)
        return results


def _worker(repo: FakeRepo, tr: FakeTransport, **over: Any) -> Worker:
    cfg = Config.model_construct(
        batch_size=over.get("batch_size", 100),
        interval=over.get("interval", 0.0),
        publish_mode=over.get("mode", PublishMode.BATCH),
        max_attempts=over.get("max_attempts", 0),
    )
    return Worker(repo, tr, cfg)  # type: ignore[arg-type]


def _dead_ids(failures: list[Failure]) -> list[int]:
    return [f.id for f in failures if f.dead]


async def test_worker_publishes_and_marks() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "a", {}), Message(2, "b", {}))
    tr = FakeTransport()

    await _worker(repo, tr).run()

    assert len(tr.published) == 2
    assert repo.published == {1, 2}


async def test_batch_marks_only_acked_on_partial_failure() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "a", {}), Message(2, "boom", {}), Message(3, "c", {}))
    tr = FakeTransport(fail_on={"boom": RuntimeError("nak")})

    with pytest.raises(RuntimeError):
        await _worker(repo, tr).run()

    # id=2 (boom) не подтверждён → не помечен; 1 и 3 помечены.
    assert repo.published == {1, 3}
    assert [f.id for f in repo.failures] == [2]


async def test_ordered_stops_at_first_failure() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "a", {}), Message(2, "boom", {}), Message(3, "c", {}))
    tr = FakeTransport(fail_on={"boom": RuntimeError("nak")})

    with pytest.raises(RuntimeError):
        await _worker(repo, tr, mode=PublishMode.ORDERED).run()

    # Стоп на id=2: помечен только префикс {1}; id=3 не публиковался.
    assert repo.published == {1}
    assert tr.published == ["a"]


async def test_mark_error_surfaces() -> None:
    repo = FakeRepo(mark_err=RuntimeError("db down"))
    repo.preload(Message(1, "a", {}))
    tr = FakeTransport()

    with pytest.raises(RuntimeError, match="mark published"):
        await _worker(repo, tr).run()

    assert repo.published == set()


async def test_poison_goes_dead_after_max_attempts() -> None:
    repo = FakeRepo()
    repo.preload(
        Message(1, "boom", {}, attempts=1),  # след. попытка — 2-я
        Message(2, "young", {}, attempts=0),
    )
    tr = FakeTransport(fail_on={"boom": RuntimeError("nak"), "young": RuntimeError("nak")})

    with pytest.raises(RuntimeError):
        await _worker(repo, tr, max_attempts=2).run()

    assert len(repo.failures) == 2
    # id=1 достиг 2 попыток → dead; id=2 (1-я попытка) — нет.
    assert _dead_ids(repo.failures) == [1]


async def test_unlimited_attempts_never_dead() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "boom", {}, attempts=99))
    tr = FakeTransport(fail_on={"boom": RuntimeError("nak")})

    with pytest.raises(RuntimeError):
        await _worker(repo, tr, max_attempts=0).run()

    assert _dead_ids(repo.failures) == []


async def test_batch_error_marks_nothing() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "a", {}))
    tr = FakeTransport(batch_err=RuntimeError("jetstream disabled"))

    with pytest.raises(RuntimeError, match="publish batch"):
        await _worker(repo, tr).run()

    assert repo.published == set()


async def test_empty_batch_is_noop() -> None:
    repo = FakeRepo()
    tr = FakeTransport()
    await _worker(repo, tr).run()
    assert tr.published == []


async def test_run_loop_cancellation_stops_task() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "evt", {}))
    tr = FakeTransport()
    worker = _worker(repo, tr, interval=0.01)

    task = asyncio.create_task(worker.run_loop())
    await asyncio.sleep(0.05)
    assert 1 in repo.published

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


async def test_run_loop_survives_tick_error() -> None:
    repo = FakeRepo()
    repo.preload(Message(1, "boom", {}))
    tr = FakeTransport(fail_on={"boom": RuntimeError("nak")})
    worker = _worker(repo, tr, interval=0.01)

    task = asyncio.create_task(worker.run_loop())
    await asyncio.sleep(0.05)
    assert not task.done()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_bus_publish_writes_to_repo() -> None:
    repo = FakeRepo()
    bus = Bus(repo)
    await bus.publish("user.created", {"id": 7})

    assert len(repo.rows) == 1
    msg = next(iter(repo.rows.values()))
    assert msg.subject == "user.created"
    assert msg.payload == {"id": 7}


async def test_bus_publish_propagates_repo_error() -> None:
    class FailingRepo(FakeRepo):
        async def insert(self, subject: str, payload: Any) -> None:
            raise RuntimeError("db down")

    bus = Bus(FailingRepo())
    with pytest.raises(RuntimeError, match="db down"):
        await bus.publish("evt", {})
