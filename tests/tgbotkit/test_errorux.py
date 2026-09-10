"""ErrorUX: устаревшая кнопка гасит «часики», прочее получает нейтральный ответ."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState

from pylibinfra.tgbotkit import default_error_ux, register_error_handlers
from pylibinfra.tgbotkit.errorux import FAILURE_TEXT, STALE_TEXT, DefaultErrorUX


class FakeCallback:
    def __init__(self) -> None:
        self.answered: str | None = None

    async def answer(self, text: str, show_alert: bool = False) -> None:
        self.answered = text


class FakeMessage:
    def __init__(self) -> None:
        self.answered: list[str] = []

    async def answer(self, text: str) -> None:
        self.answered.append(text)


def _event(callback: Any = None, message: Any = None) -> Any:
    return SimpleNamespace(update=SimpleNamespace(callback_query=callback, message=message))


@pytest.mark.parametrize("method", ["on_unknown_intent", "on_unknown_state"])
async def test_stale_dialog_stops_callback_spinner(method: str) -> None:
    callback = FakeCallback()
    await getattr(DefaultErrorUX(), method)(_event(callback=callback), None)
    assert callback.answered == STALE_TEXT


async def test_stale_dialog_without_callback_is_silent() -> None:
    await DefaultErrorUX().on_unknown_intent(_event(), None)


async def test_unhandled_answers_callback() -> None:
    callback = FakeCallback()
    await DefaultErrorUX().on_unhandled(_event(callback=callback), None)
    assert callback.answered == FAILURE_TEXT


async def test_unhandled_answers_message() -> None:
    message = FakeMessage()
    await DefaultErrorUX().on_unhandled(_event(message=message), None)
    assert message.answered == [FAILURE_TEXT]


async def test_unhandled_without_channel_is_silent() -> None:
    await DefaultErrorUX().on_unhandled(_event(), None)


def test_register_error_handlers_installs_stale_filter() -> None:
    dp = Dispatcher()
    register_error_handlers(dp, default_error_ux())
    assert len(dp.errors.handlers) == 1


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (UnknownIntent("stale"), "intent"),
        (OutdatedIntent("s1", "stale"), "intent"),
        (UnknownState("gone"), "state"),
    ],
)
async def test_stale_handler_routes_by_exception(exc: Exception, expected: str) -> None:
    seen: list[str] = []

    class _UX:
        async def on_unknown_intent(self, event: Any, bot: Any) -> None:
            seen.append("intent")

        async def on_unknown_state(self, event: Any, bot: Any) -> None:
            seen.append("state")

        async def on_unhandled(self, event: Any, bot: Any) -> None:
            seen.append("unhandled")

    dp = Dispatcher()
    register_error_handlers(dp, _UX())
    handler = dp.errors.handlers[0].callback
    assert await handler(SimpleNamespace(exception=exc), bot=None) is True
    assert seen == [expected]
