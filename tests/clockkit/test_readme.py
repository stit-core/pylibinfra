"""Быстрый старт README clockkit — исполняемый: сервис на Clock, Fixed и Tickable."""

import asyncio
from datetime import UTC, datetime

from dishka import make_async_container

from pylibinfra.clockkit import Clock, ClockProvider, Fixed, Real, Tickable


class Session:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock if clock is not None else Real()

    def expired(self, deadline: datetime) -> bool:
        return self._clock.now() > deadline

    async def wait_window(self, seconds: float) -> None:
        await self._clock.sleep(seconds)


async def test_readme_container() -> None:
    container = make_async_container(ClockProvider())

    clock = await container.get(Clock)

    assert isinstance(clock, Real)
    await container.close()


def test_readme_fixed() -> None:
    clock = Fixed(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

    assert clock.now() == clock.now()
    assert Session(clock).expired(datetime(2026, 1, 1, tzinfo=UTC)) is True


async def test_readme_tickable() -> None:
    clock = Tickable(datetime(2026, 1, 1, tzinfo=UTC))
    session = Session(clock)

    task = asyncio.create_task(session.wait_window(30))
    await clock.wait_sleepers(1)
    clock.advance(30)
    await task
