"""In-memory реализации bruteforcekit.Store для тестов: Permissive (никогда не
блокирует), Locked (всегда заблокирован до Until) и Recorder (запоминает
failure/reset для ассертов). Все три удовлетворяют bruteforcekit.Store —
оборачиваются в Guard вместе с clockkit.Clock."""

from __future__ import annotations

from datetime import datetime


class Permissive:
    """Store, который никогда не блокирует: read всегда None."""

    async def read(self, subject: str, ip: str) -> datetime | None:
        return None

    async def record_failure(self, subject: str, ip: str) -> None: ...

    async def reset(self, subject: str, ip: str) -> None: ...

    async def cleanup(self) -> None: ...


class Locked:
    """Store, который всегда отдаёт locked_until=until — Guard трактует subject как
    заблокированный (если until > clock.now())."""

    def __init__(self, until: datetime) -> None:
        self._until = until

    async def read(self, subject: str, ip: str) -> datetime | None:
        return self._until

    async def record_failure(self, subject: str, ip: str) -> None: ...

    async def reset(self, subject: str, ip: str) -> None: ...

    async def cleanup(self) -> None: ...


class Recorder:
    """Store, запоминающий subject'ы record_failure/reset для ассертов; read не
    блокирует (None)."""

    def __init__(self) -> None:
        self._failures: list[str] = []
        self._resets: list[str] = []

    async def read(self, subject: str, ip: str) -> datetime | None:
        return None

    async def record_failure(self, subject: str, ip: str) -> None:
        self._failures.append(subject)

    async def reset(self, subject: str, ip: str) -> None:
        self._resets.append(subject)

    async def cleanup(self) -> None: ...

    def failures(self) -> list[str]:
        return list(self._failures)

    def resets(self) -> list[str]:
        return list(self._resets)
