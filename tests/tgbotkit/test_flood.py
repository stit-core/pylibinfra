from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramRetryAfter

from pylibinfra.tgbotkit import Config
from pylibinfra.tgbotkit.middleware.flood import OutgoingThrottleMiddleware

from .conftest import FakeRedis


def _mw(fake_redis: FakeRedis, **cfg_over: Any) -> OutgoingThrottleMiddleware:
    cfg = Config(token="123:abc", **cfg_over)
    return OutgoingThrottleMiddleware(fake_redis, cfg)  # type: ignore[arg-type]


async def test_retry_after_retries_then_succeeds(fake_redis: FakeRedis) -> None:
    mw = _mw(fake_redis)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        if len(calls) == 1:
            # retry_after=0 → реального сна нет, цикл повторяет сразу
            raise TelegramRetryAfter(method, "flood", retry_after=0)
        return "ok"

    method = SimpleNamespace(chat_id=10)
    assert await mw(make_request, None, method) == "ok"  # type: ignore[arg-type]
    assert len(calls) == 2  # первый бросил 429, второй прошёл


async def test_retry_after_reraises_after_attempts(fake_redis: FakeRedis) -> None:
    mw = _mw(fake_redis, retry_after_attempts=2)
    calls: list[int] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(1)
        raise TelegramRetryAfter(method, "flood", retry_after=0)

    method = SimpleNamespace(chat_id=10)
    with pytest.raises(TelegramRetryAfter):
        await mw(make_request, None, method)  # type: ignore[arg-type]
    assert len(calls) == 2  # ровно retry_after_attempts попыток, потом проброс


async def test_per_chat_lock_serializes_same_chat(fake_redis: FakeRedis) -> None:
    mw = _mw(fake_redis)
    events: list[str] = []

    async def make_request(bot: Any, method: Any) -> str:
        events.append(f"start-{method.tag}")
        await asyncio.sleep(0)  # точка возможной перестановки, если бы не lock
        events.append(f"end-{method.tag}")
        return "ok"

    m1 = SimpleNamespace(chat_id=5, tag="a")
    m2 = SimpleNamespace(chat_id=5, tag="b")
    await asyncio.gather(
        mw(make_request, None, m1),  # type: ignore[arg-type]
        mw(make_request, None, m2),  # type: ignore[arg-type]
    )
    # lock сериализовал: каждый start сразу закрыт своим end, без перекрытия
    assert events in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )


async def test_rate_limit_passes_when_token_available(fake_redis: FakeRedis) -> None:
    mw = _mw(fake_redis)
    calls: list[Any] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(method)
        return "ok"

    # bucket пуст → wait=0 → запрос проходит без сна
    method = SimpleNamespace(chat_id=7)
    assert await mw(make_request, None, method) == "ok"  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_non_chat_method_skips_per_chat_gate(fake_redis: FakeRedis) -> None:
    mw = _mw(fake_redis)
    calls: list[Any] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(method)
        return "ok"

    method = SimpleNamespace()  # chat_id отсутствует (напр. get_me)
    assert await mw(make_request, None, method) == "ok"  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_disabled_rate_passes_through(fake_redis: FakeRedis) -> None:
    # rate_global=0 → интервал 0 → gate no-op, ни одного eval не нужно
    mw = _mw(fake_redis, rate_global=0.0, rate_per_chat=0.0)
    calls: list[Any] = []

    async def make_request(bot: Any, method: Any) -> str:
        calls.append(method)
        return "ok"

    method = SimpleNamespace(chat_id=3)
    assert await mw(make_request, None, method) == "ok"  # type: ignore[arg-type]
    assert len(calls) == 1


async def test_contended_send_does_not_loop_to_cap(fake_redis: FakeRedis) -> None:
    # Два быстрых send в один чат контендят на gate: второй ждёт ~один interval и
    # идёт. Регрессия loop-бага (повторный GCRA сдвигал TAT → ожидание до 60s cap):
    # под wait_for(5s) баг упёрся бы в cap и дал TimeoutError.
    mw = _mw(fake_redis)

    async def make_request(bot: Any, method: Any) -> str:
        return "ok"

    method = SimpleNamespace(chat_id=7)

    async def two() -> None:
        await mw(make_request, None, method)  # type: ignore[arg-type]
        await mw(make_request, None, method)  # type: ignore[arg-type]

    await asyncio.wait_for(two(), timeout=5.0)
