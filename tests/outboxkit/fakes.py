"""Фейки портов outboxkit для unit-тестов воркера: Repository и Transport в памяти."""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Sequence
from datetime import UTC, datetime

from pylibinfra.outboxkit import (
    Failure,
    Message,
    MessageRef,
    OutMsg,
    PublishMode,
    ReviveFilter,
    SubjectBacklog,
)

CREATED_AT = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def message(id: int, subject: str = "order.created", attempts: int = 0) -> Message:
    """Строка outbox для теста."""
    return Message(
        id=id,
        created_at=CREATED_AT,
        subject=subject,
        payload=b'{"id":%d}' % id,
        attempts=attempts,
    )


class FakeRepo:
    """Repository в памяти: отдаёт заданные строки и запоминает mark/record."""

    def __init__(
        self,
        messages: Sequence[Message] = (),
        *,
        list_error: BaseException | None = None,
        mark_error: BaseException | None = None,
        record_error: BaseException | None = None,
        backlog: Sequence[SubjectBacklog] = (),
    ) -> None:
        self.messages = list(messages)
        self.inserted: list[tuple[str, bytes]] = []
        self.marked: list[MessageRef] = []
        self.failures: list[Failure] = []
        self.revived: list[ReviveFilter] = []
        self.modes: list[PublishMode] = []
        self.limits: list[int] = []
        self._list_error = list_error
        self._mark_error = mark_error
        self._record_error = record_error
        self._backlog = list(backlog)
        self._ids = itertools.count(100)

    async def insert(self, subject: str, payload: bytes) -> None:
        self.inserted.append((subject, payload))
        self.messages.append(
            Message(id=next(self._ids), created_at=CREATED_AT, subject=subject, payload=payload)
        )

    async def list_unpublished(self, limit: int, mode: PublishMode) -> list[Message]:
        self.limits.append(limit)
        self.modes.append(mode)
        if self._list_error is not None:
            raise self._list_error
        return self.messages[:limit]

    async def mark_published(self, refs: Sequence[MessageRef]) -> None:
        if self._mark_error is not None:
            raise self._mark_error
        self.marked.extend(refs)
        marked = {ref.id for ref in refs}
        self.messages = [m for m in self.messages if m.id not in marked]

    async def record_failures(self, failures: Sequence[Failure]) -> None:
        if self._record_error is not None:
            raise self._record_error
        self.failures.extend(failures)

    async def revive_dead(self, filter: ReviveFilter) -> int:
        self.revived.append(filter)
        return 2

    async def backlog(self) -> list[SubjectBacklog]:
        return list(self._backlog)


class FakeTransport:
    """Transport в памяти: исход публикации задаёт тест."""

    def __init__(
        self,
        *,
        results: Sequence[Exception | None] | None = None,
        batch_error: BaseException | None = None,
        fail_ids: Sequence[int] = (),
        extra_results: int = 0,
    ) -> None:
        self.published: list[OutMsg] = []
        self.batches: list[list[OutMsg]] = []
        self._results = list(results) if results is not None else None
        self._batch_error = batch_error
        self._fail_ids = set(fail_ids)
        self._extra = extra_results

    async def publish(self, msg: OutMsg) -> None:
        if self._batch_error is not None:
            raise self._batch_error
        if msg.id in self._fail_ids:
            raise RuntimeError(f"publish {msg.id} failed")
        self.published.append(msg)

    async def publish_batch(self, msgs: Sequence[OutMsg]) -> list[Exception | None]:
        self.batches.append(list(msgs))
        if self._batch_error is not None:
            raise self._batch_error
        if self._results is not None:
            return list(self._results)
        results: list[Exception | None] = []
        for msg in msgs:
            if msg.id in self._fail_ids:
                results.append(RuntimeError(f"publish {msg.id} failed"))
                continue
            self.published.append(msg)
            results.append(None)
        return results + [None] * self._extra


class BlockingTransport:
    """Transport, который держит публикацию, пока тест не отпустит её."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.published: list[OutMsg] = []

    async def publish(self, msg: OutMsg) -> None:
        await self._wait()
        self.published.append(msg)

    async def publish_batch(self, msgs: Sequence[OutMsg]) -> list[Exception | None]:
        await self._wait()
        self.published.extend(msgs)
        return [None] * len(msgs)

    async def _wait(self) -> None:
        self.entered.set()
        await self.release.wait()
