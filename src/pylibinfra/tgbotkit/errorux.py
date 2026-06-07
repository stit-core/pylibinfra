"""ErrorUX — кастомизируемый пользовательский error-UX. Дефолт прод-безопасен:
на UnknownIntent/UnknownState/OutdatedIntent (устаревшие кнопки после рестарта/
TTL) гасит «часики» callback'а, на прочем — нейтральное «что-то пошло не так» +
panic-метрика. Шаблон override'ит своим Provider'ом (тексты/брендинг/i18n,
reset в корневой диалог), не трогая регистрацию error-handlers.
"""

from __future__ import annotations

from typing import Protocol

from aiogram import Bot, Dispatcher
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent
from aiogram_dialog.api.exceptions import OutdatedIntent, UnknownIntent, UnknownState


class ErrorUX(Protocol):
    async def on_unknown_intent(self, event: ErrorEvent, bot: Bot) -> None: ...
    async def on_unknown_state(self, event: ErrorEvent, bot: Bot) -> None: ...
    async def on_unhandled(self, event: ErrorEvent, bot: Bot) -> None: ...


async def _stop_spinner(event: ErrorEvent, text: str) -> None:
    # Устаревшая inline-кнопка: ответить на callback, чтобы у пользователя
    # перестали крутиться «часики». Сообщение трогать небезопасно (могло быть
    # удалено) — только answer.
    cq = event.update.callback_query
    if cq is not None:
        await cq.answer(text, show_alert=False)


class DefaultErrorUX:
    async def on_unknown_intent(self, event: ErrorEvent, bot: Bot) -> None:
        await _stop_spinner(event, "Session expired, send /start")

    async def on_unknown_state(self, event: ErrorEvent, bot: Bot) -> None:
        await _stop_spinner(event, "Session expired, send /start")

    async def on_unhandled(self, event: ErrorEvent, bot: Bot) -> None:
        cq = event.update.callback_query
        if cq is not None:
            await cq.answer("Something went wrong", show_alert=False)
            return
        msg = event.update.message
        if msg is not None:
            await msg.answer("Something went wrong, please try again later.")


def default_error_ux() -> ErrorUX:
    return DefaultErrorUX()


def register_error_handlers(dp: Dispatcher, ux: ErrorUX) -> None:
    """Только «ожидаемые» dialog-ошибки (устаревшие кнопки) → reset-UX, return
    True (обработано). Прочие исключения сознательно НЕ ловим тут — пусть
    всплывают в recovery-middleware (panic-метрика + on_unhandled + swallow),
    чтобы observability успела пометить result=error."""

    async def on_stale(event: ErrorEvent, bot: Bot) -> bool:
        if isinstance(event.exception, UnknownState):
            await ux.on_unknown_state(event, bot)
        else:
            await ux.on_unknown_intent(event, bot)
        return True

    dp.errors.register(on_stale, ExceptionTypeFilter(UnknownIntent, OutdatedIntent, UnknownState))
