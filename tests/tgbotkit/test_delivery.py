"""Delivery: локализация фона, свой request-id и plain-фолбэк на отбитой разметке."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from pylibinfra._internal import requestid
from pylibinfra.testkit.logtest import Capture
from pylibinfra.tgbotkit import Delivery, FluentTranslator, Translator


class FakeBot:
    """Дубль `Bot`: пишет вызовы и роняет первый заданной ошибкой."""

    def __init__(self, fail_first_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.request_ids: list[str] = []
        self._fail_first_with = fail_first_with

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        self.request_ids.append(requestid.current())
        if self._fail_first_with is not None and len(self.calls) == 1:
            raise self._fail_first_with


class FakeBgManager:
    def __init__(self) -> None:
        self.started: list[tuple[Any, ...]] = []

    async def start(self, state: Any, data: Any = None, mode: Any = None) -> None:
        self.started.append((state, data, mode, requestid.current()))


class FakeBgFactory:
    def __init__(self) -> None:
        self.manager = FakeBgManager()
        self.kwargs: dict[str, Any] | None = None

    def bg(self, **kwargs: Any) -> FakeBgManager:
        self.kwargs = kwargs
        return self.manager


def _delivery(
    translator: Translator | None = None,
    default_locale: str = "en",
    bot: Any = None,
    bg: Any = None,
    log: Any = None,
) -> Delivery:
    return Delivery(bg, bot, log, translator, default_locale)  # type: ignore[arg-type]


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message=message)


def test_localize_returns_key_without_translator() -> None:
    assert _delivery().localize("ru", "order-paid", {"id": "o1"}) == "order-paid"


def test_localize_renders_via_translator(tmp_path: Path) -> None:
    (tmp_path / "ru").mkdir()
    (tmp_path / "ru" / "main.ftl").write_text("hi = Привет { $name }\n", encoding="utf-8")
    translator = FluentTranslator("ru", str(tmp_path), ["main.ftl"])
    assert "Боб" in _delivery(translator, "ru").localize("ru", "hi", {"name": "Боб"})


def test_localize_falls_back_to_default_locale(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "main.ftl").write_text("hi = Hello\n", encoding="utf-8")
    translator = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    assert "Hello" in _delivery(translator, "en").localize(None, "hi", None)


async def test_send_message_passes_through_on_success() -> None:
    bot = FakeBot()
    await _delivery(bot=bot).send_message(chat_id=7, text="ok")
    assert bot.calls == [{"chat_id": 7, "text": "ok"}]


async def test_send_message_runs_under_its_own_request_id() -> None:
    bot = FakeBot()
    await _delivery(bot=bot).send_message(chat_id=7, text="ok")
    assert len(bot.request_ids[0]) == 36
    assert requestid.current() == ""


async def test_send_message_falls_back_to_plain_on_markup_error() -> None:
    log = Capture()
    bot = FakeBot(_bad_request("Bad Request: can't parse entities: Character '.' is reserved"))
    await _delivery(bot=bot, log=log).send_message(chat_id=7, text="v1.2 down")
    assert len(bot.calls) == 2
    assert bot.calls[1]["text"] == "v1.2 down"
    assert bot.calls[1]["parse_mode"] is None
    assert log.has("tgbotkit: markup rejected by telegram, resending as plain text", level="warn")


async def test_plain_fallback_overrides_explicit_parse_mode() -> None:
    bot = FakeBot(_bad_request("Bad Request: can't parse message text: unmatched end tag"))
    await _delivery(bot=bot).send_message(chat_id=7, text="<b>x", parse_mode="HTML")
    assert bot.calls[1]["parse_mode"] is None


async def test_send_message_reraises_delivery_errors() -> None:
    bot = FakeBot(_bad_request("Bad Request: chat not found"))
    with pytest.raises(TelegramBadRequest):
        await _delivery(bot=bot).send_message(chat_id=7, text="ok")
    assert len(bot.calls) == 1
    assert requestid.current() == ""


async def test_start_dialog_uses_background_manager() -> None:
    factory = FakeBgFactory()
    await _delivery(bot="bot", bg=factory).start_dialog(
        user_id=1, chat_id=2, state="state", data={"k": "v"}
    )
    assert factory.kwargs == {"bot": "bot", "user_id": 1, "chat_id": 2, "load": True}
    state, data, _, rid = factory.manager.started[0]
    assert (state, data) == ("state", {"k": "v"})
    assert len(rid) == 36
