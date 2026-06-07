"""Port для часов и production-реализация (Real).

Тестовые фейки Fixed и Tickable лежат рядом (fakes.py), чтобы не плодить
отдельный clocktest-пакет.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class Real:
    def now(self) -> datetime:
        return datetime.now(UTC)
