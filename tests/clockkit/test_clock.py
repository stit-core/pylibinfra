import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from dishka import make_async_container

from pylibinfra.clockkit import Clock, ClockProvider, Fixed, Real, Tickable

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_real_now_is_aware_utc_and_since() -> None:
    clock: Clock = Real()
    now = clock.now()

    assert now.tzinfo is UTC
    assert clock.since(now - timedelta(seconds=1)) >= 1.0
    assert isinstance(clock, Clock)


async def test_real_sleep() -> None:
    await Real().sleep(0)


def test_fixed_never_moves() -> None:
    clock = Fixed(T0)

    assert clock.now() == T0
    assert clock.now() == T0
    assert clock.since(T0 - timedelta(minutes=1)) == 60.0
    assert clock.since(T0 + timedelta(minutes=1)) == -60.0
    assert isinstance(clock, Clock)


async def test_fixed_sleep_positive_never_returns() -> None:
    task = asyncio.create_task(Fixed(T0).sleep(1))
    await asyncio.sleep(0.01)

    assert not task.done()

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await Fixed(T0).sleep(0)


async def test_tickable_advance_wakes_sleepers_in_order() -> None:
    clock = Tickable(T0)
    done: list[str] = []

    async def sleeper(name: str, seconds: float) -> None:
        await clock.sleep(seconds)
        done.append(name)

    first = asyncio.create_task(sleeper("a", 30))
    second = asyncio.create_task(sleeper("b", 90))
    await clock.wait_sleepers(2)
    assert clock.sleepers() == 2

    clock.advance(timedelta(seconds=30))
    await asyncio.sleep(0)
    assert done == ["a"]
    assert clock.sleepers() == 1

    clock.advance(60)
    await asyncio.gather(first, second)
    assert done == ["a", "b"]
    assert clock.now() == T0 + timedelta(seconds=90)
    assert clock.sleepers() == 0


async def test_tickable_wakes_all_concurrent_sleepers_with_same_deadline() -> None:
    clock = Tickable(T0)
    woken: list[int] = []

    async def sleeper(index: int) -> None:
        await clock.sleep(10)
        woken.append(index)

    tasks = [asyncio.create_task(sleeper(i)) for i in range(5)]
    await clock.wait_sleepers(5)

    clock.advance(10)
    await asyncio.gather(*tasks)

    assert sorted(woken) == [0, 1, 2, 3, 4]
    assert clock.sleepers() == 0


async def test_tickable_non_positive_sleep_returns_immediately() -> None:
    clock = Tickable(T0)

    await clock.sleep(0)
    await clock.sleep(-5)

    assert clock.sleepers() == 0


async def test_tickable_set_into_the_past_wakes_nobody() -> None:
    clock = Tickable(T0)
    task = asyncio.create_task(clock.sleep(10))
    await clock.wait_sleepers(1)

    clock.set(T0 - timedelta(days=1))
    await asyncio.sleep(0)

    assert not task.done()
    assert clock.sleepers() == 1
    assert clock.now() == T0 - timedelta(days=1)
    assert clock.since(T0) == -86400.0

    clock.set(T0 + timedelta(seconds=10))
    await task
    assert clock.sleepers() == 0


async def test_tickable_cancelled_sleep_leaves_queue() -> None:
    clock = Tickable(T0)
    task = asyncio.create_task(clock.sleep(10))
    await clock.wait_sleepers(1)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert clock.sleepers() == 0
    # Продвижение после отмены никого не будит и не падает.
    clock.advance(60)


async def test_tickable_wait_sleepers_returns_when_count_reached() -> None:
    clock = Tickable(T0)

    async def late_sleeper() -> None:
        await asyncio.sleep(0)
        await clock.sleep(5)

    task = asyncio.create_task(late_sleeper())
    await asyncio.wait_for(clock.wait_sleepers(1), timeout=1)

    assert clock.sleepers() == 1

    clock.advance(5)
    await task


async def test_tickable_wait_sleepers_no_wait_when_already_enough() -> None:
    clock = Tickable(T0)
    task = asyncio.create_task(clock.sleep(1))
    await clock.wait_sleepers(1)

    await asyncio.wait_for(clock.wait_sleepers(1), timeout=1)
    await asyncio.wait_for(clock.wait_sleepers(0), timeout=1)

    clock.advance(1)
    await task


async def test_tickable_advance_from_another_thread_wakes_the_sleeper() -> None:
    # Цикл к моменту advance блокирован без таймеров: разбудить его может только
    # постановка колбэка через call_soon_threadsafe.
    clock = Tickable(T0)
    task = asyncio.create_task(clock.sleep(10))
    await clock.wait_sleepers(1)

    threading.Thread(target=lambda: (time.sleep(0.05), clock.advance(10)), daemon=True).start()

    await asyncio.wait_for(task, timeout=5)
    assert clock.now() == T0 + timedelta(seconds=10)
    assert clock.sleepers() == 0


async def test_tickable_concurrent_advance_keeps_every_delta() -> None:
    clock = Tickable(T0)
    barrier = threading.Barrier(4)

    def tick() -> None:
        barrier.wait()
        for _ in range(50):
            clock.advance(1)

    threads = [threading.Thread(target=tick) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert clock.now() == T0 + timedelta(seconds=200)


def test_tickable_advance_survives_a_closed_loop() -> None:
    # Тест закончился, а фоновый поток всё ещё двигает часы: будить некого,
    # но и падать advance не должен.
    clock = Tickable(T0)

    async def register() -> None:
        asyncio.ensure_future(clock.sleep(10))  # noqa: RUF006
        await clock.wait_sleepers(1)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(register())
    finally:
        loop.close()

    clock.advance(60)
    assert clock.sleepers() == 0


def test_tickable_is_a_clock() -> None:
    assert isinstance(Tickable(T0), Clock)


async def test_provider_gives_real_clock() -> None:
    container = make_async_container(ClockProvider())

    clock = await container.get(Clock)

    assert isinstance(clock, Real)
    await container.close()


@pytest.mark.parametrize("clock", [Real(), Fixed(T0), Tickable(T0)])
def test_since_is_seconds(clock: Clock) -> None:
    assert isinstance(clock.since(clock.now()), float)
