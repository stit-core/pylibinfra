"""Core-порты кита: строка outbox'а, её адрес, результат неудачи и `Repository` поверх хранилища."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from pylibinfra.timerkit import Job

from .config import PublishMode


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Адрес строки: id плюс ключ партиционирования, чтобы UPDATE шёл по нужной партиции."""

    id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Message:
    """Строка outbox'а, вычитанная воркером на публикацию."""

    id: int
    created_at: datetime
    subject: str
    payload: bytes
    attempts: int = 0

    def ref(self) -> MessageRef:
        """Адрес строки для `mark_published`/`record_failures`."""
        return MessageRef(id=self.id, created_at=self.created_at)


@dataclass(frozen=True, slots=True)
class Failure:
    """Результат неудачной публикации: пауза до следующей попытки и признак `dead`."""

    ref: MessageRef
    err: str
    retry_after: float = 0.0
    dead: bool = False


@dataclass(frozen=True, slots=True)
class ReviveFilter:
    """Отбор dead-строк для `revive_dead`; заданные поля соединяются по И, пустой — все строки."""

    ids: Sequence[int] = field(default_factory=tuple)
    subject: str = ""
    since: datetime | None = None


@runtime_checkable
class Repository(Protocol):
    """Порт хранилища; `list_unpublished` идёт в транзакции тика и держит строки до её конца."""

    async def insert(self, subject: str, payload: bytes) -> None:
        """Пишет событие в outbox-таблицу; payload уже сериализован (`Bus`)."""

    async def list_unpublished(self, limit: int, mode: PublishMode) -> list[Message]:
        """Вычитывает и запирает неопубликованные строки; занятая голова `ordered` — `BusyError`."""

    async def mark_published(self, refs: Sequence[MessageRef]) -> None:
        """Помечает строки опубликованными."""

    async def record_failures(self, failures: Sequence[Failure]) -> None:
        """Инкрементит `attempts`, пишет `last_error`, откладывает ретрай и метит `dead`."""


@runtime_checkable
class DeadReviver(Protocol):
    """Опциональный интерфейс `Repository`: возврат dead-строк в очередь."""

    async def revive_dead(self, filter: ReviveFilter) -> int:
        """Возвращает dead-строки в очередь и отдаёт число задетых."""


@runtime_checkable
class Maintainer(Protocol):
    """Опциональный интерфейс `Repository`: периодические job'ы обслуживания хранилища."""

    def maintenance_jobs(self) -> list[Job]:
        """Job'ы партиций и retention для кортежа `timerkit.Jobs` консьюмера."""


RepositoryFactory = Callable[[str], Awaitable[Repository]]
"""Фабрика `Repository` на именованную таблицу — для multi-instance проводки."""
