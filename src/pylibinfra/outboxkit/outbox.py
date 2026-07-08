"""Core-абстракции transactional-outbox: Message, Failure + порт Repository.

Repository отделён от pgstore-реализации, чтобы доменный код зависел от порта, а
не от Postgres. `Store` сохранён как алиас Repository для обратной совместимости.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str
    payload: Any
    # attempts — сколько раз публикация уже проваливалась (для poison-логики).
    attempts: int = 0


@dataclass(frozen=True, slots=True)
class Failure:
    """Результат неудачной публикации события: dead=True помечает строку мёртвой
    (исчерпан max_attempts) — она больше не вычитывается на ретрай."""

    id: int
    err: str
    dead: bool


@runtime_checkable
class Repository(Protocol):
    async def insert(self, subject: str, payload: Any) -> None: ...
    async def list_unpublished(self, limit: int) -> list[Message]: ...
    async def mark_published(self, ids: list[int]) -> None: ...
    async def record_failures(self, failures: list[Failure]) -> None: ...


# Обратная совместимость: старое имя порта.
Store = Repository
