from types import SimpleNamespace
from typing import Any

from aiogram import Dispatcher

from pylibinfra.tgbotkit import default_error_ux, register_error_handlers
from pylibinfra.tgbotkit.errorux import DefaultErrorUX


class FakeCallback:
    def __init__(self) -> None:
        self.answered: str | None = None

    async def answer(self, text: str, show_alert: bool = False) -> None:
        self.answered = text


def _error_event(callback: Any = None, message: Any = None) -> Any:
    return SimpleNamespace(update=SimpleNamespace(callback_query=callback, message=message))


async def test_default_ux_stops_callback_spinner_on_unknown_intent() -> None:
    ux = DefaultErrorUX()
    cb = FakeCallback()
    await ux.on_unknown_intent(_error_event(callback=cb), bot=None)  # type: ignore[arg-type]
    assert cb.answered is not None


async def test_default_ux_unhandled_answers_message() -> None:
    ux = DefaultErrorUX()
    answered: list[str] = []

    async def answer(text: str) -> None:
        answered.append(text)

    msg = SimpleNamespace(answer=answer)
    await ux.on_unhandled(_error_event(message=msg), bot=None)  # type: ignore[arg-type]
    assert len(answered) == 1


def test_register_error_handlers_registers_on_dp_errors() -> None:
    dp = Dispatcher()
    register_error_handlers(dp, default_error_ux())
    assert len(dp.errors.handlers) >= 1
