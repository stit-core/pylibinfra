import asyncio

from dishka import Provider, Scope, make_async_container, provide

from pylibinfra.logkit import Logger, nop
from pylibinfra.timerkit import PeriodicJob, Runner, TimerkitProvider


async def test_provider_starts_and_stops_runner() -> None:
    count = 0

    async def handler() -> None:
        nonlocal count
        count += 1

    class _Deps(Provider):
        scope = Scope.APP

        @provide
        def jobs(self) -> list[PeriodicJob]:
            return [PeriodicJob(name="tick", interval=0.01, handler=handler)]

        @provide
        def log(self) -> Logger:
            return nop()

    container = make_async_container(TimerkitProvider(), _Deps())

    runner = await container.get(Runner)
    assert runner is not None
    await asyncio.sleep(0.1)
    assert count >= 3

    # close() триггерит async-generator-выход → Runner.stop() отменяет задачи.
    await container.close()
    snapshot = count
    await asyncio.sleep(0.05)
    assert count == snapshot
