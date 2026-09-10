"""Core-абстракции guard'а: `LockState`, request-path `Guard` и maintenance `Cleaner`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class LockState:
    """Активная блокировка пары `(subject, ip)`; `until` — по часам хранилища."""

    until: datetime
    failed_count: int = 0


@runtime_checkable
class Guard(Protocol):
    """Request-path guard'а: проверка блокировки и учёт исхода попытки."""

    async def check(self, subject: str, ip: str) -> LockState | None:
        """None, если пара не заблокирована, иначе активный `LockState`; счётчик не меняет."""

    async def record_failure(self, subject: str, ip: str) -> LockState | None:
        """Учитывает неудачу; `LockState`, если после неё блокировка активна, иначе None."""

    async def record_success(self, subject: str, ip: str) -> None:
        """Снимает историю попыток пары — успех обнуляет счётчик."""


@runtime_checkable
class Cleaner(Protocol):
    """Maintenance-путь guard'а: его реализуют хранилища, а не фейки вокруг `Guard`."""

    async def cleanup(self) -> None:
        """Удаляет разблокированные строки старше `Config.cleanup_retention`; идемпотентна."""
