"""Цепочка `dp.update`: request-id, dedup, throttle, recovery, observability."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.types import ErrorEvent
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from pylibinfra import logkit
from pylibinfra._internal import requestid
from pylibinfra.logkit import nop
from pylibinfra.testkit.bottest import MemoryRedis
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit import new_instruments
from pylibinfra.tgbotkit.middleware import (
    DedupMiddleware,
    ObservabilityMiddleware,
    RecoveryMiddleware,
    RequestIdMiddleware,
    ThrottleMiddleware,
    chat_id,
    install_standard_middleware,
    update_id,
    user_id,
)

from .conftest import cfg


def _handler() -> tuple[Callable[..., Any], list[Any]]:
    calls: list[Any] = []

    async def handler(event: Any, data: dict[str, Any]) -> str:
        calls.append(event)
        return "handled"

    return handler, calls


def _update(update_id: int = 42, event_type: str = "message") -> Any:
    return SimpleNamespace(update_id=update_id, event_type=event_type)


async def test_dedup_drops_duplicate(
    redis_client: MemoryRedis, read_metric: Callable[..., float]
) -> None:
    mw = DedupMiddleware(redis_client, 600, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()

    assert await mw(handler, _update(), {}) == "handled"
    assert await mw(handler, _update(), {}) is None
    assert len(calls) == 1
    assert read_metric("tg_updates_deduped_total", {"type": "message"}) == 1


async def test_dedup_keys_by_bot(redis_client: MemoryRedis) -> None:
    mw = DedupMiddleware(redis_client, 600, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()

    await mw(handler, _update(), {"bot": SimpleNamespace(id=1)})
    await mw(handler, _update(), {"bot": SimpleNamespace(id=2)})
    assert len(calls) == 2


async def test_dedup_fails_open_when_redis_is_down(read_metric: Callable[..., float]) -> None:
    log = Capture()
    mw = DedupMiddleware(MemoryRedis(fail=True), 600, new_instruments(), log)  # type: ignore[arg-type]
    handler, calls = _handler()

    assert await mw(handler, _update(), {}) == "handled"
    assert len(calls) == 1
    assert log.has("tgbotkit: dedup unavailable, update passed through", level="warn")
    assert read_metric("tg_redis_errors_total", {"op": "dedup"}) == 1


def test_dedup_ttl_rounds_up_to_a_whole_second() -> None:
    # Усечение укоротило бы окно дедупа — это лишний дубль на границе.
    assert DedupMiddleware(MemoryRedis(), 0.2, new_instruments())._ttl == 1  # type: ignore[arg-type]
    assert DedupMiddleware(MemoryRedis(), 600.5, new_instruments())._ttl == 601  # type: ignore[arg-type]


async def test_dedup_skips_background_events(redis_client: MemoryRedis) -> None:
    mw = DedupMiddleware(redis_client, 600, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()
    background = _update(update_id=0, event_type="aiogd_update")

    await mw(handler, background, {})
    await mw(handler, background, {})
    assert len(calls) == 2


async def test_throttle_limits_per_user(
    redis_client: MemoryRedis, read_metric: Callable[..., float]
) -> None:
    mw = ThrottleMiddleware(redis_client, 2, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()
    data = {"event_from_user": SimpleNamespace(id=7)}

    assert await mw(handler, _update(update_id=1), data) == "handled"
    assert await mw(handler, _update(update_id=2), data) == "handled"
    assert await mw(handler, _update(update_id=3), data) is None
    assert len(calls) == 2
    assert read_metric("tg_updates_throttled_total", {"type": "message"}) == 1


async def test_throttle_skips_background_and_userless_updates(redis_client: MemoryRedis) -> None:
    mw = ThrottleMiddleware(redis_client, 1, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()
    data = {"event_from_user": SimpleNamespace(id=7)}

    for _ in range(3):
        await mw(handler, _update(update_id=0), data)
    for _ in range(3):
        await mw(handler, _update(update_id=5), {})
    assert len(calls) == 6


async def test_throttle_fails_open_when_redis_is_down(read_metric: Callable[..., float]) -> None:
    log = Capture()
    mw = ThrottleMiddleware(MemoryRedis(fail=True), 1, new_instruments(), log)  # type: ignore[arg-type]
    handler, calls = _handler()

    for _ in range(3):
        assert await mw(handler, _update(), {"event_from_user": SimpleNamespace(id=7)}) == "handled"
    assert len(calls) == 3
    assert log.has("tgbotkit: throttle unavailable, update passed through", level="warn")
    assert read_metric("tg_redis_errors_total", {"op": "throttle"}) == 3


async def test_throttle_keys_by_bot(redis_client: MemoryRedis) -> None:
    mw = ThrottleMiddleware(redis_client, 1, new_instruments())  # type: ignore[arg-type]
    handler, calls = _handler()
    user = SimpleNamespace(id=7)

    await mw(handler, _update(update_id=1), {"event_from_user": user, "bot": SimpleNamespace(id=1)})
    await mw(handler, _update(update_id=2), {"event_from_user": user, "bot": SimpleNamespace(id=2)})
    # Два бота на одном Redis не делят лимит одного пользователя.
    assert len(calls) == 2


class _RecordingUX:
    def __init__(self, fail: bool = False) -> None:
        self.unhandled: list[ErrorEvent] = []
        self._fail = fail

    async def on_unknown_intent(self, event: ErrorEvent, bot: Any) -> None: ...

    async def on_unknown_state(self, event: ErrorEvent, bot: Any) -> None: ...

    async def on_unhandled(self, event: ErrorEvent, bot: Any) -> None:
        self.unhandled.append(event)
        if self._fail:
            raise RuntimeError("ux is broken too")


@pytest.mark.parametrize(
    "exc", [UnknownIntent("stale"), OutdatedIntent("s1", "stale"), UnknownState("gone")]
)
async def test_recovery_reraises_stale_dialog_errors(exc: Exception) -> None:
    ux = _RecordingUX()
    mw = RecoveryMiddleware(ux, new_instruments(), nop())

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise exc

    with pytest.raises(type(exc)):
        await mw(handler, _update(event_type="callback_query"), {})
    assert ux.unhandled == []


async def test_recovery_swallows_generic_exception(read_metric: Callable[..., float]) -> None:
    ux = _RecordingUX()
    mw = RecoveryMiddleware(ux, new_instruments(), nop())

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    assert await mw(handler, _update(), {}) is None
    assert read_metric("tg_update_panics_total", {"type": "message"}) == 1


async def test_recovery_survives_broken_error_ux() -> None:
    from aiogram.types import Update

    ux = _RecordingUX(fail=True)
    mw = RecoveryMiddleware(ux, new_instruments(), nop())

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    update = Update(update_id=1)
    assert await mw(handler, update, {"bot": SimpleNamespace(id=1)}) is None
    assert len(ux.unhandled) == 1


async def test_observability_records_ok(
    read_metric: Callable[..., float], span_exporter: InMemorySpanExporter
) -> None:
    mw = ObservabilityMiddleware(new_instruments())
    handler, _ = _handler()

    assert await mw(handler, _update(), {}) == "handled"
    assert read_metric("tg_updates_total", {"type": "message", "result": "ok"}) == 1
    assert read_metric("tg_update_duration_seconds", {"type": "message"}) == 1
    spans = span_exporter.get_finished_spans()
    assert [s.name for s in spans] == ["tg.update"]
    assert spans[0].attributes is not None
    assert spans[0].attributes["tg.update_type"] == "message"


async def test_observability_marks_error_and_reraises(
    read_metric: Callable[..., float], span_exporter: InMemorySpanExporter
) -> None:
    mw = ObservabilityMiddleware(new_instruments())

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await mw(handler, _update(), {})
    assert read_metric("tg_updates_total", {"type": "message", "result": "error"}) == 1
    assert span_exporter.get_finished_spans()[0].status.is_ok is False


async def test_observability_marks_a_cancelled_update_as_error(
    read_metric: Callable[..., float],
) -> None:
    mw = ObservabilityMiddleware(new_instruments())

    async def handler(event: Any, data: dict[str, Any]) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await mw(handler, _update(), {})
    # Прерванный на остановке апдейт не обработан: писать его успехом — врать.
    assert read_metric("tg_updates_total", {"type": "message", "result": "error"}) == 1


async def test_unknown_update_type_is_reported_as_unknown(
    read_metric: Callable[..., float],
) -> None:
    from aiogram.types import Update

    mw = ObservabilityMiddleware(new_instruments())
    handler, calls = _handler()
    update = Update.model_validate({"update_id": 1, "brand_new_event": {"x": 1}})

    assert await mw(handler, update, {}) == "handled"
    assert len(calls) == 1
    assert read_metric("tg_updates_total", {"type": "unknown", "result": "ok"}) == 1


async def test_dispatcher_survives_an_unknown_update_type(
    redis_client: MemoryRedis, read_metric: Callable[..., float]
) -> None:
    from aiogram import Bot
    from aiogram.types import Update

    from pylibinfra.tgbotkit import default_error_ux

    dp = Dispatcher()
    install_standard_middleware(
        dp, cfg(), nop(), new_instruments(), redis_client, default_error_ux()
    )
    bot = Bot("123456:AAstub-token-for-tests")
    update = Update.model_validate({"update_id": 11, "brand_new_event": {"x": 1}})
    try:
        with pytest.warns(RuntimeWarning, match="unknown update type"):
            await dp.feed_update(bot, update)
    finally:
        await bot.session.close()

    # Новое поле Bot API не имеет права ронять цепочку и не считается паникой.
    assert read_metric("tg_updates_total", {"type": "unknown", "result": "ok"}) == 1
    assert read_metric("tg_update_panics_total", {"type": "unknown"}) == 0


async def test_request_id_binds_fresh_uuid_and_resets() -> None:
    mw = RequestIdMiddleware()
    seen: list[str] = []

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.append(requestid.current())

    await mw(handler, _update(update_id=99), {"event_from_user": SimpleNamespace(id=1)})
    assert len(seen[0]) == 36  # UUID v7 в каноническом виде
    assert requestid.current() == ""


async def test_request_id_keeps_valid_incoming_id() -> None:
    mw = RequestIdMiddleware()
    seen: list[str] = []

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.append(requestid.current())

    token = requestid.bind("from-webhook")
    try:
        await mw(handler, _update(), {})
    finally:
        requestid.unbind(token)
    assert seen == ["from-webhook"]


async def test_request_id_exposes_update_fields_to_logkit() -> None:
    mw = RequestIdMiddleware()
    seen: list[tuple[Any, ...]] = []

    async def handler(event: Any, data: dict[str, Any]) -> None:
        registered = dict(logkit.attrs.registered())
        seen.append(
            (
                update_id(),
                user_id(),
                chat_id(),
                registered["update_id"](),
                registered["tg_user_id"](),
                registered["chat_id"](),
            )
        )

    data = {"event_from_user": SimpleNamespace(id=7), "event_chat": SimpleNamespace(id=-100)}
    await mw(handler, _update(update_id=99), data)
    assert seen == [(99, 7, -100, 99, 7, -100)]
    assert (update_id(), user_id(), chat_id()) == (0, 0, 0)


async def test_background_event_reports_no_update_fields() -> None:
    mw = RequestIdMiddleware()
    seen: list[Any] = []

    async def handler(event: Any, data: dict[str, Any]) -> None:
        seen.append(dict(logkit.attrs.registered())["update_id"]())

    await mw(handler, _update(update_id=0, event_type="aiogd_update"), {})
    assert seen == [None]


def test_install_standard_middleware_wires_chain(redis_client: MemoryRedis) -> None:
    from pylibinfra.tgbotkit import default_error_ux

    dp = Dispatcher()
    install_standard_middleware(
        dp,
        cfg(throttle_rps=5),
        nop(),
        new_instruments(),
        redis_client,  # type: ignore[arg-type]
        default_error_ux(),
    )
    kit = [
        type(mw).__name__
        for mw in dp.update.outer_middleware._middlewares
        if type(mw).__module__.startswith("pylibinfra.")
    ]
    assert kit == [
        "RequestIdMiddleware",
        "DedupMiddleware",
        "ThrottleMiddleware",
        "RecoveryMiddleware",
        "ObservabilityMiddleware",
    ]


def test_optional_middleware_is_skipped_when_disabled(redis_client: MemoryRedis) -> None:
    from pylibinfra.tgbotkit import default_error_ux

    dp = Dispatcher()
    install_standard_middleware(
        dp,
        cfg(dedup_enabled=False, throttle_rps=0),
        nop(),
        new_instruments(),
        redis_client,  # type: ignore[arg-type]
        default_error_ux(),
    )
    installed = [type(mw).__name__ for mw in dp.update.outer_middleware._middlewares]
    assert "DedupMiddleware" not in installed
    assert "ThrottleMiddleware" not in installed
