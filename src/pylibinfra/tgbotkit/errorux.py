"""ErrorUX — что видит пользователь, когда апдейт не обработался."""

from __future__ import annotations

from typing import Protocol

from aiogram import Bot, Dispatcher
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState

STALE_TEXT = "Session expired, send /start"
FAILURE_TEXT = "Something went wrong, please try again later."


class ErrorUX(Protocol):
    """Порт пользовательского error-UX; консьюмер подменяет его своим Provider'ом."""

    async def on_unknown_intent(self, event: ErrorEvent, bot: Bot) -> None:
        """Кнопка ссылается на диалог, которого больше нет."""

    async def on_unknown_state(self, event: ErrorEvent, bot: Bot) -> None:
        """Состояние диалога не восстановилось (деплой снял `StatesGroup` или истёк TTL)."""

    async def on_unhandled(self, event: ErrorEvent, bot: Bot) -> None:
        """Любое другое необработанное исключение; текст обязан быть нейтральным."""


class DefaultErrorUX:
    """Прод-безопасный дефолт: гасит «часики» callback'а и не показывает внутренних текстов."""

    async def on_unknown_intent(self, event: ErrorEvent, bot: Bot) -> None:
        """Отвечает на callback подсказкой начать заново."""
        await _stop_spinner(event, STALE_TEXT)

    async def on_unknown_state(self, event: ErrorEvent, bot: Bot) -> None:
        """Отвечает на callback подсказкой начать заново."""
        await _stop_spinner(event, STALE_TEXT)

    async def on_unhandled(self, event: ErrorEvent, bot: Bot) -> None:
        """Нейтральный ответ в тот канал, из которого пришёл апдейт."""
        callback = event.update.callback_query
        if callback is not None:
            await callback.answer(FAILURE_TEXT, show_alert=False)
            return
        message = event.update.message
        if message is not None:
            await message.answer(FAILURE_TEXT)


def default_error_ux() -> ErrorUX:
    """`DefaultErrorUX` — дефолт кита, если консьюмер не дал своего."""
    return DefaultErrorUX()


def register_error_handlers(dp: Dispatcher, ux: ErrorUX) -> None:
    """Отдаёт `dp.errors` только исключения устаревшего диалога — остальные ловит recovery.

    Ловить в `dp.errors` всё означало бы пометить апдейт обработанным раньше, чем
    observability успеет записать `result=error`.
    """

    async def on_stale(event: ErrorEvent, bot: Bot) -> bool:
        if isinstance(event.exception, UnknownState):
            await ux.on_unknown_state(event, bot)
        else:
            await ux.on_unknown_intent(event, bot)
        return True

    dp.errors.register(on_stale, ExceptionTypeFilter(UnknownIntent, OutdatedIntent, UnknownState))


async def _stop_spinner(event: ErrorEvent, text: str) -> None:
    # Сообщение трогать небезопасно (могло быть удалено) — только ответ на callback.
    callback = event.update.callback_query
    if callback is not None:
        await callback.answer(text, show_alert=False)
