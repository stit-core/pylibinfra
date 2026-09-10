import asyncio

import pytest

from pylibinfra import healthkit


async def test_run_calls_check() -> None:
    calls: list[int] = []

    async def check() -> None:
        calls.append(1)

    await healthkit.new("pgkit", check).run()
    assert calls == [1]


async def test_run_propagates_error() -> None:
    async def check() -> None:
        raise RuntimeError("pgkit: ping: boom")

    with pytest.raises(RuntimeError, match="pgkit: ping"):
        await healthkit.Probe("pgkit", check).run()


async def test_with_timeout_keeps_name_and_times_out() -> None:
    async def slow() -> None:
        await asyncio.sleep(10)

    p = healthkit.with_timeout(healthkit.new("natskit", slow), 0.01)
    assert p.name == "natskit"
    with pytest.raises(healthkit.ProbeTimeoutError, match="'natskit' timed out"):
        await p.run()


async def test_with_timeout_returns_even_if_the_check_ignores_cancellation() -> None:
    # Проба, глотающая CancelledError, не должна держать /ready дольше дедлайна.
    released = asyncio.Event()
    finished = asyncio.Event()

    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await released.wait()
            finished.set()

    p = healthkit.with_timeout(healthkit.new("pgkit", stubborn), 0.01)
    with pytest.raises(healthkit.ProbeTimeoutError):
        await p.run()

    assert not finished.is_set()
    released.set()
    await asyncio.wait_for(finished.wait(), timeout=1)


async def test_with_timeout_cancels_the_check_when_run_is_cancelled() -> None:
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(healthkit.with_timeout(healthkit.new("x", slow), 5).run())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(cancelled.wait(), timeout=1)


async def test_with_timeout_nonpositive_is_identity() -> None:
    async def ok() -> None:
        return None

    p = healthkit.new("x", ok)
    assert healthkit.with_timeout(p, 0) is p
    assert healthkit.with_timeout(p, -1) is p


async def test_with_timeout_passes_fast_check() -> None:
    async def ok() -> None:
        return None

    await healthkit.with_timeout(healthkit.new("x", ok), 1.0).run()


def test_probes_is_tuple_alias() -> None:
    probes: healthkit.Probes = ()
    assert probes == ()
