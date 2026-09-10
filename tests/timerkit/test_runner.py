import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from pylibinfra.clockkit import Tickable
from pylibinfra.logkit import nop
from pylibinfra.timerkit import Job, JobError, Runner

from .conftest import CaptureLogger

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class Counter:
    """Считает запуски job'а и даёт её телу произвольное поведение."""

    def __init__(self, body: Callable[[], object] | None = None) -> None:
        self.calls = 0
        self._body = body

    async def run(self) -> None:
        self.calls += 1
        if self._body is not None:
            result = self._body()
            if asyncio.iscoroutine(result):
                await result


async def test_first_run_after_interval() -> None:
    clock = Tickable(T0)
    counter = Counter()
    runner = Runner((Job(name="tick", interval=60, run=counter.run),), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)
    assert counter.calls == 0

    clock.advance(60)
    await clock.wait_sleepers(1)
    assert counter.calls == 1

    clock.advance(60)
    await clock.wait_sleepers(1)
    assert counter.calls == 2

    await runner.stop()


async def test_run_on_start_fires_immediately() -> None:
    clock = Tickable(T0)
    counter = Counter()
    job = Job(name="tick", interval=60, run=counter.run, run_on_start=True)
    runner = Runner((job,), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)

    assert counter.calls == 1

    await runner.stop()


async def test_wait_starts_after_previous_run_finished() -> None:
    clock = Tickable(T0)
    release = asyncio.Event()
    entered = asyncio.Event()

    async def body() -> None:
        entered.set()
        await release.wait()

    counter = Counter(body)
    runner = Runner((Job(name="slow", interval=60, run=counter.run),), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)
    clock.advance(60)
    await entered.wait()

    # Пока запуск идёт, следующего ожидания нет — периоды не накладываются.
    assert clock.sleepers() == 0
    release.set()
    await clock.wait_sleepers(1)
    assert counter.calls == 1

    await runner.stop()


async def test_jitter_extends_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pylibinfra.timerkit.timerkit.random.random", lambda: 0.5)
    clock = Tickable(T0)
    counter = Counter()
    job = Job(name="tick", interval=60, run=counter.run, jitter=20)
    runner = Runner((job,), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)

    clock.advance(60)
    await asyncio.sleep(0)
    assert counter.calls == 0

    clock.advance(10)
    await clock.wait_sleepers(1)
    assert counter.calls == 1

    await runner.stop()


async def test_error_in_one_job_does_not_stop_the_loop_or_neighbours(log: CaptureLogger) -> None:
    clock = Tickable(T0)

    def boom() -> None:
        raise RuntimeError("boom")

    bad, good = Counter(boom), Counter()
    runner = Runner(
        (
            Job(name="bad", interval=60, run=bad.run, run_on_start=True),
            Job(name="good", interval=60, run=good.run, run_on_start=True),
        ),
        log,
        clock=clock,
    )

    runner.start()
    await clock.wait_sleepers(2)
    clock.advance(60)
    await clock.wait_sleepers(2)

    assert (bad.calls, good.calls) == (2, 2)
    record = log.find("timerkit: job error")
    assert record is not None
    assert record.level == "error"
    assert record.fields == {"job": "bad", "error": "boom"}

    await runner.stop()


async def test_timeout_bounds_one_run(log: CaptureLogger) -> None:
    clock = Tickable(T0)
    never = asyncio.Event()
    counter = Counter(never.wait)
    job = Job(name="hang", interval=60, run=counter.run, timeout=0.01, run_on_start=True)
    runner = Runner((job,), log, clock=clock)

    runner.start()
    await clock.wait_sleepers(1)

    record = log.find("timerkit: job error")
    assert record is not None
    assert record.fields == {"job": "hang", "error": "TimeoutError"}

    await runner.stop()


async def test_metrics_record_result_and_duration(
    read_metric: Callable[..., float], log: CaptureLogger
) -> None:
    clock = Tickable(T0)

    def boom() -> None:
        raise RuntimeError("boom")

    runner = Runner(
        (
            Job(name="ok", interval=60, run=Counter().run, run_on_start=True),
            Job(name="bad", interval=60, run=Counter(boom).run, run_on_start=True),
        ),
        log,
        clock=clock,
    )

    runner.start()
    await clock.wait_sleepers(2)
    await runner.stop()

    assert read_metric("timer_job_runs_total", {"job": "ok", "result": "ok"}) == 1
    assert read_metric("timer_job_runs_total", {"job": "bad", "result": "error"}) == 1
    assert read_metric("timer_job_runs_total", {"job": "ok", "result": "error"}) == 0
    assert read_metric("timer_job_duration_seconds", {"job": "ok"}) == 1


