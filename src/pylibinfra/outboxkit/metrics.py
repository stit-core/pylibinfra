"""Инструменты `outbox_*` и observable-gauge'и backlog'а поверх `BacklogReporter`."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from opentelemetry.metrics import CallbackOptions, Counter, Observation

from pylibinfra._internal import otelkit

_KIT = "outboxkit"
_SNAPSHOT_TIMEOUT = 5.0
# Окно переиспользования снимка: пара gauge'ей опрашивается в одном цикле сбора.
_SNAPSHOT_TTL = 1.0


@dataclass(frozen=True, slots=True)
class SubjectBacklog:
    """Снимок очереди по subject'у: возраст самого старого (секунды) и число неопубликованных."""

    subject: str
    oldest: float
    pending: int


@runtime_checkable
class BacklogReporter(Protocol):
    """Опциональный интерфейс `Repository`: снимок очереди для lag/pending-гейджей."""

    async def backlog(self) -> list[SubjectBacklog]:
        """Возраст старейшего и число неопубликованных (не-dead) событий на каждый subject."""


class OutboxInstruments:
    """Счётчики и гистограммы одного outbox-инстанса (label `instance` — его имя)."""

    def __init__(self, instance: str) -> None:
        meter = otelkit.meter(_KIT)
        self.instance = instance
        self.attrs = {"instance": instance}
        self.inserted_total = meter.create_counter(
            "outbox_events_inserted_total", description="Events inserted into outbox"
        )
        self.processed_total = meter.create_counter(
            "outbox_events_processed_total", description="Events processed by outbox worker"
        )
        self.dead_total = meter.create_counter(
            "outbox_events_dead_total",
            description="Events marked dead after exhausting OUTBOX_MAX_ATTEMPTS",
        )
        self.revived_total = meter.create_counter(
            "outbox_revived_total", description="Dead events returned to the queue by revive_dead"
        )
        self.batch_size = meter.create_histogram(
            "outbox_batch_size", description="Batch size pulled per worker tick"
        )
        self.batch_duration = otelkit.duration_histogram(
            meter, "outbox_batch_duration_seconds", "Worker tick duration"
        )
        self.publish_duration = otelkit.duration_histogram(
            meter, "outbox_publish_duration_seconds", "Duration of the publish phase per tick"
        )


class BacklogRegistration:
    """Handle наблюдения backlog'а: `unregister` останавливает снимки инстанса.

    OTel-Python не умеет снимать observable-инструмент с провайдера, поэтому
    инструмент остаётся, а callback перестаёт наблюдать этот инстанс.
    """

    def __init__(self, backlog: _Backlog) -> None:
        self._active = True
        self._backlog = backlog

    @property
    def active(self) -> bool:
        """Наблюдает ли регистрация свой инстанс прямо сейчас."""
        return self._active

    def unregister(self) -> None:
        """Останавливает наблюдение (idempotent)."""
        self._active = False
        self._backlog.stop()


class _Backlog:
    """Снимок очереди для пары observable-gauge'ей: один поход в хранилище на цикл сбора."""

    def __init__(self, reporter: BacklogReporter, errors: Counter, attrs: dict[str, str]) -> None:
        self._reporter = reporter
        self._errors = errors
        self._attrs = attrs
        self._loop = asyncio.get_running_loop()
        self._value: list[SubjectBacklog] = []
        self._taken = float("-inf")
        self._refresh: asyncio.Task[None] | None = None

    def read(self) -> list[SubjectBacklog]:
        """Снимок очереди; свежее `_SNAPSHOT_TTL` переиспользуется парным gauge'ем."""
        now = time.monotonic()
        if now - self._taken < _SNAPSHOT_TTL:
            return self._value
        self._taken = now
        if self._on_own_loop():
            # Сбор идёт на нашем же loop'е: блокирующее ожидание снимка — дедлок.
            self._schedule()
            return self._value
        try:
            future = asyncio.run_coroutine_threadsafe(self._snapshot(), self._loop)
            self._value = future.result(timeout=_SNAPSHOT_TIMEOUT)
        except Exception:
            self._errors.add(1, self._attrs)
            self._value = []
        return self._value

    def stop(self) -> None:
        """Отменяет незавершённое обновление снимка."""
        if self._refresh is not None:
            self._refresh.cancel()
            self._refresh = None

    def _on_own_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def _schedule(self) -> None:
        if self._refresh is not None and not self._refresh.done():
            return
        self._refresh = self._loop.create_task(self._update())

    async def _update(self) -> None:
        try:
            self._value = await self._snapshot()
        except Exception:
            self._errors.add(1, self._attrs)
            self._value = []

    async def _snapshot(self) -> list[SubjectBacklog]:
        return await asyncio.wait_for(self._reporter.backlog(), _SNAPSHOT_TIMEOUT)


def register_backlog_gauges(instance: str, reporter: BacklogReporter) -> BacklogRegistration:
    """Подключает `outbox_lag_seconds` и `outbox_pending_count` поверх `BacklogReporter`.

    Зовётся из работающего event-loop'а: callback OTel синхронный и исполняется в
    потоке сборщика метрик, а `backlog()` асинхронный и идёт на захваченном здесь
    loop'е. Сбой снимка не валит экспорт — он считается `outbox_backlog_errors_total`.
    """
    meter = otelkit.meter(_KIT)
    attrs = {"instance": instance}
    backlog = _Backlog(
        reporter,
        meter.create_counter(
            "outbox_backlog_errors_total",
            description="Failed backlog snapshots behind outbox_lag_seconds/outbox_pending_count",
        ),
        attrs,
    )
    registration = BacklogRegistration(backlog)

    def snapshot() -> list[SubjectBacklog]:
        return backlog.read() if registration.active else []

    def observe_lag(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(b.oldest, {**attrs, "subject": b.subject}) for b in snapshot()]

    def observe_pending(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(b.pending, {**attrs, "subject": b.subject}) for b in snapshot()]

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
    return registration
