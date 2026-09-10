"""Быстрый старт README timerkit — исполняемый: фабрика Job, App-Provider, lifecycle."""

import asyncio
from datetime import UTC, datetime

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.clockkit import Clock, Tickable
from pylibinfra.logkit import Logger, nop
from pylibinfra.timerkit import Job, Jobs, Runner, TimerkitProvider


class Service:
    def __init__(self) -> None:
        self.purged = 0
        self.ran = asyncio.Event()

    async def purge_expired_sessions(self) -> None:
        self.purged += 1
        self.ran.set()


def cleanup_job(service: Service) -> Job:
    return Job(
        name="expired-sessions-cleanup",
        interval=300,
        timeout=60,
        jitter=30,
        run_on_start=True,
        run=service.purge_expired_sessions,
    )


class AppProvider(Provider):
    scope = Scope.APP

    def __init__(self, service: Service, clock: Clock) -> None:
        super().__init__()
        self._service = service
        self._clock = clock

    @provide
    def service(self) -> Service:
        return self._service

    @provide
    def jobs(self, service: Service) -> Jobs:
        return (cleanup_job(service),)

    @provide
    def clock(self) -> Clock:
        return self._clock

    @provide
    def log(self) -> Logger:
        return nop()


async def test_readme_quickstart() -> None:
    service = Service()
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    container = make_async_container(TimerkitProvider(), AppProvider(service, clock))

    runner = await container.get(Runner)
    assert isinstance(runner, Runner)

    await service.ran.wait()
    assert service.purged == 1

    await container.close()


async def test_readme_without_di() -> None:
    service = Service()
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    runner = Runner((cleanup_job(service),), nop(), clock=clock)

    runner.start()
    await service.ran.wait()
    await runner.stop()

    assert service.purged == 1
