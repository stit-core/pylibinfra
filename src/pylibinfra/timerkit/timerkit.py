"""Периодические джобы поверх asyncio: приложение собирает список PeriodicJob
(имя, интервал, корутина-handler), а Runner крутит каждую отдельной задачей и
ловит исключения без падения процесса (аналог recover() в Go-тикере).
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pylibinfra.logkit import Logger


@dataclass(frozen=True)
class PeriodicJob:
    name: str
    interval: float
    handler: Callable[[], Awaitable[None]]


class Runner:
    def __init__(self, jobs: list[PeriodicJob], log: Logger) -> None:
        self._jobs = jobs
        self._log = log
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._loop(job), name=job.name) for job in self._jobs]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        # Дожидаемся фактической отмены: иначе задачи переживут lifecycle-выход
        # и asyncio пожалуется на "task was destroyed but it is pending".
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _loop(self, job: PeriodicJob) -> None:
        while True:
            try:
                await job.handler()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("timerkit: job error", job=job.name, error=str(exc))
            await asyncio.sleep(job.interval)
