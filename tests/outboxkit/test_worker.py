"""Тик воркера на фейках: транзакция, режимы публикации, backoff, poison и метрики."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from pylibinfra.outboxkit import (
    BusyError,
    Config,
    OutboxError,
    PublishMode,
    Worker,
)
from pylibinfra.outboxkit import worker as worker_module
from pylibinfra.outboxkit.worker import MAX_LAST_ERROR, trunc_error
from pylibinfra.testkit import pgtest
from pylibinfra.testkit.logtest import Capture

from .fakes import BlockingTransport, FakeRepo, FakeTransport, message

_INSTANCE = "outbox"


def _cfg(**overrides: object) -> Config:
    return Config.model_validate({"tick_timeout": 5.0, "retry_backoff": 0.0, **overrides})


def _worker(repo: FakeRepo, transport: object, cfg: Config | None = None) -> Worker:
    return Worker(_INSTANCE, repo, transport, pgtest.Identity(), cfg or _cfg(), Capture())  # type: ignore[arg-type]


async def test_tick_runs_in_single_transaction_and_marks_published(
    read_metric: Callable[..., float],
) -> None:
    repo = FakeRepo([message(1), message(2)])
    transport = FakeTransport()
    tx = pgtest.Recorder()
    worker = Worker(_INSTANCE, repo, transport, tx, _cfg(batch_size=10), Capture())  # type: ignore[arg-type]

    await worker.run()

    assert tx.calls() == 1
    assert repo.limits == [10]
    assert repo.modes == [PublishMode.BATCH]
    assert [ref.id for ref in repo.marked] == [1, 2]
    assert [m.id for m in transport.published] == [1, 2]
    assert read_metric("outbox_events_processed_total", {"status": "published"}) == 2
    assert read_metric("outbox_batch_size", {"instance": _INSTANCE}) == 1
    assert read_metric("outbox_batch_duration_seconds") == 1
    assert read_metric("outbox_publish_duration_seconds") == 1


async def test_empty_tick_does_not_touch_transport(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo()
    transport = FakeTransport()

    await _worker(repo, transport).run()

    assert transport.batches == []
    assert repo.marked == []
    assert read_metric("outbox_batch_size") == 1


async def test_batch_marks_only_confirmed_rows(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo([message(1), message(2), message(3)])
    transport = FakeTransport(fail_ids=[2])
    worker = _worker(repo, transport, _cfg(max_attempts=3, retry_backoff=1.0))

    with pytest.raises(OutboxError, match=r"publish order\.created \(id=2\)"):
        await worker.run()

    assert [ref.id for ref in repo.marked] == [1, 3]
    assert [f.ref.id for f in repo.failures] == [2]
    assert repo.failures[0].dead is False
    assert repo.failures[0].retry_after == 1.0
    assert "publish 2 failed" in repo.failures[0].err
    assert read_metric("outbox_events_processed_total", {"status": "publish_error"}) == 1
    assert read_metric("outbox_events_processed_total", {"status": "published"}) == 2


async def test_ordered_stops_at_first_error(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo([message(1), message(2), message(3)])
    transport = FakeTransport(fail_ids=[2])
    worker = _worker(repo, transport, _cfg(publish_mode="ordered"))

    with pytest.raises(OutboxError, match=r"publish order\.created \(id=2\)"):
        await worker.run()

    assert repo.modes == [PublishMode.ORDERED]
    # Метится только непрерывный префикс подтверждённых, хвост не публикуется.
    assert [ref.id for ref in repo.marked] == [1]
    assert [m.id for m in transport.published] == [1]
    assert [f.ref.id for f in repo.failures] == [2]


async def test_busy_queue_head_skips_tick_silently() -> None:
    repo = FakeRepo([message(1)], list_error=BusyError())
    transport = FakeTransport()

    await _worker(repo, transport, _cfg(publish_mode="ordered")).run()

    assert transport.batches == []
    assert repo.marked == []


async def test_max_attempts_marks_row_dead(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo([message(1, attempts=2)])
    transport = FakeTransport(fail_ids=[1])
    worker = _worker(
        repo, transport, _cfg(max_attempts=3, retry_backoff=1.0, retry_max_backoff=2.0)
    )

    with pytest.raises(OutboxError):
        await worker.run()

    assert repo.failures[0].dead is True
    # 1s × 2^2 = 4s, но потолок 2s.
    assert repo.failures[0].retry_after == 2.0
    assert read_metric("outbox_events_dead_total", {"subject": "order.created"}) == 1


async def test_unlimited_attempts_never_mark_dead() -> None:
    repo = FakeRepo([message(1, attempts=99)])
    transport = FakeTransport(fail_ids=[1])

    with pytest.raises(OutboxError):
        await _worker(repo, transport, _cfg(max_attempts=0)).run()

    assert repo.failures[0].dead is False


async def test_retry_after_grows_exponentially() -> None:
    worker = _worker(FakeRepo(), FakeTransport(), _cfg(retry_backoff=1.0, retry_max_backoff=300.0))

    assert worker.retry_after(0) == 1.0
    assert worker.retry_after(1) == 2.0
    assert worker.retry_after(4) == 16.0
    assert worker.retry_after(100) == 300.0

    uncapped = _worker(FakeRepo(), FakeTransport(), _cfg(retry_backoff=2.0, retry_max_backoff=0))
    assert uncapped.retry_after(2) == 8.0

    disabled = _worker(FakeRepo(), FakeTransport(), _cfg(retry_backoff=0.0))
    assert disabled.retry_after(3) == 0.0


async def test_store_errors_surface_wrapped(read_metric: Callable[..., float]) -> None:
    list_repo = FakeRepo([message(1)], list_error=RuntimeError("connection reset"))
    with pytest.raises(OutboxError, match="worker: list"):
        await _worker(list_repo, FakeTransport()).run()

    mark_repo = FakeRepo([message(1)], mark_error=RuntimeError("deadlock"))
    with pytest.raises(OutboxError, match="worker: mark published"):
        await _worker(mark_repo, FakeTransport()).run()
    assert read_metric("outbox_events_processed_total", {"status": "mark_error"}) == 1
    # Счётчик published эмитится только после коммита — тут его нет.
    assert read_metric("outbox_events_processed_total", {"status": "published"}) == 0

    record_repo = FakeRepo([message(1)], record_error=RuntimeError("deadlock"))
    with pytest.raises(OutboxError, match="worker: record failures"):
        await _worker(record_repo, FakeTransport(fail_ids=[1])).run()


async def test_transport_batch_failure_is_wrapped() -> None:
    repo = FakeRepo([message(1)])
    transport = FakeTransport(batch_error=RuntimeError("bus down"))

    with pytest.raises(OutboxError, match="worker: publish batch"):
        await _worker(repo, transport).run()

    assert repo.marked == []


async def test_transport_result_count_mismatch_is_reported() -> None:
    repo = FakeRepo([message(1)])
    transport = FakeTransport(extra_results=1)

    with pytest.raises(OutboxError, match="transport returned 2 results for 1 messages"):
        await _worker(repo, transport).run()


async def test_tick_deadline_aborts_publish() -> None:
    repo = FakeRepo([message(1)])
    transport = BlockingTransport()
    worker = _worker(repo, transport, _cfg(tick_timeout=0.2))

    with pytest.raises(OutboxError, match="publish batch"):
        await worker.run()

    assert repo.marked == []
    transport.release.set()


async def test_cancellation_does_not_roll_back_confirmed_publications() -> None:
    repo = FakeRepo([message(1), message(2)])
    transport = BlockingTransport()
    worker = _worker(repo, transport, _cfg(tick_timeout=5.0))

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(transport.entered.wait(), 5)
    task.cancel()
    # Публикация завершается уже после отмены: фаза mark/commit её не теряет.
    transport.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert [ref.id for ref in repo.marked] == [1, 2]


async def test_ordered_stops_publishing_on_shutdown() -> None:
    repo = FakeRepo([message(1), message(2)])
    transport = BlockingTransport()
    worker = _worker(repo, transport, _cfg(publish_mode="ordered", tick_timeout=5.0))

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(transport.entered.wait(), 5)
    task.cancel()
    transport.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Первое сообщение подтверждено и помечено, второе даже не публиковалось.
    assert [m.id for m in transport.published] == [1]
    assert [ref.id for ref in repo.marked] == [1]


async def test_cancelled_run_cancels_a_tick_that_overruns_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module, "COMMIT_GRACE", 0.1)

    class _HangingMark(FakeRepo):
        def __init__(self) -> None:
            super().__init__([message(1)])
            self.entered = asyncio.Event()

        async def mark_published(self, refs: object) -> None:
            self.entered.set()
            await asyncio.Event().wait()

    repo = _HangingMark()
    worker = _worker(repo, FakeTransport(), _cfg(tick_timeout=0.1))

    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(repo.entered.wait(), 5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Запас на commit исчерпан: таска тика снята, а не оставлена жить до конца процесса.
    assert [t for t in asyncio.all_tasks() if t.get_name().startswith("outboxkit.tick.")] == []


def test_trunc_error_caps_last_error() -> None:
    assert trunc_error(RuntimeError("x" * 5000)) == "x" * MAX_LAST_ERROR
    assert trunc_error(RuntimeError("short")) == "short"
