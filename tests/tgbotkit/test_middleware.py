from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from pylibinfra.httpclient import request_id
from pylibinfra.tgbotkit.metrics import new_instruments
from pylibinfra.tgbotkit.middleware.dedup import DedupMiddleware
from pylibinfra.tgbotkit.middleware.request_id import RequestIdMiddleware
from pylibinfra.tgbotkit.middleware.throttle import ThrottleMiddleware

from .conftest import FakeRedis


def _counter() -> tuple[Callable[..., Any], list[Any]]:
    calls: list[Any] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        calls.append(event)
        return "handled"

    return handler, calls


async def test_dedup_drops_duplicate(
    fake_redis: FakeRedis, read_metric: Callable[[str], float]
) -> None:
    instr = new_instruments()
    mw = DedupMiddleware(fake_redis, 600, instr)  # type: ignore[arg-type]
    handler, calls = _counter()
    update = SimpleNamespace(update_id=42, event_type="message")

    assert await mw(handler, update, {}) == "handled"
    assert await mw(handler, update, {}) is None  # дубль дропнут
    assert len(calls) == 1
    assert read_metric("tg_updates_deduped_total") == 1


async def test_dedup_skips_background_events(fake_redis: FakeRedis) -> None:
    instr = new_instruments()
    mw = DedupMiddleware(fake_redis, 600, instr)  # type: ignore[arg-type]
    handler, calls = _counter()
    bg = SimpleNamespace(update_id=0, event_type="aiogd_update")

    await mw(handler, bg, {})
    await mw(handler, bg, {})  # update_id=0 не дедупится
    assert len(calls) == 2


async def test_throttle_limits_per_user(fake_redis: FakeRedis) -> None:
    mw = ThrottleMiddleware(fake_redis, rps=2)  # type: ignore[arg-type]
    handler, calls = _counter()
    update = SimpleNamespace(update_id=1)
    data = {"event_from_user": SimpleNamespace(id=7)}

    assert await mw(handler, update, data) == "handled"
    assert await mw(handler, update, data) == "handled"
    assert await mw(handler, update, data) is None  # сверх rps=2
    assert len(calls) == 2


async def test_request_id_set_during_handler_and_reset_after() -> None:
    mw = RequestIdMiddleware()
    seen: list[str] = []

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.append(request_id.get())

    update = SimpleNamespace(update_id=99, event_type="message")
    await mw(handler, update, {"event_from_user": SimpleNamespace(id=1)})
    assert seen == ["tg-99"]
    assert request_id.get() == ""  # сброшен после апдейта
