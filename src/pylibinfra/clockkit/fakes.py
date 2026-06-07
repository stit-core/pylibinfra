"""Детерминированные фейки Clock для тестов."""

from datetime import datetime, timedelta


class Fixed:
    """Часы, всегда возвращающие одно и то же время."""

    def __init__(self, t: datetime) -> None:
        self._t = t

    def now(self) -> datetime:
        return self._t


class Tickable:
    """Часы с ручным продвижением времени через advance()."""

    def __init__(self, start: datetime) -> None:
        self._t = start

    def now(self) -> datetime:
        return self._t

    def advance(self, delta: timedelta) -> None:
        self._t += delta
