"""RecoveryMiddleware (outer): последний рубеж. Ловит исключения, не обработанные
dp.errors, считает tg_update_panics_total, логирует и показывает пользователю
нейтральное сообщение через ErrorUX — и ГЛОТАЕТ: один битый апдейт не должен валить
бота.

Stale-dialog-исключения aiogram_dialog (UnknownIntent/OutdatedIntent/UnknownState)
ПРОБРАСЫВАЮТСЯ в dp.errors — там их чинит штатный stack-recovery aiogram_dialog
(IntentErrorMiddleware._fix_broken_stack) и stale-UX error-handler'ы шаблона; иначе
юзер с протухшей кнопкой после деплоя залип бы в петле generic-panic до ручного /start.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import ErrorEvent, TelegramObject, Update
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState

from pylibinfra.logkit import Logger
from pylibinfra.tgbotkit.errorux import ErrorUX
from pylibinfra.tgbotkit.metrics import TgInstruments

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class RecoveryMiddleware(BaseMiddleware):
    def __init__(self, ux: ErrorUX, instruments: TgInstruments, log: Logger) -> None:
        self._ux = ux
        self._instr = instruments
        self._log = log

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        try:
            return await handler(event, data)
        except (UnknownIntent, OutdatedIntent, UnknownState):
            raise
        except Exception as exc:
            update_type = str(getattr(event, "event_type", "unknown"))
            self._instr.panics_total.add(1, {"type": update_type})
            self._log.error("tgbotkit: recovered update panic", error=repr(exc))
            bot = data.get("bot")
            if isinstance(event, Update) and bot is not None:
                try:
                    await self._ux.on_unhandled(ErrorEvent(update=event, exception=exc), bot)
                except Exception as ux_exc:  # UX best-effort
                    self._log.error("tgbotkit: error-UX failed", error=repr(ux_exc))
            return None
