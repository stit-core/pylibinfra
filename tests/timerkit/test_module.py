import asyncio
from datetime import UTC, datetime

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.clockkit import Clock, ClockProvider, Real, Tickable
from pylibinfra.logkit import Logger, nop
from pylibinfra.timerkit import Job, Jobs, Runner, TimerkitProvider

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _AppProvider(Provider):
    """App-Provider консьюмера: собирает `Jobs` и перекрывает часы тестовыми."""

    scope = Scope.APP

    def __init__(self, jobs: Jobs, clock: Clock) -> None:
        super().__init__()
        self._jobs = jobs
        self._clock = clock

    @provide
    def jobs(self) -> Jobs:
        return self._jobs

    @provide
    def clock(self) -> Clock:
        return self._clock

    @provide
    def log(self) -> Logger:
        return nop()


async def test_provider_starts_runner_and_stops_it_on_close() -> None:
    calls = 0

    async def handler() -> None:
        nonlocal calls
        calls += 1

    clock = Tickable(T0)
    container = make_async_container(
        TimerkitProvider(),
        _AppProvider((Job(name="tick", interval=60, run=handler),), clock),
    )

    runner = await container.get(Runner)
    assert isinstance(runner, Runner)

    await clock.wait_sleepers(1)
    clock.advance(60)
    await clock.wait_sleepers(1)
    assert calls == 1

    await container.close()

    clock.advance(60)
    assert clock.sleepers() == 0
    assert calls == 1
    # Закрытие контейнера не оставляет фоновых задач кита.
    assert [task for task in asyncio.all_tasks() if task.get_name() == "tick"] == []


async def test_provider_defaults_to_empty_jobs_with_clock_from_clockkit() -> None:
    class _LogProvider(Provider):
        scope = Scope.APP

        @provide
        def log(self) -> Logger:
            return nop()

    container = make_async_container(TimerkitProvider(), ClockProvider(), _LogProvider())

    runner = await container.get(Runner)
    assert isinstance(runner, Runner)
    assert isinstance(await container.get(Clock), Real)

    await container.close()


async def test_consumer_clock_provider_overrides_the_default() -> None:
    clock = Tickable(T0)
    container = make_async_container(TimerkitProvider(), _AppProvider((), clock))

    assert await container.get(Clock) is clock

    await container.close()
