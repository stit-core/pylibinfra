"""Outbox-обёртка Bus: publish пишет событие в outbox-Repository. Вызов в открытой
tx (через pgkit Transactor) делает запись атомарной с доменным state —
коммит/rollback решает судьбу и бизнес-данных, и outbox-строки разом."""

from typing import Any, Protocol

from .metrics import OutboxInstruments
from .outbox import Repository


class Publisher(Protocol):
    async def publish(self, subject: str, payload: Any) -> None: ...


class Bus:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo
        self._instr = OutboxInstruments()

    async def publish(self, subject: str, payload: Any) -> None:
        try:
            await self._repo.insert(subject, payload)
        except Exception:
            self._instr.inserted_total.add(1, {"subject": subject, "status": "error"})
            raise
        self._instr.inserted_total.add(1, {"subject": subject, "status": "ok"})


async def publish_json[T](bus: Bus, subject: str, msg: T) -> None:
    await bus.publish(subject, msg)
