"""Delivery — ЕДИНСТВЕННАЯ безопасная обёртка над BgManager для доставки вне
апдейта (NATS→Telegram). Закрывает грабли фона (06 bgmanager): load=True (реальные
Chat/User для AUTO-show-mode), пустой/закрытый стек → тихий промах (не падать),
EDIT не гарантирован, изоляция request_id под каждую доставку (фон вне middleware).
Маппинг backend_user_id→(user_id,chat_id) — продуктовый, остаётся в шаблоне.
"""

from __future__ import annotations

from typing import Any

from aiogram import Bot
from aiogram.fsm.state import State
from aiogram_dialog import BgManagerFactory, StartMode

from pylibinfra.httpclient import request_id as _httpclient_request_id
from pylibinfra.logkit import Logger, nop


class Delivery:
    def __init__(self, bg_factory: BgManagerFactory, bot: Bot, log: Logger | None = None) -> None:
        self._bg = bg_factory
        self._bot = bot
        self._log = log or nop()

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
        except Exception as exc:
            # at-least-once-доставка не должна валить consumer'а: лог + проглотить
            self._log.error("tgbotkit: delivery start_dialog failed", error=repr(exc))
        finally:
            _httpclient_request_id.reset(token)

    async def send_message(self, *, chat_id: int, text: str, **kwargs: Any) -> None:
        token = _httpclient_request_id.set(f"tg-notify-{chat_id}")
        try:
            await self._bot.send_message(chat_id, text, **kwargs)
        except Exception as exc:
            self._log.error("tgbotkit: delivery send_message failed", error=repr(exc))
        finally:
            _httpclient_request_id.reset(token)
