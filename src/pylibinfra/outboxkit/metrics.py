"""Инструменты outbox + BacklogReporter/register_backlog_gauges.

Эмитит outbox_events_{inserted,processed,dead}_total, outbox_batch_size,
outbox_batch_duration_seconds, outbox_publish_duration_seconds.
outbox_lag_seconds{subject} и outbox_pending_count{subject} — opt-in observable
gauges поверх BacklogReporter (register_backlog_gauges).
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation


@dataclass(frozen=True, slots=True)
class SubjectBacklog:
    """Снимок очереди неопубликованных событий по subject'у: возраст самого
    старого (oldest, секунды) и количество (pending)."""

    subject: str
    oldest: float
    pending: int


@runtime_checkable
class BacklogReporter(Protocol):
    """Optional-порт для Repository: реализующие его store'ы (см. pgstore)
    экспонируют backlog по subject'ам. Через register_backlog_gauges это
    становится outbox_lag_seconds и outbox_pending_count."""

    async def backlog(self) -> list[SubjectBacklog]: ...


class OutboxInstruments:
    def __init__(self) -> None:
        meter = metrics.get_meter("app")

        self.inserted_total = meter.create_counter(
            "outbox_events_inserted_total", description="Events inserted into outbox"
        )
        self.processed_total = meter.create_counter(
            "outbox_events_processed_total", description="Events processed by outbox worker"
        )
        self.dead_total = meter.create_counter(
            "outbox_events_dead_total",
            description="Events marked dead after exhausting MaxAttempts",
        )
        self.batch_size = meter.create_histogram(
            "outbox_batch_size", description="Batch size pulled per worker tick"
        )
        self.batch_duration = meter.create_histogram(
            "outbox_batch_duration_seconds",
            unit="s",
            description="Worker tick duration",
        )
        self.publish_duration = meter.create_histogram(
            "outbox_publish_duration_seconds",
            unit="s",
            description="Duration of the batch publish phase per worker tick",
        )


def register_backlog_gauges(reporter: BacklogReporter) -> None:
    """Регистрирует observable gauge'и outbox_lag_seconds{subject} и
    outbox_pending_count{subject} поверх BacklogReporter. Callback OTel синхронный,
    а backlog() асинхронный — исполняем его на event-loop'е, захваченном в момент
    регистрации (его держит lifecycle приложения), через run_coroutine_threadsafe:
    PeriodicReader дёргает callback из своего фонового потока."""
    meter = metrics.get_meter("app")
    loop = asyncio.get_event_loop()

    def snapshot() -> list[SubjectBacklog]:
        try:
            fut = asyncio.run_coroutine_threadsafe(reporter.backlog(), loop)
            return fut.result(timeout=5.0)
        except Exception:
            # Observability не должна валить процесс — пропускаем тихо.
            return []

    def observe_lag(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(b.oldest, {"subject": b.subject}) for b in snapshot()]

    def observe_pending(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(b.pending, {"subject": b.subject}) for b in snapshot()]

    meter.create_observable_gauge(
        "outbox_lag_seconds",
        callbacks=[observe_lag],
        unit="s",
        description="Age of oldest unpublished event per subject",
    )
    meter.create_observable_gauge(
        "outbox_pending_count",
        callbacks=[observe_pending],
        description="Number of unpublished events per subject",
    )
