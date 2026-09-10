"""Request-id и ctx-поля апдейта: общий contextvar плюс поля `logkit` на время обработки."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from pylibinfra import logkit
from pylibinfra._internal import requestid

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_update_id: ContextVar[int] = ContextVar("tgbotkit_update_id", default=0)
_user_id: ContextVar[int] = ContextVar("tgbotkit_user_id", default=0)
_chat_id: ContextVar[int] = ContextVar("tgbotkit_chat_id", default=0)


def update_id() -> int:
    """`update_id` обрабатываемого апдейта; `0` — фоновое событие или вне обработки."""
    return _update_id.get()


def user_id() -> int:
    """Telegram-id пользователя апдейта; `0`, если апдейт не от пользователя."""
    return _user_id.get()


def chat_id() -> int:
    """Id чата апдейта; `0`, если апдейт вне чата."""
    return _chat_id.get()


def register_log_attrs() -> None:
    """Регистрирует `update_id`/`tg_user_id`/`chat_id` как ctx-поля `logkit` (идемпотентно)."""
    logkit.register_attr("update_id", lambda: _update_id.get() or None)
    logkit.register_attr("tg_user_id", lambda: _user_id.get() or None)
    logkit.register_attr("chat_id", lambda: _chat_id.get() or None)


def _ident(obj: object) -> int:
    value = getattr(obj, "id", 0)
    return int(value) if isinstance(value, int) else 0


class RequestIdMiddleware(BaseMiddleware):
    """Держит request-id и поля апдейта в контексте, пока апдейт обрабатывается.

    Валидный id из внешнего контекста (webhook-запрос уже прошёл через httpkit)
    сохраняется — цепочка «HTTP → апдейт → исходящий вызов» остаётся одной; иначе
    заводится свой UUID v7. Поля `update_id`/`tg_user_id`/`chat_id` `logkit` берёт
    из контекста сам, поэтому в каждой записи апдейта они есть без явной передачи.
    """

    def __init__(self) -> None:
        """Регистрирует ctx-поля апдейта в `logkit`; повторная сборка графа дублей не даёт."""
        register_log_attrs()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        """Обрабатывает апдейт с привязанными request-id и полями апдейта."""
        incoming = requestid.current()
        rid = requestid.bind(incoming if requestid.valid(incoming) else requestid.new())
        upd = _update_id.set(int(getattr(event, "update_id", 0) or 0))
        usr = _user_id.set(_ident(data.get("event_from_user")))
        cht = _chat_id.set(_ident(data.get("event_chat")))
        try:
            return await handler(event, data)
        finally:
            _chat_id.reset(cht)
            _user_id.reset(usr)
            _update_id.reset(upd)
            requestid.unbind(rid)
