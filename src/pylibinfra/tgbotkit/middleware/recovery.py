"""RecoveryMiddleware: последний рубеж — необработанное исключение не роняет бота."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import ErrorEvent, TelegramObject, Update
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState

from pylibinfra.logkit import Logger, nop
from pylibinfra.tgbotkit.errorux import ErrorUX
from pylibinfra.tgbotkit.metrics import Instruments

from .observability import update_type

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class RecoveryMiddleware(BaseMiddleware):
    """Гасит исключение обработчика: `tg_update_panics_total`, лог и нейтральный ответ.

    Исключения устаревшего диалога (`UnknownIntent`/`OutdatedIntent`/`UnknownState`)
    пробрасываются в `dp.errors`: там их чинит штатный stack-recovery `aiogram_dialog`,
    иначе пользователь с протухшей кнопкой залипал бы в петле generic-ответа до `/start`.
    """

    def __init__(self, ux: ErrorUX, instruments: Instruments, log: Logger | None = None) -> None:
        self._ux = ux
        self._instr = instruments
        self._log = log if log is not None else nop()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Отдаёт результат обработчика; на исключении — `None`, апдейт считается закрытым."""
        try:
            return await handler(event, data)
        except (UnknownIntent, OutdatedIntent, UnknownState):
            raise
        except Exception as exc:
            self._instr.panics.add(1, {"type": update_type(event)})
            self._log.error("tgbotkit: update handler panicked", err=repr(exc))
            bot = data.get("bot")
            if isinstance(event, Update) and bot is not None:
                await self._notify(ErrorEvent(update=event, exception=exc), bot)
            return None

    async def _notify(self, event: ErrorEvent, bot: Any) -> None:
        try:
            await self._ux.on_unhandled(event, bot)
        except Exception as exc:
            self._log.error("tgbotkit: error UX failed", err=repr(exc))
