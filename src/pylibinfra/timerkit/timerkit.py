"""Job — описание периодической задачи; Runner — её расписание, наблюдаемость и остановка."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from opentelemetry.context import Context
from opentelemetry.trace import SpanKind, StatusCode

from pylibinfra._internal import otelkit
from pylibinfra.clockkit import Clock, Real
from pylibinfra.logkit import Logger

DEFAULT_STOP_TIMEOUT = 15.0


class JobError(ValueError):
    """Невалидная Job: пустое имя, нет `run`, `interval <= 0`, отрицательные поля, дубль имени."""


@dataclass(frozen=True, slots=True)
class Job:
    """Периодическая задача: `run` вызывается каждые `interval` секунд, пока жив контейнер.

    `name` — идентификатор в логах, спанах и label'е `job`; обязателен и уникален.
    `timeout` — дедлайн одного запуска (0 — без дедлайна); `jitter` — случайная
    добавка [0, jitter) к ожиданию; `run_on_start` — первый запуск сразу.
    """

    name: str
    interval: float
    run: Callable[[], Awaitable[None]]
    timeout: float = 0.0
    jitter: float = 0.0
    run_on_start: bool = False


Jobs = tuple[Job, ...]


class Runner:
    """Крутит каждую Job в своей задаче: расписание по `Clock`, спан и метрики на запуск.

    `start` валидирует набор (`JobError`) и поднимает задачи, `stop` дожидается
    идущих запусков в пределах дедлайна и отменяет не уложившиеся.
    """

    def __init__(self, jobs: Jobs, log: Logger, *, clock: Clock | None = None) -> None:
        self._jobs = jobs
        self._log = log
        self._clock = clock if clock is not None else Real()
        self._tasks: list[asyncio.Task[None]] = []
        self._stopping = asyncio.Event()
        meter = otelkit.meter("timerkit")
        self._runs = meter.create_counter(
            "timer_job_runs_total", description="Timer job runs by result"
        )
        self._duration = otelkit.duration_histogram(
            meter, "timer_job_duration_seconds", "Timer job run duration"
        )
        self._tracer = otelkit.tracer("timerkit")

    def start(self) -> None:
        """Валидирует набор job'ов и поднимает по задаче на каждую; невалидный — `JobError`.

        Повторный вызов при живых задачах — no-op: иначе каждая job крутилась бы
        дважды, а первое поколение задач осталось бы вне `stop`.
        """
        if self._tasks:
            return
        validate(self._jobs)
        self._stopping = asyncio.Event()
        self._tasks = [asyncio.create_task(self._loop(job), name=job.name) for job in self._jobs]

    # ASYNC109: дедлайн — часть контракта остановки (не уложившиеся запуски отменяются
    # и логируются), обёртка asyncio.timeout вызывающего дала бы исключение вместо этого.
    async def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT) -> None:  # noqa: ASYNC109
        """Сигналит остановку и ждёт идущие запуски; не уложившиеся отменяет и пишет error-лог."""
        self._stopping.set()
        tasks, self._tasks = self._tasks, []
        if not tasks:
            return

        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            names = ", ".join(sorted(task.get_name() for task in pending))
            for task in pending:
                task.cancel()
            self._log.error("timerkit: stop: jobs still running", jobs=names, timeout=timeout)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self, job: Job) -> None:
        if job.run_on_start:
            await self._once(job)
        while await self._wait(job):
            await self._once(job)

    async def _wait(self, job: Job) -> bool:
        """Ждёт следующий запуск от конца предыдущего; False — пришла остановка."""
        sleep = asyncio.create_task(self._clock.sleep(_delay(job)))
        stop = asyncio.create_task(self._stopping.wait())
        try:
            await asyncio.wait({sleep, stop}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleep.cancel()
            stop.cancel()
            await asyncio.gather(sleep, stop, return_exceptions=True)
        return not self._stopping.is_set()

    async def _once(self, job: Job) -> None:
        attrs = {"job": job.name}
        # Пустой Context — запуск открывает свой трейс: инструментация внутри `run`
        # становится его детьми, а не сиротами под трейсом старта приложения.
        with self._tracer.start_as_current_span(
            f"timerkit.job {job.name}",
            context=Context(),
            kind=SpanKind.INTERNAL,
            attributes={"timer.job": job.name},
        ) as span:
            start = self._clock.now()
            result = "ok"
            try:
                await self._invoke(job)
            except asyncio.CancelledError:
                # Отмена приходит только от `stop` по истечении дедлайна: считать её
                # успехом значило бы прятать не уложившиеся запуски от алертов.
                result = "cancelled"
                span.set_status(StatusCode.ERROR, result)
                raise
            except Exception as exc:
                result = "error"
                text = str(exc) or type(exc).__name__
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, text)
                self._log.error("timerkit: job error", job=job.name, error=text)
            finally:
                self._duration.record(self._clock.since(start), attrs)
                self._runs.add(1, {**attrs, "result": result})

    async def _invoke(self, job: Job) -> None:
        # Дедлайн идёт по реальному времени (asyncio), а не по Clock: он ограничивает
        # зависший I/O, который подменённые часы всё равно не двигают.
        if job.timeout > 0:
            async with asyncio.timeout(job.timeout):
                await job.run()
            return
        await job.run()


def validate(jobs: Jobs) -> None:
    """Проверяет набор job'ов; первая проблема — `JobError` с именем job'а."""
    seen: set[str] = set()
    for job in jobs:
        if not job.name:
            raise JobError("timerkit: invalid job: name is empty")
        if job.run is None:
            raise JobError(f"timerkit: invalid job {job.name!r}: run is none")
        if job.interval <= 0:
            raise JobError(
                f"timerkit: invalid job {job.name!r}: interval must be > 0, got {job.interval:g}"
            )
        if job.timeout < 0 or job.jitter < 0:
            raise JobError(f"timerkit: invalid job {job.name!r}: timeout and jitter must be >= 0")
        if job.name in seen:
            raise JobError(f"timerkit: invalid job: duplicate job name {job.name!r}")
        seen.add(job.name)


def _delay(job: Job) -> float:
    if job.jitter <= 0:
        return job.interval
    # Джиттер расписания размазывает запуски однотипных job'ов между инстансами —
    # это не криптография.
    return job.interval + random.random() * job.jitter  # noqa: S311
