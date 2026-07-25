"""Delivery — ЕДИНСТВЕННАЯ безопасная обёртка над BgManager для доставки вне
апдейта (NATS→Telegram). Закрывает грабли фона (06 bgmanager): load=True (реальные
Chat/User для AUTO-show-mode), EDIT не гарантирован, изоляция request_id под каждую
доставку (фон вне middleware). Маппинг backend_user_id→(user_id,chat_id) —
продуктовый, остаётся в шаблоне.

Сбой доставки ПРОБРАСЫВАЕТСЯ к звонящему (notifier-handler) ради at-least-once:
исключение доходит до natskit.JsBus._handle_one → msg.nak() → redelivery (там же
лог сбоя на уровне консьюмера). Здесь — только установка/сброс request_id и
plain-фолбэк на битой разметке (см. send_message).
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State
from aiogram_dialog import BgManagerFactory, StartMode

from pylibinfra.httpclient import request_id as _httpclient_request_id
from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.i18n import Translator


class Delivery:
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
        self._log = log or nop()
        self._tr = translator
        self._default_locale = default_locale

    def localize(self, locale: str | None, key: str, args: dict[str, Any] | None = None) -> str:
        """Рендер .ftl-ключа для фоновой доставки (вне I18nMiddleware): в locale, иначе
        default. Без настроенного i18n возвращает key (как I18NFormat в тестах). Какую
        локаль брать (явный выбор/язык юзера) — резолвит звонящий: это продуктовое."""
        if self._tr is None:
            return key
        return self._tr.for_locale(locale or self._default_locale)(key, args)

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
        token = _httpclient_request_id.set(f"tg-notify-{chat_id}")
        try:
            bg = self._bg.bg(bot=self._bot, user_id=user_id, chat_id=chat_id, load=load)
            await bg.start(state, data=data, mode=mode)
        finally:
            _httpclient_request_id.reset(token)

    async def send_message(self, *, chat_id: int, text: str, **kwargs: Any) -> None:
        """Шлёт текст в parse_mode бота, с фолбэком на plain при отбитой разметке.

        Битую разметку (неэкранированный спецсимвол MarkdownV2, чужой тег в HTML)
        redelivery не вылечит: те же 400 съедят max_deliver и сообщение уйдёт в
        term — то есть потеряется молча. Для алертинга содержимое важнее
        оформления, поэтому такой ответ Telegram не пробрасывается, а сообщение
        переотправляется без разметки. Прочие 400 (нет чата, бан) — наверх, как и
        раньше: они про доставку, а не про текст.
        """
        token = _httpclient_request_id.set(f"tg-notify-{chat_id}")
        try:
            try:
                await self._bot.send_message(chat_id, text, **kwargs)
            except TelegramBadRequest as exc:
                if not _is_markup_error(exc):
                    raise
                self._log.warn(
                    "markup rejected by telegram, resending as plain text",
                    chat_id=chat_id,
                    error=exc.message,
                )
                # явный None (а не Default) отменяет parse_mode бота на этот вызов
                await self._bot.send_message(chat_id, text, **{**kwargs, "parse_mode": None})
        finally:
            _httpclient_request_id.reset(token)


def _is_markup_error(exc: TelegramBadRequest) -> bool:
    """Ошибка про разметку текста, а не про доставку.

    Telegram отдаёт весь класс (незакрытая сущность, зарезервированный символ,
    чужой тег) одной формулировкой `can't parse entities`; исторический вариант —
    `can't parse message text`. Общий префикс и ловим.
    """
    return "can't parse" in exc.message.lower()
