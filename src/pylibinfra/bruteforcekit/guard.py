"""Guard против brute-force на login-эндпойнты: считает неудачные попытки
per-subject (логин/email) и per-IP, при превышении порога возвращает
LockState. Core-абстракция (Guard + Store-port) отделена от pgstore-реализации.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from pylibinfra.clockkit import Clock


@dataclass(frozen=True, slots=True)
class LockState:
    until: datetime


@runtime_checkable
class Store(Protocol):
    # Возвращает сырой locked_until (без сравнения с now) — решение «истёк ли
    # lockout» принимает Guard через Clock, чтобы оно было тестируемым.
    async def read(self, subject: str, ip: str) -> datetime | None: ...

    async def record_failure(self, subject: str, ip: str) -> None: ...

    async def reset(self, subject: str, ip: str) -> None: ...

    async def cleanup(self) -> None: ...


class Guard:
    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def check(self, subject: str, ip: str) -> LockState | None:
        locked_until = await self._store.read(subject, ip)
        if locked_until is not None and locked_until > self._clock.now():
            return LockState(until=locked_until)
        return None

    async def record_failure(self, subject: str, ip: str) -> None:
        await self._store.record_failure(subject, ip)

    async def record_success(self, subject: str, ip: str) -> None:
        await self._store.reset(subject, ip)

    # Удаляет истёкшие attempt-строки (unlocked И старше Config.cleanup_retention).
    # Идемпотентна; вызывается периодической джобой.
    async def cleanup(self) -> None:
        await self._store.cleanup()
