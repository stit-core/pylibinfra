"""Core-абстракции transactional-outbox: Message + порты Store/LagReporter.

Store отделён от pgstore-реализации, чтобы доменный код зависел от порта, а не
от Postgres. LagReporter — отдельный (опциональный) порт: store, умеющий считать
отставание, подключается к outbox_lag_seconds через register_lag_gauge.
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    subject: str
    payload: Any


@runtime_checkable
class Store(Protocol):
    async def insert(self, subject: str, payload: Any) -> None: ...
    async def list_unpublished(self, limit: int) -> list[Message]: ...
    async def mark_published(self, id: int) -> None: ...
    async def lag_by_subject(self) -> dict[str, float]: ...


# Узкий порт для outbox_lag_seconds: реализуют store'ы, умеющие считать возраст
# самого старого неопубликованного события по каждому subject'у (значения — сек).
@runtime_checkable
class LagReporter(Protocol):
    async def lag_by_subject(self) -> dict[str, float]: ...
