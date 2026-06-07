"""In-memory recorder-Bus: запоминает опубликованные события и отдаёт их для
ассертов. Удовлетворяет outboxkit.Publisher и natskit.Bus.publish (publish по
сигнатуре (subject, payload))."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    subject: str
    payload: Any


class Recorder:
    """Фейковый publisher/bus: вместо реальной отправки пишет Event в память.

    Не потокобезопасен — async-тесты исполняются в одном event-loop'е, гонок нет.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []

    async def publish(self, subject: str, payload: Any) -> None:
        self._events.append(Event(subject=subject, payload=payload))

    def all(self) -> list[Event]:
        return list(self._events)

    def published(self, subject: str) -> list[Any]:
        return [e.payload for e in self._events if e.subject == subject]

    def last(self, subject: str) -> Any:
        for e in reversed(self._events):
            if e.subject == subject:
                return e.payload
        return None

    def count(self) -> int:
        return len(self._events)

    def reset(self) -> None:
        self._events.clear()
