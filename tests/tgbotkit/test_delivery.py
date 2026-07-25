"""Delivery: locale-канал фоновой доставки (вне I18nMiddleware) + plain-фолбэк
на отбитой Telegram-разметке."""

from pathlib import Path
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from pylibinfra.tgbotkit import FluentTranslator, Translator
from pylibinfra.tgbotkit.notifications.delivery import Delivery


def _delivery(translator: Translator | None = None, default_locale: str = "en") -> Delivery:
    # bg_factory/bot не нужны localize — он трогает только translator
    return Delivery(None, None, None, translator, default_locale)  # type: ignore[arg-type]


def test_localize_returns_key_without_translator() -> None:
    text = _delivery().localize("ru", "notifications-order-paid", {"order_id": "o1"})
    assert text == "notifications-order-paid"


def test_localize_renders_via_translator(tmp_path: Path) -> None:
    (tmp_path / "ru").mkdir()
    (tmp_path / "ru" / "main.ftl").write_text("hi = Привет { $name }\n", encoding="utf-8")
    tr = FluentTranslator("ru", str(tmp_path), ["main.ftl"])
    assert "Боб" in _delivery(tr, "ru").localize("ru", "hi", {"name": "Боб"})


def test_localize_falls_back_to_default_locale(tmp_path: Path) -> None:
    (tmp_path / "en").mkdir()
    (tmp_path / "en" / "main.ftl").write_text("hi = Hello\n", encoding="utf-8")
    tr = FluentTranslator("en", str(tmp_path), ["main.ftl"])
    # locale=None → default_locale
    assert "Hello" in _delivery(tr, "en").localize(None, "hi", None)


class _FakeBot:
    """Минимальный дубль Bot: пишет вызовы и роняет первый заданной ошибкой."""

    def __init__(self, fail_first_with: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail_first_with = fail_first_with

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})
        if self._fail_first_with is not None and len(self.calls) == 1:
            raise self._fail_first_with


def _bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=SendMessage(chat_id=1, text="x"), message=message)


async def test_send_message_passes_through_on_success() -> None:
    bot = _FakeBot()
    await Delivery(None, bot, None).send_message(chat_id=7, text="ok")  # type: ignore[arg-type]
    assert bot.calls == [{"chat_id": 7, "text": "ok"}]


async def test_send_message_falls_back_to_plain_on_markup_error() -> None:
    bot = _FakeBot(_bad_request("Bad Request: can't parse entities: Character '.' is reserved"))
    await Delivery(None, bot, None).send_message(chat_id=7, text="v1.2 down")  # type: ignore[arg-type]
    # текст доставлен целиком, второй попыткой и без разметки
    assert len(bot.calls) == 2
    assert bot.calls[1]["text"] == "v1.2 down"
    assert bot.calls[1]["parse_mode"] is None


async def test_plain_fallback_overrides_explicit_parse_mode() -> None:
    bot = _FakeBot(_bad_request("Bad Request: can't parse entities: unmatched end tag"))
    await Delivery(None, bot, None).send_message(  # type: ignore[arg-type]
        chat_id=7, text="<b>x", parse_mode="HTML"
    )
    assert bot.calls[1]["parse_mode"] is None


async def test_send_message_reraises_delivery_errors() -> None:
    bot = _FakeBot(_bad_request("Bad Request: chat not found"))
    with pytest.raises(TelegramBadRequest):
        await Delivery(None, bot, None).send_message(chat_id=7, text="ok")  # type: ignore[arg-type]
    # без ретрая: nak → redelivery по контракту at-least-once
    assert len(bot.calls) == 1
