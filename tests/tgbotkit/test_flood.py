"""OutgoingThrottleMiddleware: rate-limit, порядок в чате и ретрай 429 на управляемых часах."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramRetryAfter

from pylibinfra.clockkit import Tickable
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit.middleware.flood import OutgoingThrottleMiddleware

from .conftest import cfg

START = datetime(2026, 1, 1, tzinfo=UTC)


def _mw(
    client: MemoryRedis, clock: Tickable, log: Capture | None = None, **overrides: Any
) -> OutgoingThrottleMiddleware:
    return OutgoingThrottleMiddleware(client, cfg(**overrides), log, clock)  # type: ignore[arg-type]


def _clock() -> Tickable:
    return Tickable(START)


async def _ok(bot: Any, method: Any) -> str:
    return "ok"


async def test_first_call_passes_without_waiting(redis_client: MemoryRedis) -> None:
    clock = _clock()
    mw = _mw(redis_client, clock)  # оба гейта включены дефолтами конфига

    assert await mw(_ok, None, SimpleNamespace(chat_id=7)) == "ok"  # type: ignore[arg-type]
    assert clock.sleepers() == 0


async def test_second_call_waits_for_the_rate_slot(redis_client: MemoryRedis) -> None:
    clock = _clock()
    mw = _mw(redis_client, clock, rate_global=0.0, rate_per_chat=1.0)
    method = SimpleNamespace(chat_id=7)

    await mw(_ok, None, method)  # type: ignore[arg-type]
    second = asyncio.create_task(mw(_ok, None, method))  # type: ignore[arg-type]
    await clock.wait_sleepers(1)
    assert not second.done()

    clock.advance(1.0)
    assert await second == "ok"


async def test_wait_beyond_cap_sends_anyway(redis_client: MemoryRedis) -> None:
    log = Capture()
    clock = _clock()
    mw = _mw(redis_client, clock, log, rate_global=0.0, rate_per_chat=1.0, rate_max_wait=0.5)
    method = SimpleNamespace(chat_id=7)

    await mw(_ok, None, method)  # type: ignore[arg-type]
    assert await mw(_ok, None, method) == "ok"  # type: ignore[arg-type]
    assert clock.sleepers() == 0
    assert log.has("tgbotkit: rate limit wait capped, sending anyway", level="warn")


async def test_disabled_rate_skips_the_gate(redis_client: MemoryRedis) -> None:
    clock = _clock()
    mw = _mw(redis_client, clock, rate_global=0.0, rate_per_chat=0.0)
    method = SimpleNamespace(chat_id=7)

    for _ in range(5):
        assert await mw(_ok, None, method) == "ok"  # type: ignore[arg-type]
    assert redis_client.store == {}


async def test_method_without_chat_skips_per_chat_gate(redis_client: MemoryRedis) -> None:
    mw = _mw(redis_client, _clock(), rate_global=0.0)

    assert await mw(_ok, None, SimpleNamespace()) == "ok"  # type: ignore[arg-type]
    assert redis_client.store == {}


async def test_per_chat_lock_serializes_same_chat(redis_client: MemoryRedis) -> None:
    mw = _mw(redis_client, _clock(), rate_global=0.0, rate_per_chat=0.0)
    events: list[str] = []

    async def make_request(bot: Any, method: Any) -> str:
        events.append(f"start-{method.tag}")
        await asyncio.sleep(0)
        events.append(f"end-{method.tag}")
        return "ok"

    await asyncio.gather(
        mw(make_request, None, SimpleNamespace(chat_id=5, tag="a")),  # type: ignore[arg-type]
        mw(make_request, None, SimpleNamespace(chat_id=5, tag="b")),  # type: ignore[arg-type]
    )
    assert events in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )


async def test_retry_after_backs_off_then_succeeds(redis_client: MemoryRedis) -> None:
    log = Capture()
    clock = _clock()
    mw = _mw(redis_client, clock, log, rate_global=0.0, rate_per_chat=0.0)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise TelegramRetryAfter(method, "flood", retry_after=5)
        return "ok"

    task = asyncio.create_task(mw(make_request, None, SimpleNamespace(chat_id=10)))  # type: ignore[arg-type]
    await clock.wait_sleepers(1)
    clock.advance(5.0)

    assert await task == "ok"
    assert len(calls) == 2
    assert log.has("tgbotkit: flood control hit, backing off", level="warn")


async def test_retry_after_reraises_after_last_attempt(redis_client: MemoryRedis) -> None:
    mw = _mw(redis_client, _clock(), retry_after_attempts=2, rate_global=0.0, rate_per_chat=0.0)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        raise TelegramRetryAfter(method, "flood", retry_after=0)

    with pytest.raises(TelegramRetryAfter):
        await mw(make_request, None, SimpleNamespace(chat_id=10))  # type: ignore[arg-type]
    assert len(calls) == 2


async def test_retry_after_over_the_cap_is_not_slept_off(redis_client: MemoryRedis) -> None:
    log = Capture()
    clock = _clock()
    mw = _mw(redis_client, clock, log, rate_global=0.0, rate_per_chat=0.0, rate_max_wait=30.0)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        raise TelegramRetryAfter(method, "flood", retry_after=3600)

    with pytest.raises(TelegramRetryAfter):
        await mw(make_request, None, SimpleNamespace(chat_id=10))  # type: ignore[arg-type]

    # Час флуд-бана нельзя отсиживать под per-chat локом и дедлайном остановки.
    assert len(calls) == 1
    assert clock.sleepers() == 0
    entry = log.find("tgbotkit: flood control retry_after over the cap, giving up", level="warn")
    assert entry is not None
    assert entry.str("cap") == "30.0"


async def test_rate_limit_fails_open_when_redis_is_down(
    read_metric: Callable[..., float],
) -> None:
    log = Capture()
    clock = _clock()
    mw = _mw(MemoryRedis(fail=True), clock, log)
    method = SimpleNamespace(chat_id=7)

    assert await mw(_ok, None, method) == "ok"  # type: ignore[arg-type]
    assert clock.sleepers() == 0
    assert log.has("tgbotkit: rate limit unavailable, sending without a slot", level="warn")
    assert read_metric("tg_redis_errors_total", {"op": "rate"}) >= 1


async def test_single_attempt_does_not_retry(redis_client: MemoryRedis) -> None:
    mw = _mw(redis_client, _clock(), retry_after_attempts=1, rate_global=0.0, rate_per_chat=0.0)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        raise TelegramRetryAfter(method, "flood", retry_after=0)

    with pytest.raises(TelegramRetryAfter):
        await mw(make_request, None, SimpleNamespace(chat_id=10))  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_lock_table_drops_idle_chats(redis_client: MemoryRedis) -> None:
    from pylibinfra.tgbotkit.middleware import flood

    mw = _mw(redis_client, _clock(), rate_global=0.0, rate_per_chat=0.0)
    for chat in range(flood._LOCKS_SOFT_CAP):
        mw._lock_for(chat)
    assert len(mw._locks) == flood._LOCKS_SOFT_CAP

    mw._lock_for(flood._LOCKS_SOFT_CAP)
    assert len(mw._locks) == 1  # свободные локи вычищены, остался только новый
