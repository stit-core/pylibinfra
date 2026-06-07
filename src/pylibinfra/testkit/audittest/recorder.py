"""In-memory Recorder для auditkit: запоминает Entry и даёт ассерт-хелперы."""

from __future__ import annotations

from pylibinfra.auditkit import Entry


class Recorder:
    """Фейковый auditkit.Recorder/Store: пишет Entry в память, отдаёт для ассертов.

    Не потокобезопасен — async-тесты исполняются в одном event-loop'е, гонок нет.
    """

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    async def record(self, entry: Entry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def actions(self) -> list[str]:
        return [e.action for e in self._entries]

    def has(self, action: str) -> bool:
        return any(e.action == action for e in self._entries)

    def last(self, action: str) -> Entry | None:
        for e in reversed(self._entries):
            if e.action == action:
                return e
        return None

    def by_resource(self, resource: str) -> list[Entry]:
        return [e for e in self._entries if e.resource == resource]

    def reset(self) -> None:
        self._entries.clear()
