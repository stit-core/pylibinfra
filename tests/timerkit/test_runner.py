import asyncio

from pylibinfra.logkit import nop
from pylibinfra.timerkit import PeriodicJob, Runner


async def test_job_called_multiple_times_then_cancelled() -> None:
    count = 0

    async def handler() -> None:
        nonlocal count
        count += 1

    runner = Runner([PeriodicJob(name="tick", interval=0.01, handler=handler)], nop())
    runner.start()
    await asyncio.sleep(0.1)
    await runner.stop()

    assert count >= 3
    snapshot = count
    await asyncio.sleep(0.05)
    # После stop() джоба не должна тикать дальше.
    assert count == snapshot


async def test_exception_in_one_job_does_not_kill_runner() -> None:
    good_count = 0
    bad_count = 0

    async def good() -> None:
        nonlocal good_count
        good_count += 1

    async def bad() -> None:
        nonlocal bad_count
        bad_count += 1
        raise RuntimeError("boom")

    runner = Runner(
        [
            PeriodicJob(name="good", interval=0.01, handler=good),
            PeriodicJob(name="bad", interval=0.01, handler=bad),
        ],
        nop(),
    )
    runner.start()
    await asyncio.sleep(0.1)
    await runner.stop()

    # Падающая джоба продолжает тикать, исправная не страдает.
    assert bad_count >= 3
    assert good_count >= 3


async def test_stop_is_idempotent_and_cancels_pending() -> None:
    started = asyncio.Event()

    async def handler() -> None:
        started.set()
        await asyncio.sleep(10)

    runner = Runner([PeriodicJob(name="slow", interval=0.01, handler=handler)], nop())
    runner.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await runner.stop()
    await runner.stop()
