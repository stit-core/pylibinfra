"""Проброс subject и headers ВХОДЯЩЕГО сообщения в handler через contextvars
(паттерн как в pgkit с tx-соединением). Handler'у Subscribe/SubscribeStream не
передаётся ctx, поэтому метаданные сообщения доступны через
subject_from_context()/headers_from_context() внутри вызова.

Каждый handler исполняется в отдельной asyncio-таске, а contextvar копируется
per-task при создании таски — значения изолированы между конкурентными
handler'ами. Пусто/{} вне обработчика.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_subject: ContextVar[str] = ContextVar("natskit_subject", default="")
_headers: ContextVar[dict[str, str] | None] = ContextVar("natskit_headers", default=None)


def subject_from_context() -> str:
    """NATS-субъект обрабатываемого сообщения внутри handler'а. Пусто вне
    обработчика. Нужно wildcard-подписчику (`a.*.cmd.>`), чтобы роутить по
    конкретному субъекту, не заставляя publisher'а дублировать его в payload."""
    return _subject.get()


def headers_from_context() -> dict[str, str]:
    """Заголовки входящего сообщения внутри handler'а (event-type, schema-version,
    tenant и т.п.). {} вне обработчика. Содержит и W3C trace-context — читай
    нужные ключи."""
    h = _headers.get()
    return dict(h) if h else {}


def bind_context(
    subject: str, headers: dict[str, str] | None
) -> tuple[Token[str], Token[dict[str, str] | None]]:
    return _subject.set(subject), _headers.set(dict(headers) if headers else None)


def unbind_context(tokens: tuple[Token[str], Token[dict[str, str] | None]]) -> None:
    _subject.reset(tokens[0])
    _headers.reset(tokens[1])
