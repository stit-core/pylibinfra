"""Контракт логирования сервисов: `Logger` и его заглушка `nop()`."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Logger(Protocol):
    """Единственный способ логирования в китах и сервисах; поля — kwargs вызова."""

    def debug(self, msg: str, /, **kv: Any) -> None:
        """Пишет запись уровня `debug`."""
        ...

    def info(self, msg: str, /, **kv: Any) -> None:
        """Пишет запись уровня `info`."""
        ...

    def warn(self, msg: str, /, **kv: Any) -> None:
        """Пишет запись уровня `warn`."""
        ...

    def error(self, msg: str, /, **kv: Any) -> None:
        """Пишет запись уровня `error`."""
        ...

    def with_(self, **kv: Any) -> Logger:
        """Логгер с постоянными полями поверх той же цепочки."""
        ...

    def enabled(self, level: str) -> bool:
        """Принимается ли запись уровня `level` — чтобы пропустить дорогую подготовку полей."""
        ...


def nop() -> Logger:
    """Заглушка `Logger`: ничего не пишет, `enabled` — всегда `False`."""
    return _Nop()


class _Nop:
    def debug(self, msg: str, /, **kv: Any) -> None: ...

    def info(self, msg: str, /, **kv: Any) -> None: ...

    def warn(self, msg: str, /, **kv: Any) -> None: ...

    def error(self, msg: str, /, **kv: Any) -> None: ...

    def with_(self, **kv: Any) -> Logger:
        return self

    def enabled(self, level: str) -> bool:
        return False
