"""RequestIdMiddleware (outer на dp.update): ставит request_id в contextvar
httpclient (исходящие вызовы бэкенда понесут X-Request-ID=tg-<update_id>) и
bind'ит structlog-contextvars (update_id/tg_user_id/chat_id попадут в каждый лог
апдейта). Фоновые dialog-события (update_id=0) получают синтетический id.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from pylibinfra.httpclient import request_id as _httpclient_request_id

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class RequestIdMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update_id = int(getattr(event, "update_id", 0) or 0)
        rid = f"tg-{update_id}" if update_id else f"tg-bg-{id(event)}"
        token = _httpclient_request_id.set(rid)

        bound: dict[str, Any] = {"request_id": rid, "update_id": update_id}
        user = data.get("event_from_user")
        if user is not None:
            bound["tg_user_id"] = user.id
        chat = data.get("event_chat")
        if chat is not None:
            bound["chat_id"] = chat.id
        structlog.contextvars.bind_contextvars(**bound)
        try:
            return await handler(event, data)
        finally:
            structlog.contextvars.unbind_contextvars(*bound.keys())
            _httpclient_request_id.reset(token)
