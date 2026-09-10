"""Request-id, общий для httpkit, httpclient, grpckit и tgbotkit.

Один contextvar на процесс: входящий транспорт кладёт id (`bind`), исходящий
клиент читает (`current`), logkit добавляет поле `request_id` в каждую запись.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from pylibinfra.idkit import new_v7

HEADER = "X-Request-ID"
MAX_LEN = 128

_request_id: ContextVar[str] = ContextVar("pylibinfra_request_id", default="")


def current() -> str:
    """Request-id текущего контекста; пустая строка вне запроса."""
    return _request_id.get()


def bind(value: str) -> Token[str]:
    """Кладёт request-id в контекст; токен возвращается в `unbind`."""
    return _request_id.set(value)


def unbind(token: Token[str]) -> None:
    """Восстанавливает контекст, каким он был до `bind`."""
    _request_id.reset(token)


def valid(value: str) -> bool:
    """Id от клиента принимается только печатным ASCII без пробелов длиной 1..MAX_LEN."""
    if not value or len(value) > MAX_LEN:
        return False
    return all(" " < ch <= "~" for ch in value)


def new() -> str:
    """Свежий request-id — UUID v7."""
    return new_v7()
