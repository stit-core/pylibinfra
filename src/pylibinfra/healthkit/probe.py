"""Probe — имя плюс асинхронная проверка; with_timeout ограничивает одну проверку."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

Check = Callable[[], Awaitable[None]]


class ProbeTimeoutError(TimeoutError):
    """Проверка не уложилась в дедлайн `with_timeout`."""

    def __init__(self, name: str, seconds: float) -> None:
        super().__init__(f"healthkit: probe {name!r} timed out after {seconds:g}s")
        self.name = name
        self.seconds = seconds


@dataclass(frozen=True, slots=True)
class Probe:
    """Именованная проверка готовности зависимости; `name` — идентификатор в логе `/ready`."""

    name: str
    check: Check

    async def run(self) -> None:
        """Выполняет проверку; её ошибка поднимается как есть (кит префиксует её своим именем)."""
        await self.check()


def new(name: str, check: Check) -> Probe:
    """Собирает Probe из имени и проверки."""
    return Probe(name, check)


def with_timeout(probe: Probe, seconds: float) -> Probe:
    """Ограничивает время одной проверки; `seconds <= 0` — проба без изменений.

    Просроченная проверка отменяется, но `run` не ждёт её завершения: проба,
    игнорирующая отмену, не должна держать `/ready` дольше дедлайна.
    """
    if seconds <= 0:
        return probe

    async def check() -> None:
        task = asyncio.ensure_future(probe.check())
        try:
            done, _ = await asyncio.wait({task}, timeout=seconds)
        except asyncio.CancelledError:
            _abandon(task)
            raise
        if not done:
            _abandon(task)
            raise ProbeTimeoutError(probe.name, seconds)
        await task

    return Probe(probe.name, check)


def _abandon(task: asyncio.Future[None]) -> None:
    task.cancel()
    # Брошенная проверка досчитывает сама; её исход некому забрать, и без этого
    # asyncio печатает "exception was never retrieved".
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


Probes = tuple[Probe, ...]
