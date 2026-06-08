"""Delivery — ЕДИНСТВЕННАЯ безопасная обёртка над BgManager для доставки вне
апдейта (NATS→Telegram). Закрывает грабли фона (06 bgmanager): load=True (реальные
Chat/User для AUTO-show-mode), EDIT не гарантирован, изоляция request_id под каждую
доставку (фон вне middleware). Маппинг backend_user_id→(user_id,chat_id) —
продуктовый, остаётся в шаблоне.

Сбой доставки ПРОБРАСЫВАЕТСЯ к звонящему (notifier-handler) ради at-least-once:
исключение доходит до natskit.JsBus._handle_one → msg.nak() → redelivery (там же
лог сбоя на уровне консьюмера). Здесь — только установка/сброс request_id.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
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
        token = _httpclient_request_id.set(f"tg-notify-{chat_id}")
        try:
            await self._bot.send_message(chat_id, text, **kwargs)
        finally:
            _httpclient_request_id.reset(token)
