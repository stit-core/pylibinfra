"""DB — фасад поверх Pool: каждый вызов идёт через conn(pool), которое берёт
текущее tx-соединение из contextvar или сам пул."""

from typing import Any

from .transactor import conn
from .types import Pool, Record


class DB:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def execute(self, sql: str, *args: Any) -> str:
        return await conn(self._pool).execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Record]:
        return await conn(self._pool).fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Record | None:
        return await conn(self._pool).fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await conn(self._pool).fetchval(sql, *args)
