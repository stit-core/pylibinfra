"""Capture — `logkit.Logger`, копящий записи в памяти вместо разбора stdout."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pylibinfra.logkit import Logger
from pylibinfra.logkit.config import parse_level


@dataclass(frozen=True)
class Entry:
    """Одна перехваченная запись: уровень, сообщение и поля (`with_`-поля включены).

    Ctx-поля боевого логгера (`trace_id`, `request_id`) сюда не попадают — их
    добавляет цепочка `logkit.new`, а не `Logger`.
    """

    level: str
    msg: str
    fields: dict[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> Any:
        """Значение поля `key`; `None`, если поля нет."""
        return self.fields.get(key)

    def str(self, key: str) -> str:
        """Значение поля `key` строкой; пустая строка, если поля нет."""
        value = self.fields.get(key)
        if value is None:
            return ""
        return value if isinstance(value, str) else f"{value}"


class Capture:
    """`logkit.Logger`, пишущий записи в память; `with_` делит хранилище с родителем."""

    def __init__(self, level: str = "debug") -> None:
        """Принимает записи от уровня `level`, ниже — отвечает `enabled() == False`."""
        self._min = parse_level(level)
        self._level = level
        self._store = _Store()
        self._fields: dict[str, Any] = {}

    def debug(self, msg: str, /, **kv: Any) -> None:
        """Записывает запись уровня `debug`."""
        self._add("debug", msg, kv)

    def info(self, msg: str, /, **kv: Any) -> None:
        """Записывает запись уровня `info`."""
        self._add("info", msg, kv)

    def warn(self, msg: str, /, **kv: Any) -> None:
        """Записывает запись уровня `warn`."""
        self._add("warn", msg, kv)

    def error(self, msg: str, /, **kv: Any) -> None:
        """Записывает запись уровня `error`."""
        self._add("error", msg, kv)

    def with_(self, **kv: Any) -> Logger:
        """Логгер с постоянными полями поверх того же хранилища."""
        child = Capture(self._level)
        child._store = self._store
        child._fields = {**self._fields, **kv}
        return child

    def enabled(self, level: str) -> bool:
        """Принимается ли запись уровня `level`."""
        return parse_level(level) >= self._min

    def entries(self) -> list[Entry]:
        """Копия перехваченных записей в порядке появления."""
        return self._store.snapshot()

    def find(self, msg: str, *, level: str | None = None) -> Entry | None:
        """Последняя запись с таким сообщением (и уровнем, если задан)."""
        return self.find_func(lambda e: e.msg == msg and (level is None or e.level == level))

    def find_func(self, pred: Callable[[Entry], bool]) -> Entry | None:
        """Последняя запись, удовлетворяющая предикату."""
        for entry in reversed(self._store.snapshot()):
            if pred(entry):
                return entry
        return None

    def has(self, msg: str, *, level: str | None = None) -> bool:
        """Есть ли запись с таким сообщением (и уровнем, если задан)."""
        return self.find(msg, level=level) is not None

    def reset(self) -> None:
        """Очищает хранилище, общее с логгерами из `with_`."""
        self._store.reset()

    def _add(self, level: str, msg: str, kv: Mapping[str, Any]) -> None:
        if parse_level(level) < self._min:
            return
        self._store.append(Entry(level=level, msg=msg, fields={**self._fields, **kv}))


class _Store:
    __slots__ = ("_entries", "_lock")

    def __init__(self) -> None:
        self._entries: list[Entry] = []
        self._lock = threading.Lock()

    def append(self, entry: Entry) -> None:
        with self._lock:
            self._entries.append(entry)

    def snapshot(self) -> list[Entry]:
        with self._lock:
            return list(self._entries)

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


def capture_at(level: str) -> Capture:
    """`Capture`, принимающий записи от уровня `level` (аналог `Capture(level)`)."""
    return Capture(level)
