"""Recorder — in-memory `outboxkit.Publisher`: копит опубликованные события для ассертов."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pylibinfra.outboxkit import Publisher, encode_payload


@dataclass(frozen=True, slots=True)
class Event:
    """Опубликованное событие: subject и payload как есть (по ссылке)."""

    subject: str
    payload: Any


class Recorder:
    """Фейк publisher'а: вместо вставки в outbox пишет `Event` в память.

    Payload проверяется на сериализуемость так же, как это делает боевой `Bus`
    (`OutboxError`), — тест не пропустит событие, которое в проде не доедет до БД.
    Не потокобезопасен: async-тесты идут в одном event-loop'е.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []

    async def publish(self, subject: str, payload: Any) -> None:
        """Записывает событие; несериализуемый payload — `outboxkit.OutboxError`."""
        encode_payload(payload)
        self._events.append(Event(subject=subject, payload=payload))

    def all(self) -> list[Event]:
        """Копия всех событий в порядке публикации."""
        return list(self._events)

    def published(self, subject: str) -> list[Any]:
        """Payload'ы событий с этим subject'ом в порядке публикации."""
        return [e.payload for e in self._events if e.subject == subject]

    def last(self, subject: str) -> Any:
        """Payload последнего события с этим subject'ом либо `None`."""
        for event in reversed(self._events):
            if event.subject == subject:
                return event.payload
        return None

    def count(self) -> int:
        """Число опубликованных событий."""
        return len(self._events)

    def reset(self) -> None:
        """Очищает накопленные события."""
        self._events.clear()


def new() -> Recorder:
    """Создаёт пустой Recorder."""
    return Recorder()


if TYPE_CHECKING:
    # Расхождение фейка с портом кита обязано падать на mypy, а не в тесте консьюмера.
    _publisher: Publisher = Recorder()
