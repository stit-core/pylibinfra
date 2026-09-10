"""DB — фасад запросов: каждый вызов идёт через `conn(pool)`, который берёт
tx-соединение из contextvar, если транзакция открыта, иначе сам пул."""

from __future__ import annotations

from typing import Any

from .transactor import conn
from .types import Pool, Record


class DB:
    """Фасад запросов поверх пула; внутри `Transactor.with_tx` работает в её транзакции."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        """Выполняет запрос без результата; возвращает command tag (`INSERT 0 1`)."""
        return await conn(self._pool).execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Record]:
        """Все строки результата."""
        return await conn(self._pool).fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Record | None:
        """Первая строка результата или None."""
        return await conn(self._pool).fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        """Первое значение первой строки или None."""
        return await conn(self._pool).fetchval(sql, *args)
