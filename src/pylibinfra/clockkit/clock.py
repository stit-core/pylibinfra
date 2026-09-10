"""Порт часов и production-реализация Real."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Источник времени для кода, который иначе звал бы datetime.now и asyncio.sleep напрямую."""

    def now(self) -> datetime:
        """Текущий момент по этим часам (aware, UTC)."""

    def since(self, t: datetime) -> float:
        """Сколько секунд прошло от `t` по этим часам."""

    async def sleep(self, seconds: float) -> None:
        """Ожидание `seconds` по этим часам; фейки не спят реальное время."""


class Real:
    """Production-часы: datetime.now(UTC) и asyncio.sleep."""

    def now(self) -> datetime:
        """Текущий момент (aware, UTC)."""
        return datetime.now(UTC)

    def since(self, t: datetime) -> float:
        """Секунды, прошедшие от `t`."""
        return (datetime.now(UTC) - t).total_seconds()

    async def sleep(self, seconds: float) -> None:
        """asyncio.sleep."""
        await asyncio.sleep(seconds)
