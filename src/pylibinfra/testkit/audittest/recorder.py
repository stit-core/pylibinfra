"""Recorder — фейковый `auditkit.Logger`: копит записи и отвечает на запросы теста."""

from __future__ import annotations

from dataclasses import replace

from pylibinfra.auditkit import Entry


class Recorder:
    """In-memory `auditkit.Logger`; невалидная Entry отвергается так же, как в pgstore.

    Не потокобезопасен — async-тесты исполняются в одном event-loop'е, гонок нет.
    """

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    async def log(self, entry: Entry) -> None:
        """Сохраняет копию записи; невалидная Entry — `auditkit.InvalidEntryError`."""
        entry.validate()
        self._entries.append(_clone(entry))

    def entries(self) -> list[Entry]:
        """Копии всех записей в порядке добавления."""
        return [_clone(e) for e in self._entries]

    def actions(self) -> list[str]:
        """Действия всех записей в порядке добавления."""
        return [e.action for e in self._entries]

    def has(self, action: str) -> bool:
        """Была ли запись с таким действием."""
        return any(e.action == action for e in self._entries)

    def last(self, action: str) -> Entry | None:
        """Копия последней записи с таким действием либо None."""
        for e in reversed(self._entries):
            if e.action == action:
                return _clone(e)
        return None

    def by_resource(self, resource: str) -> list[Entry]:
        """Копии записей по ресурсу в порядке добавления."""
        return [_clone(e) for e in self._entries if e.resource == resource]

    def reset(self) -> None:
        """Очищает накопленные записи."""
        self._entries.clear()


def _clone(entry: Entry) -> Entry:
    # Копия metadata: caller волен переиспользовать свою map после log,
    # pgstore к этому моменту уже смаршалил её в JSON.
    return replace(entry, metadata=dict(entry.metadata))
