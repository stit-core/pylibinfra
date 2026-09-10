"""Инстанс outbox'а: job'ы для timerkit, оживление dead-строк и gauge'и backlog'а."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricsData

from pylibinfra.outboxkit import (
    Config,
    NotSupportedError,
    Outbox,
    ReviveFilter,
    SubjectBacklog,
    new_outbox,
    new_stream_transport,
    register_backlog_gauges,
)
from pylibinfra.outboxkit import metrics as metrics_module
from pylibinfra.outboxkit.worker import COMMIT_GRACE
from pylibinfra.testkit import natstest, pgtest
from pylibinfra.timerkit import Job

from .fakes import FakeRepo, FakeTransport, message


class _CountingBacklog:
    """`BacklogReporter`, считающий походы за снимком."""

    def __init__(self) -> None:
        self.calls = 0
        self.called = asyncio.Event()

    async def backlog(self) -> list[SubjectBacklog]:
        self.calls += 1
        self.called.set()
        return [SubjectBacklog(subject="order.created", oldest=7.0, pending=1)]


def _gauge_values(data: MetricsData | None, name: str) -> list[float]:
    if data is None:
        return []
    return [
        point.value
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _outbox(repo: FakeRepo, **overrides: object) -> Outbox:
    cfg = Config.model_validate({"interval": 2.0, "tick_timeout": 7.0, **overrides})
    return new_outbox("orders", repo, FakeTransport(), pgtest.Identity(), cfg)  # type: ignore[arg-type]


async def test_job_carries_instance_name_interval_and_budget() -> None:
    outbox = _outbox(FakeRepo())

    job = outbox.job()

    assert isinstance(job, Job)
    assert job.name == "outboxkit.publisher.orders"
    assert job.interval == 2.0
    # Бюджет запуска — дедлайн тика плюс запас на фазу mark/commit.
    assert job.timeout == 7.0 + COMMIT_GRACE


async def test_jobs_include_store_maintenance() -> None:
    class _Maintaining(FakeRepo):
        def maintenance_jobs(self) -> list[Job]:
            return [Job(name="outboxkit.partition-keeper.orders", interval=1.0, run=_noop)]

    outbox = _outbox(_Maintaining())

    assert [job.name for job in outbox.jobs()] == [
        "outboxkit.publisher.orders",
        "outboxkit.partition-keeper.orders",
    ]
    # Хранилище без job'ов обслуживания даёт только drain.
    assert [job.name for job in _outbox(FakeRepo()).jobs()] == ["outboxkit.publisher.orders"]


async def test_revive_dead_counts_revived_rows(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo()
    outbox = _outbox(repo)

    revived = await outbox.revive_dead(ReviveFilter(subject="order.created"))

    assert revived == 2
    assert repo.revived[0].subject == "order.created"
    assert read_metric("outbox_revived_total", {"instance": "orders"}) == 2
    # Пустой фильтр берёт все dead-строки.
    await outbox.revive_dead()
    assert repo.revived[1] == ReviveFilter()


async def test_revive_dead_needs_store_support() -> None:
    class _NoRevive:
        async def insert(self, subject: str, payload: bytes) -> None: ...
        async def list_unpublished(self, limit: int, mode: object) -> list[object]: ...
        async def mark_published(self, refs: object) -> None: ...
        async def record_failures(self, failures: object) -> None: ...

    outbox = new_outbox(
        "orders",
        _NoRevive(),  # type: ignore[arg-type]
        FakeTransport(),  # type: ignore[arg-type]
        pgtest.Identity(),
        Config(),
    )

    with pytest.raises(NotSupportedError, match="revive_dead"):
        await outbox.revive_dead()


async def test_backlog_gauges_observe_store_snapshot(read_metric: Callable[..., float]) -> None:
    repo = FakeRepo(backlog=[SubjectBacklog(subject="order.created", oldest=12.5, pending=3)])
    outbox = _outbox(repo)

    registration = outbox.register_backlog_gauges()
    assert registration is not None

    # Сбор идёт из отдельного потока — как у PeriodicExportingMetricReader в проде.
    lag = await asyncio.to_thread(read_metric, "outbox_lag_seconds", {"subject": "order.created"})
    pending = await asyncio.to_thread(
        read_metric, "outbox_pending_count", {"subject": "order.created"}
    )
    assert lag == 12.5
    assert pending == 3

    registration.unregister()
    assert await asyncio.to_thread(read_metric, "outbox_lag_seconds") == 0


async def test_backlog_snapshot_is_shared_by_both_gauges(
    metric_reader: InMemoryMetricReader,
) -> None:
    repo = _CountingBacklog()
    register_backlog_gauges("orders", repo)

    data = await asyncio.to_thread(metric_reader.get_metrics_data)

    # Один поход в хранилище на цикл сбора, а не по одному на каждый gauge.
    assert repo.calls == 1
    assert _gauge_values(data, "outbox_lag_seconds") == [7.0]
    assert _gauge_values(data, "outbox_pending_count") == [1]


async def test_backlog_gauges_never_block_their_own_loop(
    metric_reader: InMemoryMetricReader,
) -> None:
    repo = _CountingBacklog()
    register_backlog_gauges("orders", repo)

    loop = asyncio.get_running_loop()
    start = loop.time()
    metric_reader.get_metrics_data()
    elapsed = loop.time() - start

    # Сбор на том же loop'е не может ждать снимок синхронно — это дедлок.
    assert elapsed < 1.0
    await asyncio.wait_for(repo.called.wait(), 5)
    assert repo.calls == 1


async def test_in_loop_snapshot_failure_is_counted(read_metric: Callable[..., float]) -> None:
    class _Broken:
        def __init__(self) -> None:
            self.called = asyncio.Event()

        async def backlog(self) -> list[SubjectBacklog]:
            self.called.set()
            raise RuntimeError("connection reset")

    repo = _Broken()
    register_backlog_gauges("orders", repo)

    read_metric("outbox_lag_seconds")
    await asyncio.wait_for(repo.called.wait(), 5)

    assert (
        await asyncio.to_thread(read_metric, "outbox_backlog_errors_total", {"instance": "orders"})
        >= 1
    )


async def test_pending_snapshot_is_not_duplicated_and_unregister_cancels_it(
    metric_reader: InMemoryMetricReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(metrics_module, "_SNAPSHOT_TTL", 0.0)

    class _Slow:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def backlog(self) -> list[SubjectBacklog]:
            self.calls += 1
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return []

    repo = _Slow()
    registration = register_backlog_gauges("orders", repo)

    metric_reader.get_metrics_data()
    await asyncio.wait_for(repo.entered.wait(), 5)
    metric_reader.get_metrics_data()

    # Второй сбор не заказывает снимок поверх незавершённого.
    assert repo.calls == 1
    registration.unregister()
    await asyncio.sleep(0)
    assert repo.cancelled.is_set()


async def test_backlog_gauges_are_optional() -> None:
    class _NoBacklog(FakeRepo):
        backlog = None  # type: ignore[assignment]

    assert _outbox(_NoBacklog()).register_backlog_gauges() is None


async def test_backlog_snapshot_failure_is_counted(read_metric: Callable[..., float]) -> None:
    class _Broken(FakeRepo):
        async def backlog(self) -> list[SubjectBacklog]:
            raise RuntimeError("connection reset")

    register_backlog_gauges("orders", _Broken())

    assert await asyncio.to_thread(read_metric, "outbox_lag_seconds") == 0
    assert (
        await asyncio.to_thread(read_metric, "outbox_backlog_errors_total", {"instance": "orders"})
        >= 1
    )


async def test_publisher_and_worker_share_the_instance() -> None:
    repo = FakeRepo([message(1)])
    outbox = new_outbox(
        "orders", repo, new_stream_transport(natstest.new(), "orders"), pgtest.Identity(), Config()
    )

    await outbox.publisher().publish("order.created", {"id": 1})
    await outbox.worker().run()

    assert outbox.name == "orders"
    assert outbox.bus() is outbox.publisher()
    assert repo.inserted[0][0] == "order.created"
    assert repo.marked


async def _noop() -> None:
    return None