async def test_span_per_run_is_root_and_marks_error(span_exporter: InMemorySpanExporter) -> None:
    clock = Tickable(T0)

    def boom() -> None:
        raise RuntimeError("boom")

    runner = Runner(
        (
            Job(name="ok", interval=60, run=Counter().run, run_on_start=True),
            Job(name="bad", interval=60, run=Counter(boom).run, run_on_start=True),
        ),
        nop(),
        clock=clock,
    )

    runner.start()
    await clock.wait_sleepers(2)
    await runner.stop()

    spans = {span.name: span for span in span_exporter.get_finished_spans()}
    assert set(spans) == {"timerkit.job ok", "timerkit.job bad"}

    ok_span = spans["timerkit.job ok"]
    assert ok_span.kind is SpanKind.INTERNAL
    assert ok_span.attributes is not None
    assert ok_span.attributes["timer.job"] == "ok"
    assert ok_span.parent is None  # root: запуск открывает свой трейс

    bad_span = spans["timerkit.job bad"]
    assert bad_span.status.status_code is StatusCode.ERROR
    assert [event.name for event in bad_span.events] == ["exception"]


async def test_stop_waits_for_a_running_job(log: CaptureLogger) -> None:
    clock = Tickable(T0)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def body() -> None:
        entered.set()
        await release.wait()

    counter = Counter(body)
    job = Job(name="slow", interval=60, run=counter.run, run_on_start=True)
    runner = Runner((job,), log, clock=clock)

    runner.start()
    await entered.wait()
    stopping = asyncio.create_task(runner.stop(timeout=5))
    await asyncio.sleep(0)
    assert not stopping.done()

    release.set()
    await asyncio.wait_for(stopping, timeout=1)

    assert log.find("timerkit: stop: jobs still running") is None


async def test_stop_cancels_jobs_that_miss_the_deadline(
    log: CaptureLogger, read_metric: Callable[..., float]
) -> None:
    clock = Tickable(T0)
    entered = asyncio.Event()

    async def body() -> None:
        entered.set()
        await asyncio.sleep(30)

    job = Job(name="stuck", interval=60, run=Counter(body).run, run_on_start=True)
    runner = Runner((job,), log, clock=clock)

    runner.start()
    await entered.wait()

    await runner.stop(timeout=0.01)

    record = log.find("timerkit: stop: jobs still running")
    assert record is not None
    assert record.level == "error"
    assert record.fields == {"jobs": "stuck", "timeout": 0.01}
    # Отменённый запуск не должен выглядеть успешным в метрике исходов.
    assert read_metric("timer_job_runs_total", {"job": "stuck", "result": "cancelled"}) == 1
    assert read_metric("timer_job_runs_total", {"job": "stuck", "result": "ok"}) == 0


async def test_cancelled_run_marks_its_span(span_exporter: InMemorySpanExporter) -> None:
    entered = asyncio.Event()

    async def body() -> None:
        entered.set()
        await asyncio.sleep(30)

    job = Job(name="stuck", interval=60, run=Counter(body).run, run_on_start=True)
    runner = Runner((job,), nop(), clock=Tickable(T0))

    runner.start()
    await entered.wait()
    await runner.stop(timeout=0.01)

    span = next(s for s in span_exporter.get_finished_spans() if s.name == "timerkit.job stuck")
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "cancelled"
    assert span.events == ()


async def test_second_start_does_not_duplicate_running_jobs() -> None:
    clock = Tickable(T0)
    counter = Counter()
    runner = Runner((Job(name="tick", interval=60, run=counter.run),), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)
    runner.start()
    await asyncio.sleep(0)

    # Второй start не поднимает второе поколение задач: иначе job шла бы дважды,
    # а первые задачи остались бы вне stop.
    assert clock.sleepers() == 1

    clock.advance(60)
    await clock.wait_sleepers(1)
    assert counter.calls == 1

    await runner.stop(timeout=1)
    assert clock.sleepers() == 0
    assert [task for task in asyncio.all_tasks() if task.get_name() == "tick"] == []


async def test_restart_after_stop_schedules_again() -> None:
    clock = Tickable(T0)
    counter = Counter()
    runner = Runner((Job(name="tick", interval=60, run=counter.run),), nop(), clock=clock)

    runner.start()
    await clock.wait_sleepers(1)
    await runner.stop(timeout=1)

    runner.start()
    await clock.wait_sleepers(1)
    clock.advance(60)
    await clock.wait_sleepers(1)

    assert counter.calls == 1

    await runner.stop(timeout=1)


async def test_stop_is_idempotent_and_safe_without_start() -> None:
    runner = Runner((Job(name="tick", interval=60, run=Counter().run),), nop(), clock=Tickable(T0))

    await runner.stop()

    runner.start()
    await runner.stop()
    await runner.stop()


async def test_start_rejects_invalid_jobs() -> None:
    runner = Runner((Job(name="", interval=60, run=Counter().run),), nop(), clock=Tickable(T0))

    with pytest.raises(JobError):
        runner.start()

    await runner.stop()


async def test_runner_defaults_to_real_clock() -> None:
    counter = Counter()
    runner = Runner((Job(name="tick", interval=0.01, run=counter.run, run_on_start=True),), nop())

    runner.start()
    await asyncio.sleep(0.05)
    await runner.stop()

    assert counter.calls >= 1


async def test_timeout_does_not_affect_a_run_that_fits(log: CaptureLogger) -> None:
    clock = Tickable(T0)
    counter = Counter()
    job = Job(name="fast", interval=60, run=counter.run, timeout=5, run_on_start=True)
    runner = Runner((job,), log, clock=clock)

    runner.start()
    await clock.wait_sleepers(1)

    assert counter.calls == 1
    assert log.records == []

    await runner.stop()
