"""Инструменты outbox + register_lag_gauge.

Эмитит outbox_events_{inserted,processed}_total, outbox_batch_size,
outbox_batch_duration_seconds, outbox_publish_duration_seconds.
outbox_lag_seconds{subject} — opt-in observable gauge поверх LagReporter.
"""

import asyncio
from collections.abc import Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

from .outbox import LagReporter


class OutboxInstruments:
    def __init__(self) -> None:
        meter = metrics.get_meter("app")

        self.inserted_total = meter.create_counter(
            "outbox_events_inserted_total", description="Events inserted into outbox"
        )
        self.processed_total = meter.create_counter(
            "outbox_events_processed_total", description="Events processed by outbox worker"
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
            description="Time to publish + mark single event",
        )


def register_lag_gauge(reporter: LagReporter) -> None:
    """Регистрирует observable gauge outbox_lag_seconds{subject} поверх
    LagReporter. Callback OTel синхронный, а lag_by_subject асинхронный —
    исполняем его на event-loop'е, захваченном в момент регистрации (его
    держит lifecycle приложения), через run_coroutine_threadsafe: PeriodicReader
    дёргает callback из своего фонового потока."""
    meter = metrics.get_meter("app")
    loop = asyncio.get_event_loop()

    def observe(_: CallbackOptions) -> Iterable[Observation]:
        try:
            fut = asyncio.run_coroutine_threadsafe(reporter.lag_by_subject(), loop)
            lags = fut.result(timeout=5.0)
        except Exception:
            # Observability не должна валить процесс — пропускаем тихо.
            return []
        return [Observation(seconds, {"subject": subj}) for subj, seconds in lags.items()]

    meter.create_observable_gauge(
        "outbox_lag_seconds",
        callbacks=[observe],
        unit="s",
        description="Age of oldest unpublished event per subject",
    )
