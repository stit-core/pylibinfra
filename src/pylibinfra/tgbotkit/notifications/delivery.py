"""Delivery — безопасная доставка в Telegram вне апдейта (фон из JetStream)."""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State
from aiogram_dialog import BgManagerFactory, StartMode

from pylibinfra._internal import requestid
from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.i18n import Translator


class Delivery:
    """Единственная безопасная обёртка над `BgManager` для доставки вне апдейта.

    Каждая доставка идёт под своим request-id: фон не проходит цепочку `dp.update`,
    поэтому иначе записи логов слипались бы в один контекст. Сбой доставки
    пробрасывается вызывающему — обработчику подписки, чтобы natskit ответил `NAK`
    и сообщение переотправилось. Маппинг «пользователь бэкенда → chat_id» продуктовый
    и остаётся в сервисе.
    """

    def __init__(
        self,
        bg_factory: BgManagerFactory,
        bot: Bot,
        log: Logger | None = None,
        translator: Translator | None = None,
        default_locale: str = "en",
    ) -> None:
        self._bg = bg_factory
        self._bot = bot
        self._log = log if log is not None else nop()
        self._translator = translator
        self._default_locale = default_locale

    def localize(self, locale: str | None, key: str, args: dict[str, Any] | None = None) -> str:
        """Рендерит ключ в `locale` (иначе в дефолтной); без настроенного i18n — сам ключ.

        Какую локаль брать — решает вызывающий: это продуктовое знание, у фонового
        события нет `from_user`.
        """
        if self._translator is None:
            return key
        return self._translator.for_locale(locale or self._default_locale)(key, args)

    async def start_dialog(
        self,
        *,
        user_id: int,
        chat_id: int,
        state: State,
        data: dict[str, Any] | None = None,
        mode: StartMode = StartMode.RESET_STACK,
        load: bool = True,
    ) -> None:
        """Открывает диалог пользователю из фона; `load=True` подтягивает реальные Chat/User."""
        token = requestid.bind(requestid.new())
        try:
            bg = self._bg.bg(bot=self._bot, user_id=user_id, chat_id=chat_id, load=load)
            await bg.start(state, data=data, mode=mode)
        finally:
            requestid.unbind(token)

    async def send_message(self, *, chat_id: int, text: str, **kwargs: Any) -> None:
        """Шлёт текст в `parse_mode` бота, повторяя без разметки, если Telegram её отверг.

        Битую разметку переотправка не вылечит: те же `400` исчерпают `max_deliver`, и
        сообщение уйдёт в DLQ, то есть пропадёт. Для алерта содержимое важнее оформления.
        Прочие `400` (нет чата, бот заблокирован) пробрасываются: они про доставку.
        """
        token = requestid.bind(requestid.new())
        try:
            await self._send(chat_id, text, kwargs)
        finally:
            requestid.unbind(token)

    async def _send(self, chat_id: int, text: str, kwargs: dict[str, Any]) -> None:
        try:
            await self._bot.send_message(chat_id, text, **kwargs)
        except TelegramBadRequest as exc:
            if not is_markup_error(exc):
                raise
            self._log.warn(
                "tgbotkit: markup rejected by telegram, resending as plain text",
                chat_id=chat_id,
                err=exc.message,
            )
            # Явный None (а не Default) отменяет parse_mode бота на этот вызов.
            await self._bot.send_message(chat_id, text, **{**kwargs, "parse_mode": None})


def is_markup_error(exc: TelegramBadRequest) -> bool:
    """Отличает `400` про разметку текста от `400` про доставку.

    Весь класс (незакрытая сущность, зарезервированный символ, чужой тег) Telegram
    отдаёт формулировкой `can't parse entities`; исторический вариант —
    `can't parse message text`. Ловим общий префикс.
    """
    return "can't parse" in exc.message.lower()
