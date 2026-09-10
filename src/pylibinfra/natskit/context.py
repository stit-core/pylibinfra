"""Subject и заголовки входящего сообщения внутри обработчика — через contextvars.

Handler получает только payload, поэтому метаданные сообщения доступны ему
`subject_from_context()`/`headers_from_context()`. Каждый обработчик исполняется в
своей asyncio-таске, а contextvar копируется per-task — значения изолированы.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token

_subject: ContextVar[str] = ContextVar("natskit_subject", default="")
_headers: ContextVar[Mapping[str, str] | None] = ContextVar("natskit_headers", default=None)

_Tokens = tuple[Token[str], Token[Mapping[str, str] | None]]


def subject_from_context() -> str:
    """Реальный subject обрабатываемого сообщения; пусто вне обработчика.

    Нужен wildcard-подписчику (`a.*.cmd.>`), чтобы роутить по конкретному субъекту,
    не заставляя publisher'а дублировать его в payload.
    """
    return _subject.get()


def headers_from_context() -> dict[str, str]:
    """Заголовки входящего сообщения (event-type, W3C trace-context); `{}` вне обработчика."""
    headers = _headers.get()
    return dict(headers) if headers else {}


@contextmanager
def message_context(subject: str, headers: Mapping[str, str] | None) -> Iterator[None]:
    """Кладёт subject и заголовки сообщения в контекст обработки (тем же пользуются фейки)."""
    tokens = bind_context(subject, headers)
    try:
        yield
    finally:
        unbind_context(tokens)


def bind_context(subject: str, headers: Mapping[str, str] | None) -> _Tokens:
    """Привязывает метаданные сообщения к текущему контексту; снимает `unbind_context`."""
    return _subject.set(subject), _headers.set(dict(headers) if headers else None)


def unbind_context(tokens: _Tokens) -> None:
    """Снимает привязку, сделанную `bind_context`."""
    _subject.reset(tokens[0])
    _headers.reset(tokens[1])
